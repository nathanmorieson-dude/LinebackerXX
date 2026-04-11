# LinebackerXX — AI Receptionist Prototype

Real-time voice receptionist powered by **Pipecat** + **faster-whisper** (STT) +
**CosyVoice2** (TTS) + **Twilio** (telephony), live-testable on an Australian phone
number.

## Architecture

```
Caller (AU number)
   │
   ▼  PSTN
Twilio ──POST /incoming──► FastAPI (server.py)
                              │  TwiML: <Connect><Stream url="wss://…/ws"/>
                              ▼
                         WebSocket /ws
                              │
                         Pipecat pipeline (bot.py)
                              ├─ TwilioFrameSerializer   μ-law ↔ PCM16
                              ├─ faster-whisper STT       local CPU
                              ├─ Claude claude-sonnet-4-6 LLM + tools
                              ├─ CosyVoice2 TTS           local HTTP
                              └─ TwilioFrameSerializer   PCM16 → μ-law
```

The public HTTPS/WSS URL is provided by **ngrok** (auto-started if you supply
`NGROK_AUTHTOKEN`).

---

## Demo Call Flows

| Caller says | Bot does |
|---|---|
| "I'd like to book an appointment" | Asks for name / date / time, calls `check_availability` + `book_appointment`, reads back a confirmation number |
| "What are your opening hours?" | Calls `get_faq(topic="hours")` and answers |
| "Can I speak to billing?" | Calls `transfer_call(department="accounts")`, announces transfer |

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | 3.11 recommended |
| Twilio account | AU number already provisioned |
| Anthropic API key | `claude-sonnet-4-6` access |
| CosyVoice2 HTTP server | Running locally on port 9880 (see below) |
| ngrok account (free) | For the auto-tunnel; or run ngrok manually |

---

## Quick Start

### 1. Clone and set up environment

```bash
git clone <repo-url>
cd LinebackerXX
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env and fill in every value
```

Key variables:

| Variable | Description |
|---|---|
| `TWILIO_ACCOUNT_SID` | From Twilio console |
| `TWILIO_AUTH_TOKEN` | From Twilio console |
| `TWILIO_PHONE_NUMBER` | Your AU number, e.g. `+61291234567` |
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `COSYVOICE2_BASE_URL` | Base URL of local CosyVoice2 server |
| `COSYVOICE2_SPEAKER_ID` | Voice/speaker name your server accepts |
| `NGROK_AUTHTOKEN` | ngrok auth token (enables auto-tunnel) |

### 3. Start the CosyVoice2 inference server

The prototype expects a REST endpoint:

```
POST {COSYVOICE2_BASE_URL}/tts
Content-Type: application/json
{"text": "Hello", "speaker": "<COSYVOICE2_SPEAKER_ID>", "language": "en", "speed": 1.0}
→ audio/wav (mono PCM16, any sample rate)
```

A minimal FastAPI wrapper for the CosyVoice2 model:

```python
# cosyvoice_server.py (run separately)
from fastapi import FastAPI
from fastapi.responses import Response
import io, wave, numpy as np

app = FastAPI()

@app.post("/tts")
async def tts(body: dict):
    from cosyvoice.cli.cosyvoice import CosyVoice2
    model = CosyVoice2("pretrained_models/CosyVoice2-0.5B")
    output = list(model.inference_sft(body["text"], body["speaker"]))[0]
    # output["tts_speech"] is a torch tensor at 22050 Hz
    audio = (output["tts_speech"].numpy() * 32767).astype("int16")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(22050)
        wf.writeframes(audio.tobytes())
    return Response(buf.getvalue(), media_type="audio/wav")
```

```bash
uvicorn cosyvoice_server:app --port 9880
```

Verify:
```bash
curl -X POST http://localhost:9880/tts \
     -H "Content-Type: application/json" \
     -d '{"text":"Hello, Linebackers Legal.", "speaker":"default"}' \
     --output test.wav && aplay test.wav
```

### 4. Start the receptionist server

```bash
python server.py
```

On startup you should see:
```
ngrok tunnel active: https://abc123.ngrok-free.app
Twilio webhook URL  : https://abc123.ngrok-free.app/incoming
Twilio webhook updated successfully.
```

### 5. Make a call

Dial your Australian Twilio number. Aria will greet you within ~2 seconds.

---

## Manual ngrok (no NGROK_AUTHTOKEN)

```bash
# Terminal 1
ngrok http 8765

# Terminal 2
python scripts/configure_twilio.py --url https://abc123.ngrok-free.app

# Terminal 3
python server.py
```

---

## Tuning

### Reduce latency
- Use `WHISPER_MODEL=tiny.en` (fastest, slightly less accurate)
- Run on a machine with a GPU and set `WHISPER_DEVICE=cuda`, `WHISPER_COMPUTE_TYPE=float16`
- Host CosyVoice2 on GPU

### Change voice
Set `COSYVOICE2_SPEAKER_ID` to any speaker name your CosyVoice2 model supports.

### Adjust VAD sensitivity
In `bot.py`, tweak `VADParams(stop_secs=0.6)` — higher value = longer pause before
the bot treats silence as end-of-turn.

### Extend the FAQ / persona
Edit `prompts/receptionist.py` — `SYSTEM_PROMPT`, `_FAQ_ANSWERS`, and `TOOLS`.

---

## Project Structure

```
LinebackerXX/
├── .env.example               Template environment variables
├── requirements.txt           Python dependencies
├── server.py                  FastAPI: /incoming TwiML + /ws WebSocket
├── bot.py                     Pipecat pipeline builder
├── services/
│   └── cosyvoice_tts.py       Custom Pipecat TTSService for CosyVoice2
├── prompts/
│   └── receptionist.py        System prompt, tool schemas, mock handlers
└── scripts/
    └── configure_twilio.py    CLI to update Twilio webhook URL
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Caller hears silence | CosyVoice2 server not reachable | Check `COSYVOICE2_BASE_URL`, confirm server is up |
| Transcription is garbled | Wrong Whisper model for accent | Try `WHISPER_MODEL=medium.en` |
| Webhook not updating | Bad Twilio creds or wrong number format | Use E.164 format for `TWILIO_PHONE_NUMBER` |
| `EndFrame` before greeting | ngrok / pipeline race | Wait for "ngrok tunnel active" log before calling |
| `ImportError: faster_whisper` | Missing install | `pip install faster-whisper` |
