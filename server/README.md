# Diarize + PDF service

Runs on your AI server (CPU only). The Meeting app sends it the recorded
audio to get "who spoke when", and the finished report HTML to get a PDF.

- `POST /diarize` — WAV in, speaker segments out (sherpa-onnx: pyannote
  segmentation-3.0 + NeMo TitaNet-large embeddings)
- `POST /pdf` — report HTML in, PDF out (WeasyPrint)
- `GET /health`

## Setup

```bash
sudo mkdir -p /opt/fraink-server && sudo chown $USER /opt/fraink-server
cp diarize_server.py /opt/fraink-server/ && cd /opt/fraink-server
python3 -m venv .venv
.venv/bin/pip install sherpa-onnx numpy weasyprint

mkdir -p models && cd models
wget https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2
tar xf sherpa-onnx-pyannote-segmentation-3-0.tar.bz2
wget https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_large.onnx
cd ..

# edit User= in the service file, then:
sudo cp fraink-diarize.service /etc/systemd/system/
sudo systemctl enable --now fraink-diarize
curl localhost:8895/health
```

Then point `ai_services.diarize_url` in the device's `config.yaml` at it.
