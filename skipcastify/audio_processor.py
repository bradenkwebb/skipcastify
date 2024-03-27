from pydub import AudioSegment
from pydub.playback import play
import sys
from rss_parser import fetch_podcast_updates
import logging
import os
import time
l = logging.getLogger("pydub.converter")
l.setLevel(logging.WARN)
l.addHandler(logging.StreamHandler())

channel_url = "https://lexfridman.com/feed/podcast/"

aud = AudioSegment.from_mp3('temp.mp3')
# f_10_sec = aud[10 * 1000:20 * 1000]
# f_10_sec.export('short.wav', format='wav')
f_10_min = aud[:10 * 60 * 1000]

f_path = 'data/intro.wav'
f_10_min.export(f_path, format='wav')

import subprocess
# this at least runs in the background now, so I can do other things...
process = subprocess.Popen(['buzz', 'add', '--task', 'transcribe', '--srt', '--vtt', '--txt', f_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# Fix this!! Very hacky
while process.poll() is None:
    # Process is still running, you can do other tasks here if needed
    if os.path.exists(f_path):
        try:
            os.rename(f_path, f_path)
            print('Access on file "' + f_path +'" is available!')
        except OSError as e:
            print('Access-error on file "' + f_path + '"! \n' + str(e))
    time.sleep(1)  # Adjust the sleep duration as needed

print("Done!")