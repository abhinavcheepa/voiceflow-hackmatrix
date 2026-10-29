"""
Local OpenVoice v2 cloning pipeline with low-latency caching and audio persistence.
Model files live in backend/openvoice/checkpoints_v2/
Cloned voice profiles live in backend/clones/{id}.pt
Persisted generated audios live in backend/recordings/cloned/
"""
from __future__ import annotations

import io
import os
import re
import sys
import time
import shutil
import hashlib
import logging
from typing import Optional, Tuple, Dict
from pathlib import Path

logger = logging.getLogger("cloning")

# Persistent directory for cloned audios
CLONED_AUDIO_DIR = Path(__file__).resolve().parent / "recordings" / "cloned"
CLONED_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# In-memory target embedding cache: {clone_id: (target_se, timestamp)}
_target_cache: Dict[str, any] = {}
# In-memory audio response cache: {(clone_id, audio_hash): (mp3_bytes, audio_url)}
_audio_cache: Dict[Tuple[str, str], Tuple[bytes, str]] = {}
_MAX_AUDIO_CACHE = 100

_MODEL = None
_DEVICE = None
_EXTRACTOR = None
_CONVERTER = None
_CHECKPOINTS_DIR = Path(__file__).resolve().parent / "openvoice" / "checkpoints_v2"
_CLONES_DIR = Path(__file__).resolve().parent / "clones"
_CLONES_DIR.mkdir(parents=True, exist_ok=True)


def _init_models():
    global _MODEL, _DEVICE, _EXTRACTOR, _CONVERTER
    if _CONVERTER is not None:
        return

    import torch
    # Optimize CPU threads for real-time inference latency
    if not torch.cuda.is_available():
        try:
            torch.set_num_threads(min(4, os.cpu_count() or 4))
        except Exception:
            pass

    _DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"
    logger.info("Initializing OpenVoice v2 on %s", _DEVICE)

    try:
        from openvoice.api import ToneColorConverter
        from openvoice.se_extractor import get_se

        ckpt = _CHECKPOINTS_DIR / "converter"
        config_path = ckpt / "config.json"
        model_path = ckpt / "checkpoint.pth"

        if not config_path.exists() or not model_path.exists():
            logger.warning("OpenVoice v2 checkpoints missing in %s", ckpt)
            return

        _CONVERTER = ToneColorConverter(str(config_path), device=_DEVICE)
        _CONVERTER.load_ckpt(str(model_path))
        _EXTRACTOR = get_se
        logger.info("OpenVoice v2 ToneColorConverter loaded successfully on %s", _DEVICE)
    except Exception as e:
        logger.error("Failed to initialize OpenVoice v2: %s", e, exc_info=True)


def is_available() -> bool:
    ckpt = _CHECKPOINTS_DIR / "converter" / "checkpoint.pth"
    return ckpt.exists()


def list_clones() -> list[dict]:
    _CLONES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for f in _CLONES_DIR.glob("*.pt"):
        cid = f.stem
        out.append({
            "id": cid,
            "created_at": f.stat().st_mtime,
            "size_bytes": f.stat().st_size,
        })
    return sorted(out, key=lambda x: x["created_at"], reverse=True)


def enroll_sample(sample_path: str, clone_id: str) -> dict:
    """Extract speaker embedding from sample audio and save to disk + cache."""
    _init_models()
    if _CONVERTER is None or _EXTRACTOR is None:
        raise RuntimeError("OpenVoice v2 models not available")

    import torch
    out_pt = _CLONES_DIR / f"{clone_id}.pt"
    se, audio_name = _EXTRACTOR(sample_path, _CONVERTER, target_dir=str(_CLONES_DIR), vad=True)
    torch.save(se, str(out_pt))

    # Update in-memory target cache
    _target_cache[clone_id] = se
    logger.info("Saved clone profile %s -> %s", clone_id, out_pt)
    return {
        "id": clone_id,
        "path": str(out_pt),
        "size_bytes": out_pt.stat().st_size,
    }


def delete_clone(clone_id: str) -> bool:
    _target_cache.pop(clone_id, None)
    out_pt = _CLONES_DIR / f"{clone_id}.pt"
    if out_pt.exists():
        out_pt.unlink()
        return True
    return False


def _get_target_se(clone_id: str):
    """Retrieve target speaker embedding from fast in-memory cache or disk."""
    if clone_id in _target_cache:
        return _target_cache[clone_id]

    import torch
    pt_path = _CLONES_DIR / f"{clone_id}.pt"
    if not pt_path.exists():
        raise FileNotFoundError(f"Clone profile not found: {clone_id}")

    se = torch.load(str(pt_path), map_location=_DEVICE)
    _target_cache[clone_id] = se
    return se


def convert(
    base_audio_bytes: bytes,
    clone_id: str,
    tau: float = 0.20,
    text: str = "",
    save_to_db: bool = True
) -> bytes:
    """
    Apply speaker color conversion to base TTS audio and persistently save the result.
    Optimized for low latency:
    - In-memory target embedding caching
    - In-memory audio response caching
    - Lower TAU (0.20) for crisp phonetics & un-muffled pronunciation
    - Saves output MP3 to recordings/cloned/ and records in SQLite DB.
    """
    _init_models()
    if _CONVERTER is None or _EXTRACTOR is None:
        logger.warning("ToneColorConverter unavailable, returning base audio")
        return base_audio_bytes

    # Check audio cache for identical base audio + clone_id
    audio_hash = hashlib.md5(base_audio_bytes).hexdigest()
    cache_key = (clone_id, audio_hash)
    if cache_key in _audio_cache:
        cached_bytes, _ = _audio_cache[cache_key]
        return cached_bytes

    t0 = time.time()
    import tempfile
    import torch
    import soundfile as sf
    import numpy as np

    tmp_in = None
    tmp_out = None
    tmp_base_se = None

    try:
        target_se = _get_target_se(clone_id)

        # Temporary file for base audio input
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f_in:
            f_in.write(base_audio_bytes)
            tmp_in = f_in.name

        tmp_out = tmp_in + ".converted.wav"
        tmp_base_se = tmp_in + ".se.pt"

        # Extract base audio speaker embedding
        base_se, _ = _EXTRACTOR(tmp_in, _CONVERTER, target_dir=os.path.dirname(tmp_in), vad=False)

        # Convert tone color (tau=0.20 balances speaker timbre without distorting phonemes)
        _CONVERTER.convert(
            audio_src_path=tmp_in,
            src_se=base_se,
            tgt_se=target_se,
            output_path=tmp_out,
            tau=tau,
        )

        # Load converted audio and normalize peak levels to prevent clipping
        data, samplerate = sf.read(tmp_out)
        max_val = np.max(np.abs(data))
        if max_val > 0.01:
            data = data / max_val * 0.95

        # Encode to MP3 via soundfile or pydub
        out_buf = io.BytesIO()
        sf.write(out_buf, data, samplerate, format="WAV")
        wav_bytes = out_buf.getvalue()

        # Convert WAV bytes to MP3
        final_mp3_bytes = _wav_to_mp3(wav_bytes)

        elapsed_ms = int((time.time() - t0) * 1000)
        logger.info("Voice conversion done: clone=%s, size=%d, elapsed=%dms", clone_id, len(final_mp3_bytes), elapsed_ms)

        # Save cloned audio to persistent storage & DB
        filename = f"clone_{clone_id}_{int(time.time())}_{audio_hash[:8]}.mp3"
        dest_path = CLONED_AUDIO_DIR / filename
        with open(dest_path, "wb") as f_save:
            f_save.write(final_mp3_bytes)

        duration_sec = round(len(data) / float(samplerate), 2)
        if save_to_db:
            try:
                import db
                db.save_cloned_audio(
                    clone_id=clone_id,
                    text=text[:300] if text else "",
                    filename=filename,
                    filepath=str(dest_path),
                    size_bytes=len(final_mp3_bytes)
                )
            except Exception as dbe:
                logger.error("Failed to log cloned audio in DB: %s", dbe)

        # Cache result
        if len(_audio_cache) >= _MAX_AUDIO_CACHE:
            _audio_cache.pop(next(iter(_audio_cache)))
        _audio_cache[cache_key] = (final_mp3_bytes, f"/api/voice/cloned-audios/play/{filename}")

        return final_mp3_bytes

    except Exception as e:
        logger.error("Voice conversion error: %s", e, exc_info=True)
        return base_audio_bytes
    finally:
        for p in [tmp_in, tmp_out, tmp_base_se]:
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass


def _wav_to_mp3(wav_bytes: bytes) -> bytes:
    """Convert WAV bytes to MP3 bytes using pydub or return WAV if pydub/ffmpeg not found."""
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_wav(io.BytesIO(wav_bytes))
        mp3_io = io.BytesIO()
        seg.export(mp3_io, format="mp3", bitrate="128k")
        return mp3_io.getvalue()
    except Exception as e:
        logger.debug("pydub mp3 export fallback: %s", e)
        return wav_bytes


def warm():
    """Pre-warm models on server startup."""
    try:
        _init_models()
    except Exception as e:
        logger.debug("Warmup error: %s", e)
