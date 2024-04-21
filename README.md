A simple tool for removing ads from podcasts.

![A diagram of the podcast ad removal process](./pipeline.png)

## Installation
```bash
pip install -r requirements.txt
```

## Usage


## Notes
I originally planned to use Azure Cognitive Services to transcribe the podcast audio, at least for simplicity while testing. The free tier only allows 5 hours of audio per month, so I'd fly through that really quickly if I were to use this tool regularly.

However, it doesn't appear as though Azure actually provides a convenient way to *align* its speech-to-text outputs with audio. That seems like a major oversight—there are lots of non-neural forced alignment tools out there—but [Buzz](https://chidiwilliams.github.io/buzz/docs) actually provides a great solution, which calls on OpenAI's Whisper model (which was actually open-sourced). Since I can run that locally (albeit somewhat slowly) from a very straightforward CLI, I think I'll use that instead.

The main problem with Buzz at the moment is *closing* it from the CLI. It seems to hang indefinitely, and I have to kill the process manually. I'll have to look into that.

I don't think the Buzz source code is particularly inscrutable, though - if anything, it's quite accessible. I think
that if I look at it a bit more closely, I should be able to figure out how to use it in a more programmatic way.

Also, I think I should use a zer-shot classifier for ads. There are a couple on HuggingFace including one from 2022, `morenolq/spotify-podcast-advertising-classification`, which might be worth checking out.

### Overall process
- Every time a new podcast episode is released, download it
- Run Buzz on the episode to get the transcript
- Run the transcript through a simple ad detection algorithm # TODO: Implement this
- Use the ad detection results to remove the ads from the audio # TODO: Implement this
- Save the ad-free audio to a new file # TODO: Implement this
- Upload the ad-free audio to google drive # TODO: Implement this
- Update the podcast feed with the new audio file # TODO: Implement this

### Buzz
So far, I've
- Installed Buzz with the Windows Installer
- Cloned the Buzz git repo as a submodule
- Installed Buzz with PyPi in this virtual environment

I'm sure I only need one of those, but not sure how all of those installations are interacting. They seem to be using the same database? I'm not sure how that works or what I should uninstall.