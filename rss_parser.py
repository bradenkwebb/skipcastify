import feedparser
import csv
import os
import hashlib
import pandas as pd

def _feed_has_updates(feed):
    hash_df = df = pd.read_csv('rss_data/updates.csv', index_col='podcast')
    feed_hash = hashlib.md5(str(feed.entries).encode('utf8')).hexdigest()
    pod_name = feed.feed.title
    updated = pod_name in hash_df.index and feed_hash == hash_df.loc[pod_name, 'last_hash']
    if not updated:
        hash_df.loc[pod_name, 'last_hash'] = feed_hash
        hash_df.to_csv('rss_data/updates.csv')
    return updated

def fetch_podcast_data(url):
    fields=['title', 'link', 'link_type', 'published', 'summary']
    feed = feedparser.parse(url)
    updated = _feed_has_updates(feed)
    if updated:
        print("No updates found for ", feed.feed.title)
        return feed
    print("Parsing RSS feed for: ", feed.feed.title)
    with open(os.path.join('rss_data', feed.feed.title + '.csv'), mode='w', newline='', encoding='utf-8') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        # Iterate through entries and write to the CSV file
        for entry in feed.entries:
            # print("Title: ", entry.title)
            for link in entry.links:
                writer.writerow({
                    'title': entry.title, 
                    'link': link.get('href'), 
                    'link_type': link.get('type'), 
                    'published': entry.published, 
                    'summary': entry.summary}
                )
            # writer.writerow(dict((field, entry.get(value, '')) for field, value in fields.items()))
    return feed

phil_this_url = "https://feeds.megaphone.fm/QCD6036500916"

fetch_podcast_data(phil_this_url)
print('Done!')
