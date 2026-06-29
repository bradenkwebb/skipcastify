#!/usr/bin/env python3
"""Regenerate RSS feeds without running the full pipeline.

Usage:
    uv run python scripts/generate_feeds.py
"""
from dotenv import load_dotenv
load_dotenv()

import os
import sys
import logging
from urllib.parse import urlparse
from skipcastify.services.generate_feeds import FeedManager
from skipcastify.utils.utils import load_subscriptions

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

data_dir = os.environ.get("DATA_DIR")
server_base_url = os.environ.get("SERVER_BASE_URL")
subscriptions_path = os.environ.get("SUBSCRIPTIONS")

if not data_dir:
    print("ERROR: DATA_DIR not set in .env")
    sys.exit(1)
if not server_base_url:
    print("ERROR: SERVER_BASE_URL not set in .env")
    sys.exit(1)
if not subscriptions_path:
    print("ERROR: SUBSCRIPTIONS not set in .env")
    sys.exit(1)

exclude_host = urlparse(server_base_url).hostname
urls = load_subscriptions(subscriptions_path, exclude_host)

episode_token = os.environ.get("EPISODE_TOKEN", "")
fm = FeedManager(server_base_url, data_dir, episode_token)
for url in urls:
    try:
        path = fm.generate_feed(url)
        print(f"Generated: {path}")
    except Exception as e:
        print(f"WARNING: Skipped {url}: {e}", file=sys.stderr)
