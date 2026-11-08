"""Show and episode metadata (description, links, artwork), fetched from the
upstream RSS feed when an episode page asks, and kept in memory for a day.
A request for an episode the cached copy lacks (a new release) refetches, at
most every 10 minutes per feed. Nothing is written to disk."""

import re
import threading
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

import requests

from skipcastify.utils.utils import safe_filename

TTL_S = 24 * 3600
MISS_REFRESH_S = 600
MAX_BYTES = 40 * 1024 * 1024
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()
_inflight: dict[str, threading.Lock] = {}  # one fetch per feed; concurrent requests wait for it

NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
      "content": "http://purl.org/rss/1.0/modules/content/"}


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


def _http(url) -> Optional[str]:
    url = (url or "").strip()
    return url if url.startswith(("http://", "https://")) else None


def _text(el, path) -> Optional[str]:
    v = el.findtext(path, namespaces=NS)
    return v.strip() if v and v.strip() else None


def _image(el) -> Optional[str]:
    img = el.find("itunes:image", NS)
    return _http(img.get("href")) if img is not None else _http(el.findtext("image/url"))


def _summarize(raw: bytes, slug: str) -> dict:
    """Keep only what an episode page shows, keyed the way episode files are named.
    ElementTree, not feedparser: 0.3s vs 12s on a 2,000-episode feed."""
    ch = ET.fromstring(raw).find("channel")
    if ch is None:
        raise ValueError("not an RSS feed")
    show = {
        "title": _text(ch, "title"),
        "website": _http(ch.findtext("link")),
        "author": _text(ch, "itunes:author") or _text(ch, "author"),
        "image": _image(ch),
        "description": notes_blocks(_text(ch, "description") or _text(ch, "itunes:summary") or ""),
    }
    episodes = {}
    for item in ch.iter("item"):
        title = _text(item, "title")
        if not title:
            continue
        notes = _text(item, "content:encoded") or _text(item, "description") or _text(item, "itunes:summary") or ""
        episodes.setdefault(safe_filename(title, slug)[: -len(".mp3")], {
            "title": title,
            "link": _http(item.findtext("link")),
            "episode": _text(item, "itunes:episode"),
            "season": _text(item, "itunes:season"),
            "image": _image(item),
            "notes": notes_blocks(notes),
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


def feed_summary(url: str, slug: str, max_age: float = TTL_S) -> dict:
    def cached():
        hit = _cache.get(url)
        return hit[1] if hit and time.monotonic() - hit[0] < max_age else None
    with _lock:
        if (hit := cached()) is not None:
            return hit
        flight = _inflight.setdefault(url, threading.Lock())
    with flight:  # a second request for the same feed waits here, then reads the cache
        with _lock:
            if (hit := cached()) is not None:
                return hit
        summary = _summarize(_fetch(url), slug)
        with _lock:
            _cache[url] = (time.monotonic(), summary)
    return summary


def episode_meta(data_dir, slug: Optional[str], name: str) -> dict:
    """{"show": ..., "episode": ... or None, "error": ... or None}."""
    url = upstream_url(data_dir, slug) if slug else None
    if not url:
        return {"show": None, "episode": None, "error": "no upstream feed known for this show"}
    try:
        summary = feed_summary(url, slug)
        if name not in summary["episodes"]:
            summary = feed_summary(url, slug, max_age=MISS_REFRESH_S)  # maybe released since we cached
    except Exception as e:  # network, HTTP or parse failure: the page still works without it
        return {"show": None, "episode": None, "error": f"couldn't load the show's feed ({type(e).__name__})"}
    ep = summary["episodes"].get(name)
    return {"show": summary["show"], "episode": ep,
            "error": None if ep else "this episode is no longer in the show's feed"}
