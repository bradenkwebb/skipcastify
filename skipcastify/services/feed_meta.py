"""Show and episode metadata (description, links, artwork), fetched from the
upstream RSS feed when an episode page asks, and kept in memory for an hour.
Nothing is written to disk."""

import re
import threading
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

import feedparser
import requests

from skipcastify.utils.utils import safe_filename

TTL_S = 3600
MAX_BYTES = 40 * 1024 * 1024
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def upstream_url(data_dir, slug: str) -> Optional[str]:
    """Our generated feed's <channel><link> is the subscription URL it was built from."""
    path = Path(data_dir) / "feeds" / f"{slug}.xml"
    try:
        link = ET.parse(path).getroot().find("channel").findtext("link")
    except (OSError, ET.ParseError, AttributeError):
        return None
    return link if link and link.startswith(("http://", "https://")) else None


class _Blocks(HTMLParser):
    """Show notes HTML -> paragraphs of text and link runs. No markup survives."""
    BREAKS = {"p", "br", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "tr", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[list[dict]] = [[]]
        self.href: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        if tag in self.BREAKS:
            self._break()
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self.href = href if href.startswith(("http://", "https://")) else None

    def handle_endtag(self, tag):
        if tag == "a":
            self.href = None
        elif tag in self.BREAKS:
            self._break()

    def handle_data(self, data):
        text = re.sub(r"\s+", " ", data)
        if not text.strip() and not self.blocks[-1]:
            return
        run = {"text": text}
        if self.href:
            run["href"] = self.href
        self.blocks[-1].append(run)

    def _break(self):
        if self.blocks[-1]:
            self.blocks.append([])


_URL_RE = re.compile(r"(https?://[^\s<>\"']+[^\s<>\"'.,;:!?)\]])")


def _linkify(run: dict) -> list[dict]:
    """Bare URLs in plain-text notes become link runs too."""
    if "href" in run:
        return [run]
    parts, out = _URL_RE.split(run["text"]), []
    for i, part in enumerate(parts):
        if part:
            out.append({"text": part, "href": part} if i % 2 else {"text": part})
    return out


def notes_blocks(html: str) -> list[list[dict]]:
    p = _Blocks()
    p.feed(html or "")
    p.close()
    blocks = []
    for b in p.blocks:
        runs = [r for run in b for r in _linkify(run)]
        if runs and "".join(r["text"] for r in runs).strip():
            runs[0]["text"] = runs[0]["text"].lstrip()
            runs[-1]["text"] = runs[-1]["text"].rstrip()
            blocks.append(runs)
    return blocks


def _image(obj) -> Optional[str]:
    img = obj.get("image")
    href = img.get("href") if isinstance(img, dict) else None
    return href if href and href.startswith(("http://", "https://")) else None


def _http(url) -> Optional[str]:
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None


def _summarize(raw: bytes, slug: str) -> dict:
    """Keep only what an episode page shows, keyed the way episode files are named."""
    parsed = feedparser.parse(raw)
    ch = parsed.feed
    show = {
        "title": ch.get("title"),
        "website": _http(ch.get("link")),
        "author": ch.get("author") or ch.get("itunes_author"),
        "image": _image(ch),
        "description": notes_blocks(ch.get("summary") or ch.get("subtitle") or ""),
    }
    episodes = {}
    for e in parsed.entries:
        title = e.get("title")
        if not title:
            continue
        content = (e.get("content") or [{}])[0].get("value") if e.get("content") else None
        episodes.setdefault(safe_filename(title, slug)[: -len(".mp3")], {
            "title": title,
            "link": _http(e.get("link")),
            "episode": e.get("itunes_episode"),
            "season": e.get("itunes_season"),
            "image": _image(e),
            "notes": notes_blocks(content or e.get("summary") or ""),
        })
    return {"show": show, "episodes": episodes}


def _fetch(url: str) -> bytes:
    with requests.get(url, timeout=15, stream=True, headers={"User-Agent": "Skipcastify/1.0"}) as r:
        r.raise_for_status()
        chunks, size = [], 0
        for chunk in r.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("feed too large")
            chunks.append(chunk)
    return b"".join(chunks)


def feed_summary(url: str, slug: str) -> dict:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < TTL_S:
            return hit[1]
    summary = _summarize(_fetch(url), slug)
    with _lock:
        _cache[url] = (now, summary)
    return summary


def episode_meta(data_dir, slug: Optional[str], name: str) -> dict:
    """{"show": ..., "episode": ... or None, "error": ... or None}."""
    url = upstream_url(data_dir, slug) if slug else None
    if not url:
        return {"show": None, "episode": None, "error": "no upstream feed known for this show"}
    try:
        summary = feed_summary(url, slug)
    except Exception as e:  # network, HTTP or parse failure: the page still works without it
        return {"show": None, "episode": None, "error": f"couldn't load the show's feed ({type(e).__name__})"}
    ep = summary["episodes"].get(name)
    return {"show": summary["show"], "episode": ep,
            "error": None if ep else "this episode is no longer in the show's feed"}
