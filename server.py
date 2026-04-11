"""
server.py — FastAPI entry point for the Linebackers Legal receptionist bot.

Endpoints
---------
POST /incoming   Twilio calls this when a call is received.
                 Returns TwiML that starts a bidirectional media stream.
WS   /ws         Twilio streams μ-law 8 kHz audio here.
                 Hands the WebSocket to bot.py's Pipecat pipeline.
GET  /health     Quick liveness check.

Startup
-------
If NGROK_AUTHTOKEN is set in .env, the server opens a public ngrok tunnel
and automatically updates the Twilio phone number's webhook URL so you can
call the AU number immediately without any manual config.

Usage
-----
    python server.py

Or with uvicorn directly:
    uvicorn server:app --port 8765 --log-level info
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SERVER_PORT = int(os.getenv("SERVER_PORT", "8765"))
NGROK_AUTHTOKEN = os.getenv("NGROK_AUTHTOKEN", "").strip()

# Mutable global — set by lifespan once ngrok/server URL is known
_public_host: str = ""


# ---------------------------------------------------------------------------
# Lifespan: optional ngrok tunnel + Twilio webhook auto-config
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _public_host

    if NGROK_AUTHTOKEN:
        try:
            from pyngrok import ngrok, conf
            conf.get_default().auth_token = NGROK_AUTHTOKEN
            tunnel = ngrok.connect(SERVER_PORT, "http")
            _public_host = tunnel.public_url.replace("http://", "").replace("https://", "")
            webhook_url = f"https://{_public_host}/incoming"
            logger.info("ngrok tunnel active: https://%s", _public_host)
            logger.info("Twilio webhook URL   : %s", webhook_url)

            # Auto-configure Twilio webhook
            try:
                from scripts.configure_twilio import set_twilio_webhook
                set_twilio_webhook(webhook_url)
                logger.info("Twilio webhook updated successfully.")
            except Exception as exc:
                logger.warning("Could not auto-update Twilio webhook: %s", exc)
                logger.warning(
                    "Run manually:  python scripts/configure_twilio.py --url %s",
                    webhook_url,
                )
        except ImportError:
            logger.warning("pyngrok not installed; skipping auto-tunnel.")
        except Exception as exc:
            logger.error("ngrok failed: %s", exc)
            logger.error("Set up the tunnel manually and update Twilio webhook.")
    else:
        logger.info(
            "NGROK_AUTHTOKEN not set. Start ngrok manually:\n"
            "  ngrok http %d\n"
            "Then run:  python scripts/configure_twilio.py --url <ngrok-url>",
            SERVER_PORT,
        )

    yield  # server runs here

    # Cleanup
    if NGROK_AUTHTOKEN:
        try:
            from pyngrok import ngrok
            ngrok.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="LinebackerXX Receptionist", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "public_host": _public_host or "not-set"}


@app.post("/incoming")
async def incoming_call(request: Request):
    """
    Twilio hits this endpoint when a call arrives.
    We respond with TwiML that opens a bidirectional media stream back to /ws.
    The host is taken from the request if ngrok isn't configured.
    """
    # Prefer the known public host (ngrok); fall back to the request's host
    host = _public_host or request.headers.get("host", f"localhost:{SERVER_PORT}")

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://{host}/ws" />
  </Connect>
</Response>"""

    logger.info("Incoming call — returning TwiML stream to wss://%s/ws", host)
    return PlainTextResponse(twiml, media_type="application/xml")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Twilio Media Stream WebSocket handler.

    Twilio sends three types of events:
      connected  — initial handshake (no audio yet)
      start      — stream metadata, gives us stream_sid
      media      — μ-law 8 kHz base64 audio chunks
      stop       — call ended

    We consume 'connected' and 'start' here to extract stream_sid, then
    hand the live WebSocket to the Pipecat pipeline which reads 'media' events.
    """
    await websocket.accept()
    logger.info("WebSocket connected")

    stream_sid: str = ""
    call_sid: str = ""
    transfer_flag: dict = {}

    # Read until we get the 'start' event
    try:
        async for raw in websocket.iter_text():
            data = json.loads(raw)
            event = data.get("event", "")

            if event == "connected":
                logger.debug("Twilio: connected event received")
                continue

            if event == "start":
                start = data.get("start", {})
                stream_sid = start.get("streamSid", "")
                call_sid = start.get("callSid", "")
                logger.info(
                    "Twilio stream started | stream_sid=%s call_sid=%s",
                    stream_sid,
                    call_sid,
                )
                break

            if event == "stop":
                logger.info("Twilio stream stopped before start (short call).")
                return

            logger.warning("Unexpected early Twilio event: %s", event)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected before stream start.")
        return
    except Exception as exc:
        logger.error("Error reading Twilio start event: %s", exc)
        return

    if not stream_sid:
        logger.error("No stream_sid received from Twilio; aborting.")
        return

    # Hand off to Pipecat pipeline
    try:
        from bot import run_bot
        await run_bot(websocket, stream_sid, transfer_flag)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected during call (stream_sid=%s).", stream_sid)
    except Exception as exc:
        logger.exception("Pipeline error for stream_sid=%s: %s", stream_sid, exc)

    # Post-call: handle transfer if requested
    if transfer_flag.get("requested"):
        dept = transfer_flag.get("department", "")
        number = transfer_flag.get("number", "")
        logger.info(
            "Transfer requested to %s (%s) — in a real deployment, "
            "use Twilio REST API to redirect call_sid=%s.",
            dept,
            number,
            call_sid,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=SERVER_PORT,
        log_level="info",
        # reload=True,  # uncomment for dev hot-reload (disables lifespan async cm)
    )
