#!/usr/bin/env python3
"""Regenerate RSS feeds without running the full pipeline.

Usage:
    uv run python scripts/generate_feeds.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

import os
import yaml
from skipcastify.services.generate_feeds import FeedManager
import logging

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

with open(subscriptions_path) as f:
    config = yaml.safe_load(f)

episode_token = os.environ.get("EPISODE_TOKEN", "")
fm = FeedManager(server_base_url, data_dir, episode_token=episode_token)
for url in config['subscriptions']:
    path = fm.generate_feed(url)
    print(f"Generated: {path}")
