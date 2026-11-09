"""The subscription list (the SUBSCRIPTIONS OPML) as edited from /settings,
plus a preview of a feed before subscribing and a podcast search."""

import ipaddress
import os
import socket
import statistics
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from skipcastify.services.feed_meta import _image, _text
from skipcastify.utils.utils import slugify

MAX_BYTES = 40 * 1024 * 1024
TIMEOUT_S = 15
WINDOW_DAYS = 14  # R2 retention, for the storage estimate
SEARCH_URL = "https://itunes.apple.com/search"


def opml_path() -> Path:
    return Path(os.environ["SUBSCRIPTIONS"]).resolve()


def upstream_path() -> Path:
    return Path(os.environ.get("UPSTREAM_OPML") or Path(__file__).resolve().parents[2] / "upstream.opml")


def read(path: Path) -> list[dict]:
    """[{title, url}] for each rss outline, in file order."""
    if not path.is_file():
        return []
    out = []
    for o in ET.parse(path).getroot().iter("outline"):
        url = (o.get("xmlUrl") or "").strip()
        if o.get("type") == "rss" and url:
            out.append({"title": o.get("title") or o.get("text") or url, "url": url})
    return out


def write(path: Path, subs: list[dict]) -> None:
    root = ET.Element("opml", version="1.0")
    ET.SubElement(ET.SubElement(root, "head"), "title").text = "skipcastify subscriptions"
    group = ET.SubElement(ET.SubElement(root, "body"), "outline", text="feeds", title="feeds")
    for s in subs:
        ET.SubElement(group, "outline", type="rss", text=s["title"], title=s["title"], xmlUrl=s["url"])
    ET.indent(root)
    tmp = path.with_name(path.name + ".tmp")
    ET.ElementTree(root).write(tmp, encoding="UTF-8", xml_declaration=True)
    tmp.replace(path)


def check_url(url: str) -> str:
    """The URL if it is http(s) to a public address; ValueError otherwise."""
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("enter an http(s) feed URL")
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(u.hostname, u.port or 443)}
    except socket.gaierror:
        raise ValueError(f"can't resolve {u.hostname}") from None
    if not all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs):
        raise ValueError("that host is not on the public internet")
    return u.geturl()


def fetch(url: str) -> bytes:
    """GET with every redirect hop re-checked, a timeout and a size cap."""
    for _ in range(5):
        url = check_url(url)
        with requests.get(url, timeout=TIMEOUT_S, stream=True, allow_redirects=False,
                          headers={"User-Agent": "Skipcastify/1.0"}) as r:
            if r.is_redirect:
                url = urljoin(url, r.headers.get("Location", ""))
                continue
            r.raise_for_status()
            chunks, size = [], 0
            for chunk in r.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("feed too large")
                chunks.append(chunk)
            return b"".join(chunks)
    raise ValueError("too many redirects")


def _duration_s(text: str | None) -> float | None:
    if not text:
        return None
    try:
        parts = [float(p) for p in text.strip().split(":")]
    except ValueError:
        return None
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def summarize(raw: bytes, now: float | None = None) -> dict:
    """What subscribing would mean: title, cadence and an R2 storage estimate."""
    ch = ET.fromstring(raw).find("channel")
    if ch is None:
        raise ValueError("not an RSS feed")
    now = now or time.time()
    dates, sizes = [], []
    items = ch.findall("item")
    for item in items:
        try:
            dates.append(parsedate_to_datetime(item.findtext("pubDate")).timestamp())
        except (TypeError, ValueError):
            pass
        enc = item.find("enclosure")
        length = int(enc.get("length") or 0) if enc is not None and (enc.get("length") or "").isdigit() else 0
        if length < 1_000_000:  # missing or bogus; estimate from duration at 128 kbps
            d = _duration_s(_text(item, "itunes:duration"))
            length = int(d * 16_000) if d else 0
        if length:
            sizes.append(length)
    recent = [d for d in dates if now - d <= 90 * 86400]
    per_window = len(recent) * WINDOW_DAYS / 90
    avg = statistics.median(sizes[:20]) if sizes else 0
    title = _text(ch, "title") or "Untitled feed"
    return {
        "title": title,
        "slug": slugify(title),
        "author": _text(ch, "itunes:author") or _text(ch, "author"),
        "image": _image(ch),
        "episodes": len(items),
        "latest": max(dates) if dates else None,
        "per_window": round(per_window, 1),
        "avg_bytes": int(avg),
        "est_bytes": int(per_window * avg),
    }


def search(term: str) -> list[dict]:
    r = requests.get(SEARCH_URL, params={"media": "podcast", "entity": "podcast", "limit": 10, "term": term},
                     timeout=10, headers={"User-Agent": "Skipcastify/1.0"})
    r.raise_for_status()
    return [{"title": x.get("collectionName"), "author": x.get("artistName"), "url": x.get("feedUrl")}
            for x in r.json().get("results", []) if x.get("feedUrl")]
