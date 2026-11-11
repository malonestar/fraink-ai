# Assets

## Wake word models (`models/`)

microWakeWord models (TFLite + JSON manifest), run on-device with
[pymicro-wakeword](https://github.com/OHF-Voice/pymicro-wakeword):

| Model | Phrase |
|---|---|
| `hey_frank_v5` | "Hey Frank" (default) |
| `hey_stackchan_v1` | "Hey StackChan" |
| `hey_m5_v3` | "Hey M5" |
| `hey_frank_v3`, `hey_m5_v1` | older versions, kept for comparison |

Switch between them on the device: **Settings → Phrase**. Drop your own
microWakeWord `.tflite` + `.json` pair in here and it shows up in the list.

## Sounds (`sounds/`)

- `chime.wav` — Pomodoro phase-change chime (generated, included).
- `wake_word_triggered.wav` — the wake chime. Not included; it's the Home
  Assistant Voice PE sound. Grab it and convert it to 16-bit WAV:

  ```bash
  wget https://github.com/esphome/home-assistant-voice-pe/raw/dev/sounds/wake_word_triggered.flac
  python3 -c "import soundfile as sf; d, r = sf.read('wake_word_triggered.flac', dtype='int16'); sf.write('assets/sounds/wake_word_triggered.wav', d, r, subtype='PCM_16')"
  ```

  Without it the app still works, it just doesn't chime on wake.
