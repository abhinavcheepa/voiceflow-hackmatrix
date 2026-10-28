"""Call recording — every web call kept as one audio file and one text record.

The server already holds every second of a web call: each caller turn arrives
as an audio blob and each agent line leaves as an mp3. Segments are written as
the call runs (so a crash mid-call still leaves its audio on disk) and stitched
into one mp3 when it ends. Alongside it goes a plain-text record — details,
summary and timestamped transcript — readable without the dashboard.

Files live in backend/recordings/ (gitignored: they are customers' voices).
"""

import logging
import os
import re
import shutil
import subprocess
from datetime import datetime

log = logging.getLogger("voiceflow.recording")

HERE = os.path.dirname(os.path.abspath(__file__))
REC_DIR = os.getenv("RECORDINGS_DIR") or os.path.join(HERE, "recordings")

_SAFE_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def ffmpeg_exe() -> str | None:
    """Bundled ffmpeg (imageio-ffmpeg), else one on PATH."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg")


def path(call_id: str, ext: str) -> str:
    """Where a call's file lives. The id reaches the filesystem and, via the
    download endpoints, comes from a URL — so it is validated, never trusted."""
    if not _SAFE_ID.match(call_id or ""):
        raise ValueError("bad call id")
    return os.path.join(REC_DIR, f"{call_id}.{ext}")


class Recorder:
    """Collects one call's audio segments in order."""

    def __init__(self, call_id: str) -> None:
        self.call_id = call_id
        self.dir = os.path.join(REC_DIR, call_id)
        self.count = 0

    def add(self, role: str, audio: bytes, ext: str) -> None:
        if not audio:
            return
        try:
            os.makedirs(self.dir, exist_ok=True)
            self.count += 1
            with open(os.path.join(self.dir, f"{self.count:04d}-{role}.{ext}"), "wb") as f:
                f.write(audio)
        except OSError as e:  # a full disk must not end the call
            log.warning("recording segment lost for %s: %s", self.call_id, e)

    def finish(self) -> str | None:
        """Stitch the segments into <call_id>.mp3. Returns its path, or None.

        Every segment is resampled to one format first — caller turns are
        48 kHz webm/opus, agent lines 24 kHz mp3, and concat needs them equal.
        """
        if not self.count:
            return None
        exe = ffmpeg_exe()
        if not exe:
            log.warning("no ffmpeg — %s kept as separate segments in %s", self.call_id, self.dir)
            return None
        segments = sorted(os.listdir(self.dir))
        args = [exe, "-v", "error", "-y"]
        for s in segments:
            args += ["-i", os.path.join(self.dir, s)]
        graph = "".join(
            f"[{i}:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono[a{i}];"
            for i in range(len(segments))
        ) + "".join(f"[a{i}]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=0:a=1[out]"
        out = path(self.call_id, "mp3")
        args += ["-filter_complex", graph, "-map", "[out]", "-b:a", "64k", out]
        try:
            subprocess.run(args, capture_output=True, check=True, timeout=120)
        except (subprocess.SubprocessError, OSError) as e:
            detail = getattr(e, "stderr", b"") or b""
            log.warning("could not stitch %s (segments kept): %s %s", self.call_id, e,
                        detail.decode(errors="replace")[-300:])
            return None
        shutil.rmtree(self.dir, ignore_errors=True)
        return out


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def render_text(call: dict, lines: list[tuple[float, str, str]], analysis: dict) -> str:
    """The call as a plain-text document.

    `lines` are (seconds-into-call, speaker, text); speaker "—" marks an event
    such as a transfer rather than speech.
    """
    started = datetime.fromtimestamp(call["started"]).astimezone()
    dur = int(call.get("duration", 0))
    success = analysis.get("success")
    out = [
        "VoiceFlow AI — Call record",
        "=" * 40,
        f"Call ID   : {call['id']}",
        f"Agent     : {call.get('agent') or '—'}",
        f"Caller    : {analysis.get('name') or 'Unknown'}",
        f"Started   : {started:%d %b %Y, %I:%M %p %Z}",
        f"Duration  : {dur // 60}m {dur % 60:02d}s",
        f"Language  : {call.get('language') or '—'}",
        f"Outcome   : {analysis.get('outcome') or call.get('outcome') or '—'}"
        + ("" if success is None else f"   (resolved: {'yes' if success else 'no'})"),
        "",
        "SUMMARY",
        "-" * 40,
        analysis.get("summary") or "(No summary — the call had no conversation, or analysis was unavailable.)",
    ]
    for label, key in (("Intent", "intent"), ("Item", "item")):
        if analysis.get(key):
            out.append(f"{label}: {analysis[key]}")
    out += ["", "TRANSCRIPT", "-" * 40]
    out += [
        f"[{_clock(t)}] — {text} —" if who == "—" else f"[{_clock(t)}] {who}: {text}"
        for t, who, text in lines
    ] or ["(nothing was said)"]
    return "\n".join(out) + "\n"


def write_text(call_id: str, text: str) -> str | None:
    try:
        os.makedirs(REC_DIR, exist_ok=True)
        p = path(call_id, "txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p
    except (OSError, ValueError) as e:
        log.warning("text record not written for %s: %s", call_id, e)
        return None
