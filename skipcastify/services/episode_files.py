"""Which local episode file belongs to which upstream episode, keyed by guid.

Files are named from the title at download time, but publishers retitle and
renumber episodes afterwards. The guid stays put, so lookups go through it and
fall back to the title only for episodes never recorded here.

data/episode_files.json: {slug: {guid: filename}}. The first name recorded for
a guid wins, because that's the name the files on disk carry.
"""

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urljoin

from skipcastify.utils.utils import safe_filename

_cache: dict[str, tuple[float, dict]] = {}


def _path(data_dir) -> Path:
    return Path(data_dir) / "episode_files.json"


def load(data_dir) -> dict[str, dict[str, str]]:
    """The mapping, cached until the file changes. Missing or unreadable is empty."""
    p = _path(data_dir)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    hit = _cache.get(str(p))
    if not (hit and hit[0] == mtime):
        try:
            hit = (mtime, json.loads(p.read_text()))
        except (OSError, ValueError):
            return {}
        _cache[str(p)] = hit
    # A copy, so remember() can't change the cache before save() succeeds.
    return {slug: dict(files) for slug, files in hit[1].items()}


def save(data_dir, mapping: dict) -> None:
    p = _path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".episode_files.")
    with os.fdopen(fd, "w") as f:
        json.dump(mapping, f, indent=1, sort_keys=True)
    os.replace(tmp, p)


def filename(mapping: dict, slug: str, guid: str | None, title: str) -> str:
    """The episode's file name: the one recorded for its guid, else from its title."""
    return (guid and mapping.get(slug, {}).get(guid)) or safe_filename(title, slug)


def name(mapping: dict, slug: str, guid: str | None, title: str) -> str:
    """filename() without the .mp3: the episode name used everywhere else."""
    return filename(mapping, slug, guid, title)[: -len(".mp3")]


def item_guid(item, base_url: str | None = None) -> str | None:
    """An RSS <item>'s identity, by the same rule the downloader uses: guid, else enclosure URL.

    feedparser resolves a permalink guid against the feed URL; pass base_url to match that on upstream XML.
    """
    el = item.find("guid")
    guid = (el.text or "").strip() if el is not None else ""
    if guid:
        if base_url and el.get("isPermaLink", "true").lower() != "false":
            guid = urljoin(base_url, guid)
        return guid
    enclosure = item.find("enclosure")
    return enclosure.get("url") if enclosure is not None else None


def remember(mapping: dict, slug: str, guid: str | None, fname: str) -> bool:
    """Record fname for guid unless one is already recorded. True if it changed."""
    if not guid or guid in mapping.get(slug, {}):
        return False
    mapping.setdefault(slug, {})[guid] = fname
    return True
