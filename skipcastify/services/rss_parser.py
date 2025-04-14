from dataclasses import dataclass
import feedparser
import csv
import os
import hashlib
import xml.etree.ElementTree as ET
from typing import List, Union


class RSSParser:
    def __init__(self):
        pass

    def parse_opml(self, opml_path) -> List[Union[str, str]]:
        """Parses an OPML file and returns a list of podcast URLs.

        Args:
            opml_path (str): The path to the OPML file.

        Returns:
            list: A list of tuples containing the podcast title and URL.
        """
        tree = ET.parse("overcast.opml")
        root = tree.getroot()
        valid_outlines = []
        for outline in root.findall(".//outline"):
            title = outline.attrib.get("title")
            url = outline.attrib.get("xmlUrl")
            if title and url:
                valid_outlines.append((title, url))
        return valid_outlines

    def _feed_has_updates(self, feed: feedparser.FeedParserDict):
        """Checks if the feed has been updated since the last time it was parsed.

        Args:
            feed (feedparser.FeedParserDict): The feed to check for updates.

        Returns:
            bool: True if the feed has been updated, False otherwise.
        """
        hash_df = pd.read_csv("rss_data/updates.csv", index_col="podcast")
        feed_hash = hashlib.md5(str(feed.entries).encode("utf8")).hexdigest()
        pod_name = feed.feed.title
        updated = (
            pod_name not in hash_df.index
            or feed_hash != hash_df.loc[pod_name, "last_hash"]
        )
        if updated:
            hash_df.loc[pod_name, "last_updated"] = feed.get("updated", "")
            hash_df.loc[pod_name, "last_hash"] = feed_hash
            hash_df.to_csv("rss_data/updates.csv")
        return updated

    def fetch_podcast_updates(self, url):
        """Fetches podcast data from the given URL and saves it to a CSV file.

        Args:
            url (str): The URL of the podcast feed.

        Returns:
            feedparser.FeedParserDict: The parsed feed data.
            bool: True if the feed has been updated, False otherwise.
        """
        fields = ["title", "link", "link_type", "published", "summary"]
        feed = feedparser.parse(url)
        updated = self._feed_has_updates(feed)
        if not updated:
            print("No updates found for ", feed.feed.title)
            return feed, updated
        print("Parsing RSS feed for: ", feed.feed.title)
        with open(
            os.path.join("rss_data", feed.feed.title.replace(":", "-") + ".csv"),
            mode="w",
            newline="",
            encoding="utf-8",
        ) as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fields)
            writer.writeheader()
            # Iterate through entries and write to the CSV file
            for entry in feed.entries:
                # print("Title: ", entry.title)
                for link in entry.links:
                    writer.writerow(
                        {
                            "title": entry.title,
                            "link": link.get("href"),
                            "link_type": link.get("type"),
                            "published": entry.published,
                            "summary": entry.summary,
                        }
                    )
        return feed, updated
