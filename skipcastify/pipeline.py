import logging
from dotenv import load_dotenv
# import pandas as pd
import xml.etree.ElementTree as ET
import requests
import yaml
# from services.audio_processor import AudioProcessor
from services.download_episode import EpisodeDownloader
from services.generate_feeds import FeedManager
from services.rss_parser import RSSParser
import sys
# from services.transcript_processor import TranscriptProcessor
import os

class Pipeline:
    def __init__(self, config_path: str) -> None:
        self.config_path = config_path
        self.feed_manager = FeedManager(os.getenv("SERVER_IP"), os.getenv("DATA_DIR"))
        self.downloader = EpisodeDownloader(config_path, os.getenv("DATA_DIR"))
        # self.processor = AudioProcessor()
    
    def run(self):
        self.downloader.download_latest()

        # TODO: Process audio (placeholder for now)

        with open(self.config_path) as f:
            config = yaml.safe_load(f)
            for subscription in config['subscriptions']:
                self.feed_manager.generate_feed(subscription)

# def pipeline():
#     rss_parser = RSSParser()
#     audio_processor = AudioProcessor()
#     transcript_processor = TranscriptProcessor()

#     for pod_title, url in rss_parser.parse_opml("overcast.opml"):
#         print("Updating feed: ", pod_title, " from ", url)
#         feed, updated = rss_parser.fetch_podcast_updates(url)

#         if pod_title == "Philosophize This!":
#             pod_data = pd.read_csv(f"rss_data/{pod_title}.csv")
#             pod_data["published"] = pd.to_datetime(pod_data["published"])
#             ep_title, link, publish_date = (
#                 pod_data[pod_data["link_type"] == "audio/mpeg"]
#                 .sort_values(by="published", ascending=False)
#                 .iloc[0][["title", "link", "published"]]
#             )
#             print(f"Retrieving {ep_title} from {link}")
#             resp = requests.get(link, allow_redirects=True)
#             print(
#                 f"{int(resp.headers['Content-Length']) / 1024**2:.2f} MB file downloaded"
#             )

#             # Check that content is an audio file
#             if "audio" not in resp.headers["Content-Type"]:
#                 sys.exit("Error: Content is not an audio file")
#             ep_path = f"data/{pod_title}/{ep_title}/"
#             audio_processor.save_http_bytes(resp.content, ep_path)
#             print(f"Audio duration: {audio_processor.audio_duration(pretty=True)}")
#             print("Transcribing audio...")
#             transcript = audio_processor.transcribe()
#             transcript_processor.set_transcript(transcript)
#             print("Done!")


def main():
    load_dotenv()
    pipeline = Pipeline(os.getenv("SUBSCRIPTIONS"))
    pipeline.run()

if __name__ == "__main__":
    # pipeline()
    main()
