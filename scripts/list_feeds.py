#!/usr/bin/env python3
"""Print Overcast-ready subscription URLs for all skipcastify-hosted feeds."""
from dotenv import load_dotenv
load_dotenv()

import os
import sys
from urllib.parse import urlparse
from skipcastify.utils.utils import load_subscriptions, slugify
import xml.etree.ElementTree as ET

server_base_url = os.environ.get("SERVER_BASE_URL", "").rstrip("/")
feed_username = os.environ.get("FEED_USERNAME", "")
feed_password = os.environ.get("FEED_PASSWORD", "")
subscriptions_path = os.environ.get("SUBSCRIPTIONS", "overcast.opml")

if not server_base_url:
    print("ERROR: SERVER_BASE_URL not set in .env")
    sys.exit(1)

# Build a title map from the OPML so we don't need network calls
titles = {}
if subscriptions_path.lower().endswith(".opml"):
    tree = ET.parse(subscriptions_path)
    exclude_host = urlparse(server_base_url).hostname
    for outline in tree.getroot().iter("outline"):
        if outline.get("type") != "rss":
            continue
        url = outline.get("xmlUrl", "")
        if exclude_host and urlparse(url).hostname == exclude_host:
            continue
        title = outline.get("title") or outline.get("text", "")
        if title:
            titles[url] = title

parsed = urlparse(server_base_url)
auth_base = f"{parsed.scheme}://{feed_username}:{feed_password}@{parsed.netloc}"

data_dir = os.environ.get("DATA_DIR", "data")
feeds_dir = os.path.join(data_dir, "feeds")

print(f"\n{'Podcast':<55} {'Feed URL'}")
print("-" * 120)
for feed_url, title in titles.items():
    slug = slugify(title)
    xml_path = os.path.join(feeds_dir, f"{slug}.xml")
    exists = "✓" if os.path.exists(xml_path) else "✗"
    overcast_url = f"{auth_base}/feeds/{slug}.xml"
    print(f"[{exists}] {title:<52} {overcast_url}")

print(f"\n✓ = feed XML exists on disk   ✗ = not yet generated")
