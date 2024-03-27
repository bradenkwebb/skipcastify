import requests
import librosa
from pydub import AudioSegment
from pydub.playback import play
import sys
from rss_parser import fetch_podcast_updates
import logging
import azure.cognitiveservices.speech as speechsdk
import os
l = logging.getLogger("pydub.converter")
l.setLevel(logging.WARN)
l.addHandler(logging.StreamHandler())

speech_key, service_region = os.environ['SPEECH_KEY'], os.environ['SPEECH_REGION']
speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=service_region)

channel_url = "https://lexfridman.com/feed/podcast/"

# feed = fetch_podcast_updates(channel_url)
# url = feed.entries[0]['links'][1]['href']
# print(url)

# response = requests.get(url)
# print("Content is a", str(int(response.headers.get('Content-Length')) // 1024 // 1024), "MB", response.headers.get('Content-Type'), 'file')
# print("Last modified", response.headers['Last-Modified'])

# with open('temp.mp3', 'wb') as f:
#     f.write(response.content)
aud = AudioSegment.from_mp3('temp.mp3')
f_10_sec = aud[10 * 1000:20 * 1000]
f_10_sec.export('short.wav', format='wav')


# aud = AudioSegment.from_mp3('short.mp3')

# transcript = 'Business of fighting and Dana is truly the mastermind behind the UFC.'

import requests
import librosa
from pydub import AudioSegment
from pydub.playback import play
import sys
from skipcastify.rss_parser import fetch_podcast_updates
import logging
import azure.cognitiveservices.speech as speechsdk
import os

speech_key, service_region = os.environ['SPEECH_KEY'], os.environ['SPEECH_REGION']
speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=service_region)
audio_config = speechsdk.AudioConfig(filename='short.wav')
speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

result = speech_recognizer.recognize_once()

# Checks result.
if result.reason == speechsdk.ResultReason.RecognizedSpeech:
    print("Recognized: {}".format(result.text))
elif result.reason == speechsdk.ResultReason.NoMatch:
    print("No speech could be recognized: {}".format(result.no_match_details))
elif result.reason == speechsdk.ResultReason.Canceled:
    cancellation_details = result.cancellation_details
    print("Speech Recognition canceled: {}".format(cancellation_details.reason))
    if cancellation_details.reason == speechsdk.CancellationReason.Error:
        print("Error details: {}".format(cancellation_details.error_details))