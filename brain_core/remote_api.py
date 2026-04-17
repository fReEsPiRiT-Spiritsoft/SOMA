"""
SOMA Remote API — Authenticated remote access for mobile clients.
==================================================================
Provides API-key authenticated endpoints for text chat, streaming,
TTS audio synthesis (returned as audio, NOT played on speakers),
and apartment announcements.

This module is ADDITIVE — it does NOT modify any existing systems.
All existing endpoints, voice pipeline, and local behavior remain unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import secrets
import time
import uuid
import wave
from pathlib import Path
from typing import AsyncGenerator, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field

from brain_core.config import settings

logger = structlog.get_logger("soma.remote_api")

# ── API Key Management ───────────────────────────────────────────────────

# Keys stored as SHA-256 hashes in memory. Generated via /api/v1/remote/keys endpoint.
_api_keys: dict[str, dict] = {}  # hash → {"name": str, "created": float, "last_used": float}
_master_key_hash: str = ""       # Set from config on startup


def init_remote_auth():
    """Initialize remote auth from config. Called during app startup."""
    global _master_key_hash
    key = settings.remote_api_key
    if key:
        _master_key_hash = hashlib.sha256(key.encode()).hexdigest()
        _api_keys[_master_key_hash] = {
            "name": "master",
            "created": time.time(),
            "last_used": 0.0,
        }
        logger.info("remote_api_master_key_loaded")
    else:
        logger.warning("remote_api_no_key", hint="Set REMOTE_API_KEY in .env to enable remote access")


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def verify_api_key(key: str) -> bool:
    """Verify an API key. Returns True if valid."""
    h = _hash_key(key)
    if h in _api_keys:
        _api_keys[h]["last_used"] = time.time()
        return True
    return False


async def require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """FastAPI dependency: Require valid API key in X-API-Key header."""
    if not verify_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


# ── Request / Response Models ────────────────────────────────────────────

class RemoteChatRequest(BaseModel):
    """Chat request from mobile client."""
    message: str
    session_id: Optional[str] = None
    user_id: str = "remote_user"


class RemoteChatResponse(BaseModel):
    """Chat response to mobile client."""
    response: str
    session_id: str
    engine_used: str = "unknown"
    latency_ms: float = 0.0


class RemoteTTSRequest(BaseModel):
    """Request TTS audio synthesis (returned as WAV, not played on speakers)."""
    text: str


class AnnounceRequest(BaseModel):
    """Request to make an announcement on apartment speakers."""
    message: str


class KeyCreateRequest(BaseModel):
    """Create a new API key."""
    name: str = "mobile"


class KeyCreateResponse(BaseModel):
    """Response with the newly created API key (shown only once)."""
    api_key: str
    name: str
    hint: str = "Save this key — it cannot be retrieved later."


# ── Router ───────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1/remote", tags=["remote"])


@router.get("/ping")
async def remote_ping(api_key: str = Depends(require_api_key)):
    """Health check for remote clients."""
    return {"status": "ok", "timestamp": time.time()}


@router.post("/chat", response_model=RemoteChatResponse)
async def remote_chat(
    req: RemoteChatRequest,
    api_key: str = Depends(require_api_key),
):
    """
    Send a text message to SOMA and get a complete response.
    This is INSTANCED — audio is NOT played on apartment speakers.
    """
    from brain_core.logic_router import SomaRequest

    # Lazy import to avoid circular deps
    from brain_core.main import logic_router, broadcast_thought

    if not logic_router:
        raise HTTPException(status_code=503, detail="SOMA is still booting")

    session_id = req.session_id or str(uuid.uuid4())

    await broadcast_thought("info", f"📱 REMOTE: '{req.message[:60]}'", "REMOTE")

    soma_req = SomaRequest(
        prompt=req.message,
        user_id=req.user_id,
        session_id=session_id,
        metadata={"source": "remote_api", "instanced": True},
    )

    start = time.monotonic()
    response = await logic_router.route(soma_req)
    latency = (time.monotonic() - start) * 1000

    return RemoteChatResponse(
        response=response.response,
        session_id=session_id,
        engine_used=response.engine_used,
        latency_ms=round(latency, 1),
    )


@router.post("/chat/stream")
async def remote_chat_stream(
    req: RemoteChatRequest,
    api_key: str = Depends(require_api_key),
):
    """
    Stream SOMA's response token-by-token via SSE (Server-Sent Events).
    INSTANCED — no speaker output. The client renders text + optionally does browser TTS.
    """
    from brain_core.logic_router import SomaRequest
    from brain_core.main import logic_router, broadcast_thought

    if not logic_router:
        raise HTTPException(status_code=503, detail="SOMA is still booting")

    session_id = req.session_id or str(uuid.uuid4())

    await broadcast_thought("info", f"📱 REMOTE STREAM: '{req.message[:60]}'", "REMOTE")

    soma_req = SomaRequest(
        prompt=req.message,
        user_id=req.user_id,
        session_id=session_id,
        metadata={"source": "remote_api", "instanced": True},
    )

    async def event_stream():
        start = time.monotonic()
        full_response = []
        try:
            async for chunk in logic_router.route_stream(soma_req):
                full_response.append(chunk.text)
                data = json.dumps({
                    "text": chunk.text,
                    "is_final": chunk.is_final,
                    "engine_used": chunk.engine_used,
                    "latency_ms": round((time.monotonic() - start) * 1000, 1),
                })
                yield f"data: {data}\n\n"
        except Exception as e:
            logger.error("remote_stream_error", error=str(e))
            yield f"data: {json.dumps({'error': str(e), 'is_final': True})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/tts")
async def remote_tts(
    req: RemoteTTSRequest,
    api_key: str = Depends(require_api_key),
):
    """
    Synthesize text to speech and return WAV audio.
    Audio is NOT played on apartment speakers — returned to client only.
    """
    from brain_core.main import voice_pipeline

    if not voice_pipeline or not voice_pipeline.tts:
        raise HTTPException(status_code=503, detail="TTS not available")

    tts = voice_pipeline.tts

    # Synthesize to in-memory WAV (reusing Piper, no aplay)
    try:
        audio_bytes = await _synthesize_to_bytes(tts, req.text)
    except Exception as e:
        logger.error("remote_tts_error", error=str(e))
        raise HTTPException(status_code=500, detail="TTS synthesis failed")

    return Response(
        content=audio_bytes,
        media_type="audio/wav",
        headers={"Content-Disposition": "inline; filename=soma_tts.wav"},
    )


async def _synthesize_to_bytes(tts, text: str) -> bytes:
    """Synthesize text to WAV bytes using Piper (without playing on speakers)."""
    import concurrent.futures
    import numpy as np

    if not tts._piper:
        # Fallback: espeak to temp file
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = Path(f.name)
        await tts._espeak_to_file(text, tmp_path)
        audio = tmp_path.read_bytes()
        tmp_path.unlink(missing_ok=True)
        return audio

    from piper.config import SynthesisConfig

    syn_config = SynthesisConfig(
        length_scale=1.0,
        noise_scale=0.667,
        noise_w_scale=0.8,
        volume=1.0,
    )

    piper_instance = tts._piper
    loop = asyncio.get_event_loop()

    def _synthesize():
        chunks = []
        for chunk in piper_instance.synthesize(text, syn_config=syn_config):
            int16 = (chunk.audio_float_array * 32767).astype(np.int16)
            chunks.append(int16)
        return chunks

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        chunks = await loop.run_in_executor(ex, _synthesize)

    if not chunks:
        return b""

    all_audio = np.concatenate(chunks)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(piper_instance.config.sample_rate)
        wav.writeframes(all_audio.tobytes())

    return buf.getvalue()


@router.post("/announce")
async def remote_announce(
    req: AnnounceRequest,
    api_key: str = Depends(require_api_key),
):
    """
    Make SOMA speak an announcement through the apartment speakers.
    'Sag in der Wohnung: ...' — plays via local TTS on home speakers.
    """
    from brain_core.main import voice_pipeline, broadcast_thought

    if not voice_pipeline:
        raise HTTPException(status_code=503, detail="Voice pipeline not available")

    await broadcast_thought(
        "info",
        f"📢 REMOTE ANNOUNCE: '{req.message[:80]}'",
        "REMOTE",
    )

    # Speak through apartment speakers (normal TTS pipeline → aplay)
    await voice_pipeline.autonomous_speak(req.message)

    return {"status": "announced", "message": req.message}


@router.post("/keys", response_model=KeyCreateResponse)
async def create_api_key(
    req: KeyCreateRequest,
    api_key: str = Depends(require_api_key),
):
    """Create a new API key. Requires existing auth (master key)."""
    new_key = f"soma_{secrets.token_urlsafe(32)}"
    h = _hash_key(new_key)
    _api_keys[h] = {
        "name": req.name,
        "created": time.time(),
        "last_used": 0.0,
    }
    logger.info("remote_api_key_created", name=req.name)
    return KeyCreateResponse(api_key=new_key, name=req.name)


@router.get("/keys")
async def list_api_keys(api_key: str = Depends(require_api_key)):
    """List all API keys (names only, not the actual keys)."""
    return [
        {"name": v["name"], "created": v["created"], "last_used": v["last_used"]}
        for v in _api_keys.values()
    ]


# ── WebSocket for real-time remote chat ──────────────────────────────────

_remote_ws_connections: set[WebSocket] = set()


@router.websocket("/ws")
async def remote_websocket(ws: WebSocket):
    """
    WebSocket for real-time remote communication.
    First message must be: {"type": "auth", "api_key": "..."}
    Then: {"type": "chat", "message": "...", "session_id": "..."}
    """
    await ws.accept()

    # Auth handshake
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        msg = json.loads(raw)
        if msg.get("type") != "auth" or not verify_api_key(msg.get("api_key", "")):
            await ws.send_text(json.dumps({"type": "error", "message": "Authentication failed"}))
            await ws.close(code=4001)
            return
        await ws.send_text(json.dumps({"type": "auth_ok"}))
    except Exception:
        await ws.close(code=4001)
        return

    _remote_ws_connections.add(ws)
    logger.info("remote_ws_connected", total=len(_remote_ws_connections))

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)

            if msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
                continue

            if msg.get("type") == "chat":
                await _handle_ws_chat(ws, msg)
                continue

            if msg.get("type") == "announce":
                await _handle_ws_announce(ws, msg)
                continue

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("remote_ws_error", error=str(e))
    finally:
        _remote_ws_connections.discard(ws)
        logger.info("remote_ws_disconnected", total=len(_remote_ws_connections))


async def _handle_ws_chat(ws: WebSocket, msg: dict):
    """Handle a chat message over WebSocket with streaming response."""
    from brain_core.logic_router import SomaRequest
    from brain_core.main import logic_router, broadcast_thought

    if not logic_router:
        await ws.send_text(json.dumps({"type": "error", "message": "SOMA is booting"}))
        return

    message = msg.get("message", "")
    session_id = msg.get("session_id", str(uuid.uuid4()))
    user_id = msg.get("user_id", "remote_user")

    await broadcast_thought("info", f"📱 REMOTE WS: '{message[:60]}'", "REMOTE")

    soma_req = SomaRequest(
        prompt=message,
        user_id=user_id,
        session_id=session_id,
        metadata={"source": "remote_ws", "instanced": True},
    )

    start = time.monotonic()
    try:
        async for chunk in logic_router.route_stream(soma_req):
            await ws.send_text(json.dumps({
                "type": "chunk",
                "text": chunk.text,
                "is_final": chunk.is_final,
                "engine_used": chunk.engine_used,
                "session_id": session_id,
                "latency_ms": round((time.monotonic() - start) * 1000, 1),
            }))
    except Exception as e:
        logger.error("remote_ws_chat_error", error=str(e))
        await ws.send_text(json.dumps({"type": "error", "message": str(e)}))


async def _handle_ws_announce(ws: WebSocket, msg: dict):
    """Handle apartment announcement from WebSocket."""
    from brain_core.main import voice_pipeline, broadcast_thought

    message = msg.get("message", "")
    if not message:
        await ws.send_text(json.dumps({"type": "error", "message": "Empty announcement"}))
        return

    if not voice_pipeline:
        await ws.send_text(json.dumps({"type": "error", "message": "Voice pipeline unavailable"}))
        return

    await broadcast_thought("info", f"📢 REMOTE ANNOUNCE: '{message[:80]}'", "REMOTE")
    await voice_pipeline.autonomous_speak(message)

    await ws.send_text(json.dumps({"type": "announced", "message": message}))
