import re
import xml.etree.ElementTree as ET
import yaml
from urllib.parse import urlparse


def load_subscriptions(path: str, exclude_host: str = None) -> list:
    """Load feed URLs from a YAML subscriptions file or an Overcast OPML export.

    When exclude_host is provided (the hostname of this server), feeds pointing
    at our own server are filtered out so we don't re-process our own output.
    """
    if path.lower().endswith('.opml'):
        tree = ET.parse(path)
        urls = []
        for outline in tree.getroot().iter('outline'):
            if outline.get('type') != 'rss':
                continue
            url = outline.get('xmlUrl', '').strip()
            if not url:
                continue
            if exclude_host and urlparse(url).hostname == exclude_host:
                continue
            urls.append(url)
        return urls
    else:
        with open(path) as f:
            return yaml.safe_load(f)['subscriptions']


def slugify(title: str) -> str:
    """
    Converts a given string into a URL-friendly slug.

    This function takes a string (typically a title) and transforms it into
    a lowercase, hyphen-separated string suitable for use in URLs. It removes
    any characters that are not alphanumeric and replaces sequences of non-alphanumeric
    characters with a single hyphen.
    Args:
        title: The input string to be slugified.
    Returns:
        A URL-friendly slug representation of the input string.
    """
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")

import unicodedata

def safe_filename(title: str, slug: str, max_len: int = 100) -> str:
    """Generates a safe filename for an audio file based on the title and slug."""
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", title).strip("_")
    max_base_len = max_len - len(slug) - len(".mp3") - 1  # 1 for the dash
    base = base[:max_base_len]
    return f"{slug}-{base}.mp3"