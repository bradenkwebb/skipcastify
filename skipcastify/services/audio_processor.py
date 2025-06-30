# from pydub import AudioSegment
# from pydub.playback import play
import sys
from skipcastify.services.rss_parser import RSSParser
import logging
import os
import time
import subprocess
import datetime

from skipcastify.services.state_manager import StateManager

logger = logging.getLogger(__name__)

# aud = AudioSegment.from_mp3('temp.mp3')
# # f_10_sec = aud[10 * 1000:20 * 1000]
# # f_10_sec.export('short.wav', format='wav')
# f_10_min = aud[:10 * 60 * 1000]

# f_path = 'data/intro.wav'
# f_10_min.export(f_path, format='wav')

# # this at least runs in the background now, so I can do other things...
# process = subprocess.Popen(
#     ['buzz', 'add', '--task', 'transcribe', '--srt', '--vtt', '--txt', f_path],
#     stdout=subprocess.PIPE,
#     stderr=subprocess.PIPE,

# )

# # Fix this!! Very hacky
# while process.poll() is None:
#     # Process is still running, you can do other tasks here if needed
#     if os.path.exists(f_path):
#         try:
#             os.rename(f_path, f_path)
#             print('Access on file "' + f_path +'" is available!')
#         except OSError as e:
#             print('Access-error on file "' + f_path + '"! \n' + str(e))
#     time.sleep(1)  # Adjust the sleep duration as needed

# print("Done!")


class AudioProcessor:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir

    def save_http_bytes(self, bytes, ep_path):
        self.episode_path = ep_path
        og_aud_path = os.path.join(ep_path, "original.mp3")
        if not os.path.exists(ep_path):
            logger.warn(f"Creating directory {ep_path}")
            os.makedirs(ep_path)
        with open(og_aud_path, "wb") as f:
            f.write(bytes)
        self.load_audio(og_aud_path)

    # def load_audio(self, audio_path):
    #     self.audio = AudioSegment.from_mp3(audio_path)

    def save_audio(self, audio_save_path, format="mp3"):
        self.audio.export(audio_save_path, format=format)

    def get_audio(self):
        return self.audio

    def audio_duration(self, pretty=False):
        if pretty:
            return str(datetime.timedelta(seconds=self.audio.duration_seconds))
        return self.audio.duration_seconds

    def transcribe(self):  # WORK TO BE DONE HERE
        f_10_min = self.audio[: 10 * 60 * 1000]
        f_path = os.path.join(self.episode_path, "f_10_min.wav")
        f_10_min.export(f_path, format="wav")
        process = subprocess.Popen(
            ["buzz", "add", "--task", "transcribe", "--srt", "--vtt", "--txt", f_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        while process.poll() is None:
            if os.path.exists(f_path):
                try:
                    os.rename(f_path, f_path)
                    print('Access on file "' + f_path + '" is available!')
                except OSError as e:
                    print('Access-error on file "' + f_path + '"! \n' + str(e))
            time.sleep(1)  # Adjust the sleep duration as needed
        print("Done!")
    
    def process(self, episode_file_path: str, state_manager: StateManager):
        pass
