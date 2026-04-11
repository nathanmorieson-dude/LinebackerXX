"""
bot.py — Pipecat pipeline for the Linebackers Legal receptionist.

Call run_bot(websocket, stream_sid) from server.py once the Twilio
WebSocket handshake (connected + start events) is complete.

Pipeline topology
-----------------
transport.input()
    └─ WhisperSTTService        (faster-whisper, local CPU)
        └─ context_aggregator.user()
            └─ AnthropicLLMService  (claude-sonnet-4-6 + tools)
                └─ CosyVoice2TTSService  (local HTTP → 8 kHz PCM16)
                    └─ transport.output()
                        └─ context_aggregator.assistant()

Tool results are injected back into the context by pipecat's LLM aggregator.
A transfer_call result with {"transfer": True} sets a flag that causes
server.py to redirect the Twilio call after the pipeline ends.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Callable

from dotenv import load_dotenv
from fastapi import WebSocket

from pipecat.frames.frames import (
    EndFrame,
    LLMMessagesFrame,
    Frame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.anthropic import AnthropicLLMService
from pipecat.services.whisper import WhisperSTTService, Model as WhisperModel
from pipecat.transports.network.fastapi_websocket import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.vad.silero import SileroVADAnalyzer
from pipecat.vad.vad_analyzer import VADParams

from prompts.receptionist import SYSTEM_PROMPT, TOOLS, TOOL_HANDLERS
from services.cosyvoice_tts import CosyVoice2TTSService

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
COSYVOICE2_BASE_URL = os.getenv("COSYVOICE2_BASE_URL", "http://localhost:9880")
COSYVOICE2_SPEAKER_ID = os.getenv("COSYVOICE2_SPEAKER_ID", "default")
COSYVOICE2_ENDPOINT = os.getenv("COSYVOICE2_ENDPOINT", "/tts")
COSYVOICE2_SPEED = float(os.getenv("COSYVOICE2_SPEED", "1.0"))
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base.en")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

# Map env string to pipecat WhisperModel enum value (falls back to BASE_EN)
_WHISPER_MODEL_MAP = {
    "tiny": WhisperModel.TINY,
    "tiny.en": WhisperModel.TINY_EN,
    "base": WhisperModel.BASE,
    "base.en": WhisperModel.BASE_EN,
    "small": WhisperModel.SMALL,
    "small.en": WhisperModel.SMALL_EN,
    "medium": WhisperModel.MEDIUM,
    "medium.en": WhisperModel.MEDIUM_EN,
    "large-v3": WhisperModel.LARGE_V3,
}

# ---------------------------------------------------------------------------
# Tool dispatcher wired into the Anthropic LLM service
# ---------------------------------------------------------------------------

def _register_tools(
    llm: AnthropicLLMService,
    transfer_flag: dict,
) -> None:
    """Register all receptionist tool handlers with the LLM service."""

    async def _dispatch(function_name, tool_call_id, args, llm_svc, context, result_callback):
        handler = TOOL_HANDLERS.get(function_name)
        if handler is None:
            await result_callback({"error": f"Unknown tool: {function_name}"})
            return

        logger.info("Tool call: %s(%s)", function_name, json.dumps(args))
        result = handler(args)
        logger.info("Tool result: %s", json.dumps(result))

        # If this is a transfer, note it so the server can act after the call
        if result.get("transfer"):
            transfer_flag["requested"] = True
            transfer_flag["department"] = result.get("department", "")
            transfer_flag["number"] = result.get("number", "")

        await result_callback(result)

    for name in TOOL_HANDLERS:
        llm.register_function(name, _dispatch)


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------

async def run_bot(
    websocket: WebSocket,
    stream_sid: str,
    transfer_flag: dict | None = None,
) -> None:
    """
    Build and run the Pipecat receptionist pipeline for one call.

    Args:
        websocket:     The FastAPI WebSocket already accepted by server.py.
        stream_sid:    Twilio stream SID (from the 'start' event).
        transfer_flag: Mutable dict; set to {"requested": True, ...} if the
                       bot decides to transfer the call. Inspected by server.py.
    """
    if transfer_flag is None:
        transfer_flag = {}

    # -- Transport (Twilio ↔ Pipecat) ----------------------------------------
    serializer = TwilioFrameSerializer(stream_sid)
    transport = FastAPIWebsocketTransport(
        websocket,
        FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.6)),
            vad_audio_passthrough=True,
            serializer=serializer,
        ),
    )

    # -- STT (faster-whisper, local) -----------------------------------------
    whisper_model = _WHISPER_MODEL_MAP.get(WHISPER_MODEL, WhisperModel.BASE_EN)
    stt = WhisperSTTService(
        model=whisper_model,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE,
        language="en",
    )

    # -- LLM (Claude via Anthropic API) --------------------------------------
    llm = AnthropicLLMService(
        api_key=ANTHROPIC_API_KEY,
        model="claude-sonnet-4-6",
        max_tokens=512,
    )
    _register_tools(llm, transfer_flag)

    # -- TTS (CosyVoice2, local HTTP) ----------------------------------------
    tts = CosyVoice2TTSService(
        base_url=COSYVOICE2_BASE_URL,
        speaker_id=COSYVOICE2_SPEAKER_ID,
        endpoint=COSYVOICE2_ENDPOINT,
        speed=COSYVOICE2_SPEED,
    )

    # -- LLM context (conversation history + tools) --------------------------
    # Pre-load a "call connected" user turn so the LLM immediately generates
    # the greeting when the pipeline starts.
    initial_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "[call connected — greet the caller now]"},
    ]
    context = OpenAILLMContext(messages=initial_messages, tools=TOOLS)
    context_aggregator = llm.create_context_aggregator(context)

    # -- Pipeline ------------------------------------------------------------
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True,
            report_only_initial_ttfb=True,
        ),
    )

    # Trigger the initial greeting by flushing the context frame
    await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnect(transport_obj, client):
        logger.info("Twilio client disconnected — ending pipeline.")
        await task.queue_frames([EndFrame()])

    runner = PipelineRunner()
    logger.info("Pipeline running for stream_sid=%s", stream_sid)
    await runner.run(task)
    logger.info("Pipeline finished for stream_sid=%s", stream_sid)
