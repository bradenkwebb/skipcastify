import requests
import librosa
from pydub import AudioSegment
from pydub.playback import play
import sys
from rss_parser import fetch_podcast_updates
import logging
l = logging.getLogger("pydub.converter")
l.setLevel(logging.DEBUG)
l.addHandler(logging.StreamHandler())

channel_url = "https://lexfridman.com/feed/podcast/"


feed = fetch_podcast_updates(channel_url)
url = feed.entries[0]['links'][1]['href']
print(url)

response = requests.get(url)
print("Content is a", str(int(response.headers.get('Content-Length')) // 1024 // 1024), "MB", response.headers.get('Content-Type'), 'file')
print("Last modified", response.headers['Last-Modified'])

with open('temp.mp3', 'wb') as f:
    f.write(response.content)
aud = AudioSegment.from_mp3('temp.mp3')

f_10_sec = aud[:19 * 1000]

play(f_10_sec)