import pandas as pd
from skipcastify.rss_parser import fetch_podcast_updates
import requests
import sys
phil_this_url = "https://feeds.megaphone.fm/QCD6036500916"

feed = fetch_podcast_updates(phil_this_url)
pod_data = pd.read_csv(f'rss_data/{feed.feed.title}.csv')
pod_data['published'] = pd.to_datetime(pod_data['published'])
title, link, publish_date = pod_data[pod_data['link_type'] == 'audio/mpeg'].sort_values(by='published', ascending=False).iloc[0][['title', 'link', 'published']]
print(f"Retrieving {title} from {link}")
resp = requests.get(link, allow_redirects=True)
print(f'{int(resp.headers['Content-Length']) / 1024**2:.2f} MB file downloaded') 

# Check that content is an audio file
if 'audio' not in resp.headers['Content-Type']:
    sys.exit('Error: Content is not an audio file')

      