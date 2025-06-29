import re

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