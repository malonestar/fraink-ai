# Puck

**The fully local pocket AI device you'll actually use.**

Puck is a pocket-sized voice assistant built on a Raspberry Pi Zero 2 W, a
PiSugar 3 battery and a PiSugar WhisPlay screen/mic/speaker board. Wake it
with **"Hey Frank"**.

It's not a toy, and it's not a thin wrapper around a cloud API. The wake
word runs on the device, and everything else (speech-to-text, the LLMs,
text-to-speech, speaker diarization) runs on **your own** home AI server,
reached over Tailscale — so it works the same at home or off a phone
hotspot, and nothing leaves your network.

## What it does

| App | What it's for |
|---|---|
| **Voice Companion** | Hands-free chat with your local LLM (via [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)), with an animated face |
| **Note Taker** | Voice memos → transcribed → pushed to your phone with [ntfy](https://ntfy.sh) (and optionally into a notes vault) |
| **Meeting** | Records a meeting, transcribes it in chunks, labels speakers, summarizes with a big model and emails you an HTML + PDF report |
| **Translator** | Speak in one language, hear it back in another (es/fr/de/it/pt ↔ en) |
| **Pomodoro** | Focus timer with chimes, optional Home Assistant scenes |
| **HA Controller** | Glanceable Home Assistant entities |
| **Wi-Fi Setup** | Opens a captive-portal hotspot so you can join a new network from your phone |
| **Tailscale** | Status and connect/disconnect |
| **Settings** | Listen mode, wake word on/off + phrase, screen recording, restart / reboot / power down |

Swappable wake words are included: "Hey Frank", "Hey StackChan" and
"Hey M5" (see [assets/README.md](assets/README.md)).

## Hardware

- Raspberry Pi Zero 2 W (with soldered header)
- [PiSugar 3](https://github.com/PiSugar/PiSugar/wiki/PiSugar-3-Series) battery (1200 mAh)
- [PiSugar WhisPlay](https://github.com/PiSugar/Whisplay) HAT — 1.69" LCD, mics, speaker, button, RGB LED
- microSD card (16 GB+)
- 3D-printed case: [pisugar3-whisplay-chatbot-fdm](https://www.printables.com/model/1624666-pisugar3-whisplay-chatbot-fdm) by Al Schramm (@AlSchramm_4491347 on Printables). Thanks, Al!

**Assembly tip:** the PiSugar 3 talks to the Pi through spring pogo pins
pressed against the underside of the header. If the battery isn't detected,
lightly scuff the solder pads under the header and make sure the board is
pressed down evenly before tightening the screws.

## Server side

The device is the front end. You'll need a box on your network (a GPU helps)
running:

| Service | Used by | Default port |
|---|---|---|
| [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) | Voice Companion, Note Taker, HA | 8000 |
| [whisper.cpp server](https://github.com/ggml-org/whisper.cpp/tree/master/examples/server) (optional, GPU) | speech-to-text | 8890 |
| [speaches](https://github.com/speaches-ai/speaches) | speech-to-text fallback + Piper TTS | 8001 |
| [Ollama](https://ollama.com) | fast model (translation) | 11434 |
| vLLM or any OpenAI-compatible API (or Ollama) | big model (meeting summaries) | 8000 |
| [`server/diarize_server.py`](server/) | speaker labels + PDF reports for Meeting | 8895 |
| [ntfy](https://ntfy.sh) | memo push notifications | — |

Models used in the video: Gemma 4 E4B for voice chat (via xiaozhi), Gemma 4
E2B for translation, Qwen 3.8 27B for meeting summaries, Whisper
large-v3-turbo / SenseVoice for speech-to-text, Piper for speech. Use
whatever fits your hardware; it's all configured in `config.yaml` and your
xiaozhi server config.

## Install

1. Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager. Set the
   hostname, user, Wi-Fi and enable SSH in the Imager settings.
2. Boot, SSH in, then:

   ```bash
   git clone https://github.com/malonestar/puck-ai ~/puck-ai
   cd ~/puck-ai
   ./setup.sh
   ```

   This installs the WhisPlay driver, the PiSugar power manager, Tailscale,
   a Python venv and the `puck` systemd service.
3. Edit `~/puck-ai/config.yaml` — your server addresses, ntfy token and
   email app password. (`config.yaml` is gitignored; the template is
   [`config.example.yaml`](config.example.yaml).)
4. `sudo tailscale up` and open the login link, then `sudo reboot`.

Logs: `journalctl -u puck -f`

## Using it

Two buttons: the WhisPlay button on top and the PiSugar button on the side.

| | Home screen | Inside an app |
|---|---|---|
| WhisPlay tap | next app | app action (talk, start/stop, next…) |
| WhisPlay long press | open app | back to home |
| PiSugar tap | previous app | back to home |

Any press wakes the screen from the screensaver. Say **"Hey Frank"** inside
the voice apps to talk hands-free. Reboot / power down live in Settings.

## Tools

- `tools/screencap_to_video.py` — the device can record its own screen
  (Settings → Screen Rec). Copy the session folder to your PC and turn it
  into an MP4 for picture-in-picture in your videos.
- `tools/meeting_dryrun.py` — run the full Meeting pipeline on a WAV file
  from your PC, no device needed.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
python -m pytest tests/ -q
```

The tests run on any machine; hardware is mocked.

## License

[MIT](LICENSE)
