# XTTS-v2 Speaker Reference Audio Files

XTTS-v2 uses voice cloning: it requires a short audio sample of a speaker
to reproduce their voice characteristics during synthesis.

## Required file structure

```
src/resource/xtts_speakers/
  uk/
    male.wav
    female.wav
  en/
    male.wav
    female.wav
  de/
    male.wav
    female.wav
  ru/
    male.wav
    female.wav
```

## File requirements

| Property | Requirement |
|---|---|
| Format | WAV (PCM, any bit depth) |
| Sample rate | 22050 Hz or higher (24000 Hz recommended) |
| Channels | Mono or stereo |
| Duration | 5–30 seconds of clear, continuous speech |
| Content | Single speaker, no background music or noise |
| Language | Must match the synthesis language |

## How to create reference files

### Option A: Record your own voice
Record 10–20 seconds of clear speech. Convert to WAV:
```bash
ffmpeg -i recording.mp3 -ar 24000 -ac 1 src/resource/xtts_speakers/en/male.wav
```

### Option B: Generate from Edge TTS
```bash
edge-tts --voice en-US-GuyNeural \
    --text "Hello, this is a reference sample for voice cloning." \
    --write-media /tmp/sample.mp3
ffmpeg -i /tmp/sample.mp3 -ar 24000 -ac 1 src/resource/xtts_speakers/en/male.wav
```

### Option C: Extract from existing audio
```bash
ffmpeg -i source.mp4 -ss 00:01:10 -t 15 -ar 24000 -ac 1 src/resource/xtts_speakers/de/female.wav
```

## Notes

- Better quality reference audio → better synthesis quality.
- The reference audio defines voice timbre; the model handles all languages.
- WAV files are NOT committed to Git (see .gitignore). Each user must prepare
  their own reference samples.
