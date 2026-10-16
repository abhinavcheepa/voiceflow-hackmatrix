"""Local voice cloning — the agent speaks in the owner's own voice, free.

Pipeline, all on this machine, no paid API:

    text ─► Edge TTS (fluent Hindi/English, fast) ─► OpenVoice V2 tone-colour
            converter ─► same words, same accent, the owner's timbre

Enrolment turns ~30s of the owner's recorded speech into one 256-number
"tone colour" embedding. Every reply is then synthesised by Edge and converted
to that timbre. Conversion is language-agnostic, so the clone speaks Hindi,
Marathi or Tamil as well as Edge does — which a from-scratch cloning TTS on CPU
could not, at anywhere near call latency.

OpenVoice V2 (MIT, myshell-ai) is used only for its converter network; its TTS
front-end, Chinese text stack and watermarking are skipped so the install stays
torch + librosa. Heavy deps are imported lazily: without them the rest of the
app runs exactly as before and cloning reports what is missing.

Install once (CPU wheels, ~250 MB download):
    pip install -r requirements-clone.txt
"""

import logging
import os
import shutil
import subprocess
import threading
import uuid

log = logging.getLogger("voiceflow.cloning")

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.getenv("CLONE_MODEL_DIR") or os.path.join(HERE, "models", "openvoice_v2")
VOICES_DIR = os.getenv("CLONE_VOICES_DIR") or os.path.join(HERE, "voices")
HF_REPO = "myshell-ai/OpenVoiceV2"

# How strongly to pull toward the target timbre. OpenVoice's own default; lower
# keeps more of the base voice, higher can smear words.
TAU = float(os.getenv("CLONE_TAU") or 0.3)
# Below this much clean speech the embedding is mostly room noise.
MIN_SECONDS = 10

_lock = threading.Lock()  # one CPU model, one conversion at a time
_model = None
_hps = None


def missing() -> str | None:
    """None when cloning can run, else what to install."""
    import importlib.util

    for mod, pkg in (("torch", "torch"), ("librosa", "librosa"), ("openvoice", "OpenVoice")):
        if importlib.util.find_spec(mod) is None:
            return f"{pkg} is not installed — run: pip install -r requirements-clone.txt"
    if not _ffmpeg():
        return "ffmpeg not found — run: pip install imageio-ffmpeg"
    return None


def available() -> bool:
    return missing() is None


def _ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - fall back to a system ffmpeg
        return shutil.which("ffmpeg")


# ── audio in / out ──────────────────────────────────────────────────────
# ffmpeg, because owner samples arrive as browser webm/opus and Edge returns
# mp3 — neither of which soundfile/librosa can decode on their own on Windows.

def decode(data: bytes, sr: int):
    """Any audio container -> mono float32 numpy at `sr`."""
    import numpy as np

    out = subprocess.run(
        [_ffmpeg(), "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1",
         "-ar", str(sr), "pipe:1"],
        input=data, capture_output=True, check=True,
    ).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def encode_mp3(audio, sr: int) -> bytes:
    import numpy as np

    return subprocess.run(
        [_ffmpeg(), "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1",
         "-i", "pipe:0", "-f", "mp3", "-b:a", "128k", "pipe:1"],
        input=np.asarray(audio, dtype=np.float32).tobytes(), capture_output=True, check=True,
    ).stdout


# ── model ───────────────────────────────────────────────────────────────

def _checkpoint() -> tuple[str, str]:
    """Converter config + weights (~130 MB), fetched from Hugging Face once."""
    cfg = os.path.join(MODEL_DIR, "converter", "config.json")
    ckpt = os.path.join(MODEL_DIR, "converter", "checkpoint.pth")
    if not (os.path.exists(cfg) and os.path.exists(ckpt)):
        from huggingface_hub import hf_hub_download

        log.info("downloading OpenVoice V2 converter to %s (one time)", MODEL_DIR)
        for f in ("converter/config.json", "converter/checkpoint.pth"):
            hf_hub_download(HF_REPO, f, local_dir=MODEL_DIR)
    return cfg, ckpt


def _load():
    """Build the converter once. Mirrors openvoice.api.ToneColorConverter
    without its watermark and text-frontend imports."""
    global _model, _hps
    if _model is None:
        if reason := missing():
            raise RuntimeError(reason)
        import torch
        from openvoice import utils
        from openvoice.models import SynthesizerTrn

        cfg, ckpt = _checkpoint()
        hps = utils.get_hparams_from_file(cfg)
        model = SynthesizerTrn(
            len(getattr(hps, "symbols", [])),
            hps.data.filter_length // 2 + 1,
            n_speakers=hps.data.n_speakers,
            **hps.model,
        )
        state = torch.load(ckpt, map_location="cpu")
        model.load_state_dict(state["model"], strict=False)
        model.eval()
        _model, _hps = model, hps
        log.info("voice-clone converter loaded")
    return _model, _hps


def _spec(audio):
    import torch
    from openvoice.mel_processing import spectrogram_torch

    d = _hps.data
    y = torch.FloatTensor(audio).unsqueeze(0)
    return spectrogram_torch(y, d.filter_length, d.sampling_rate, d.hop_length,
                             d.win_length, center=False)


def _tone(spec):
    """Tone-colour embedding of one utterance."""
    return _model.ref_enc(spec.transpose(1, 2)).unsqueeze(-1)


# ── public ──────────────────────────────────────────────────────────────

def _path(clone_id: str) -> str:
    # The id is ours (hex), but it reaches the filesystem — never trust it blindly.
    if not clone_id.isalnum():
        raise ValueError("bad clone id")
    return os.path.join(VOICES_DIR, f"{clone_id}.pt")


def enroll(samples: list[bytes]) -> str:
    """Owner's recorded clips -> a saved voice. Returns its id. Blocking."""
    import numpy as np
    import torch

    with _lock:
        model, hps = _load()
        sr = hps.data.sampling_rate
        clips = [a for a in (decode(s, sr) for s in samples) if a.size]
        seconds = sum(a.size for a in clips) / sr
        if seconds < MIN_SECONDS:
            raise RuntimeError(f"Only {seconds:.0f}s of audio — record at least {MIN_SECONDS}s.")
        # Normalise loudness so a quiet clip doesn't count for less.
        clips = [a / max(float(np.abs(a).max()), 1e-4) * 0.9 for a in clips]
        with torch.no_grad():
            se = torch.stack([_tone(_spec(a)) for a in clips]).mean(0)

    clone_id = uuid.uuid4().hex[:12]
    os.makedirs(VOICES_DIR, exist_ok=True)
    torch.save(se.cpu(), _path(clone_id))
    log.info("enrolled clone %s from %.0fs of audio", clone_id, seconds)
    return clone_id


def convert(base_audio: bytes, clone_id: str) -> bytes:
    """Re-voice Edge's mp3 in the cloned timbre. Returns mp3. Blocking."""
    import torch

    tgt = torch.load(_path(clone_id), map_location="cpu")
    with _lock:
        model, hps = _load()
        sr = hps.data.sampling_rate
        audio = decode(base_audio, sr)
        with torch.no_grad():
            spec = _spec(audio)
            src = _tone(spec)  # the base voice's own timbre, from this very line
            out = model.voice_conversion(
                spec, torch.LongTensor([spec.size(-1)]), sid_src=src, sid_tgt=tgt, tau=TAU,
            )[0][0, 0].cpu().float().numpy()
    return encode_mp3(out, sr)


def delete(clone_id: str) -> None:
    try:
        os.remove(_path(clone_id))
    except (FileNotFoundError, ValueError):
        pass


def warm() -> None:
    """Pay the torch + model load at startup instead of on the first reply."""
    try:
        with _lock:
            _load()
    except Exception as e:  # noqa: BLE001 - a cold first clone reply is not worth a crash
        log.info("clone warm-up skipped: %s", e)
