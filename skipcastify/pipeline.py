import logging
import pandas as pd
import xml.etree.ElementTree as ET
from rss_parser import fetch_podcast_updates
import requests
import sys

# phil_this_url = "https://feeds.megaphone.fm/QCD6036500916"



# pod_data['published'] = pd.to_datetime(pod_data['published'])
# title, link, publish_date = pod_data[pod_data['link_type'] == 'audio/mpeg'].sort_values(by='published', ascending=False).iloc[0][['title', 'link', 'published']]
# print(f"Retrieving {title} from {link}")
# resp = requests.get(link, allow_redirects=True)
# print(f'{int(resp.headers['Content-Length']) / 1024**2:.2f} MB file downloaded') 

# # Check that content is an audio file
# if 'audio' not in resp.headers['Content-Type']:
#     sys.exit('Error: Content is not an audio file')

      

def pipeline():
    # Parse the OPML file
    tree = ET.parse('overcast.opml')
    root = tree.getroot()

    # Iterate through outline elements
    for outline in root.findall('.//outline'):
        title = outline.attrib.get('title')
        url = outline.attrib.get('xmlUrl')
        if title and url:
            print("Updating feed: ", title, " from ", url)
            feed = fetch_podcast_updates(url)
            # pod_data = pd.read_csv(f'rss_data/{feed.feed.title}.csv')

if __name__ == '__main__':
    pipeline()