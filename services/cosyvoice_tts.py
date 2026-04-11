"""
CosyVoice2TTSService — Pipecat TTSService backed by a local CosyVoice2 HTTP server.

Expected server contract (configure via .env):
  POST {COSYVOICE2_BASE_URL}{COSYVOICE2_ENDPOINT}
  Content-Type: application/json
  Body: {"text": "...", "speaker": "...", "language": "en", "speed": 1.0}
  Response: audio/wav  (mono PCM16, any sample rate)

Alternatively, some CosyVoice2 servers use query parameters:
  GET {COSYVOICE2_BASE_URL}/inference_sft?tts_text=...&spk_id=...
Set COSYVOICE2_ENDPOINT accordingly and update _call_server() if needed.

The service resamples the WAV output to 8 000 Hz PCM16 mono before yielding
audio frames, because Twilio's media streams use μ-law @ 8 kHz and pipecat's
TwilioFrameSerializer expects PCM16 at 8 kHz from the pipeline.
"""

from __future__ import annotations

import io
import logging
import wave
from typing import AsyncGenerator

import httpx
import numpy as np

from pipecat.frames.frames import (
    AudioRawFrame,
    ErrorFrame,
    Frame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.services.ai_services import TTSService

logger = logging.getLogger(__name__)

TWILIO_SAMPLE_RATE = 8_000  # Hz — must match TwilioFrameSerializer expectations
CHUNK_SAMPLES = 160          # 20 ms @ 8 kHz
CHUNK_BYTES = CHUNK_SAMPLES * 2  # 16-bit = 2 bytes/sample


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _wav_to_pcm16_mono(wav_bytes: bytes) -> tuple[bytes, int]:
    """
    Extract raw PCM-16 mono data and sample rate from WAV bytes.
    Converts stereo → mono by averaging channels if needed.
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if sampwidth != 2:
        raise ValueError(f"Expected 16-bit WAV, got {sampwidth * 8}-bit.")

    audio = np.frombuffer(raw, dtype=np.int16)

    if n_channels > 1:
        audio = audio.reshape(-1, n_channels).mean(axis=1).astype(np.int16)

    return audio.tobytes(), framerate


def _resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resampler for PCM16 mono. Good enough for voice."""
    if src_rate == dst_rate:
        return pcm
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    target_len = int(round(len(audio) * dst_rate / src_rate))
    if target_len == 0:
        return b""
    indices = np.linspace(0, len(audio) - 1, target_len)
    resampled = np.interp(indices, np.arange(len(audio)), audio)
    return resampled.astype(np.int16).tobytes()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class CosyVoice2TTSService(TTSService):
    """Pipecat TTSService that sends text to a local CosyVoice2 HTTP server."""

    def __init__(
        self,
        base_url: str = "http://localhost:9880",
        speaker_id: str = "default",
        endpoint: str = "/tts",
        speed: float = 1.0,
        language: str = "en",
        http_timeout: float = 30.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._base_url = base_url.rstrip("/")
        self._speaker_id = speaker_id
        self._endpoint = endpoint
        self._speed = speed
        self._language = language
        self._client = httpx.AsyncClient(timeout=http_timeout)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call_server(self, text: str) -> bytes:
        """POST to the CosyVoice2 server and return raw WAV bytes."""
        url = f"{self._base_url}{self._endpoint}"
        payload = {
            "text": text,
            "speaker": self._speaker_id,
            "language": self._language,
            "speed": self._speed,
        }
        logger.debug("CosyVoice2 POST %s | speaker=%s | text=%r", url, self._speaker_id, text)
        response = await self._client.post(
            url,
            json=payload,
            headers={"Accept": "audio/wav, application/octet-stream"},
        )
        response.raise_for_status()
        return response.content

    # ------------------------------------------------------------------
    # TTSService interface
    # ------------------------------------------------------------------

    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        """Generate TTS audio frames from text.

        Yields:
            TTSStartedFrame once.
            AudioRawFrame chunks of 20 ms PCM-16 @ 8 kHz.
            TTSStoppedFrame once.
            ErrorFrame on failure (instead of crashing the pipeline).
        """
        logger.info("CosyVoice2 TTS: synthesising %d chars", len(text))
        yield TTSStartedFrame()

        try:
            wav_bytes = await self._call_server(text)
            pcm, src_rate = _wav_to_pcm16_mono(wav_bytes)
            pcm_8k = _resample_pcm16(pcm, src_rate, TWILIO_SAMPLE_RATE)

            for offset in range(0, len(pcm_8k), CHUNK_BYTES):
                chunk = pcm_8k[offset : offset + CHUNK_BYTES]
                if chunk:
                    # Pad last chunk to a full 20 ms frame
                    if len(chunk) < CHUNK_BYTES:
                        chunk = chunk + b"\x00" * (CHUNK_BYTES - len(chunk))
                    yield AudioRawFrame(
                        audio=chunk,
                        sample_rate=TWILIO_SAMPLE_RATE,
                        num_channels=1,
                    )
        except httpx.HTTPStatusError as exc:
            logger.error(
                "CosyVoice2 server returned HTTP %d: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            yield ErrorFrame(error=f"CosyVoice2 HTTP error {exc.response.status_code}")
        except httpx.RequestError as exc:
            logger.error("CosyVoice2 connection error: %s", exc)
            yield ErrorFrame(error=f"CosyVoice2 unreachable: {exc}")
        except Exception as exc:
            logger.exception("Unexpected CosyVoice2 TTS error")
            yield ErrorFrame(error=str(exc))
        finally:
            yield TTSStoppedFrame()

    async def cleanup(self) -> None:
        await self._client.aclose()
        await super().cleanup()
