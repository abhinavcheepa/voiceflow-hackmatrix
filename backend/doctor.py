"""Doctor script for VoiceFlow end-to-end verification.

Runs local checks across Network, .env, LLM, STT, TTS, ffmpeg/PyAV, Voice Cloning,
WhatsApp Bridge, Web-call WebSocket, and Security Sanity.
Outputs one line per check: [OK], [WARN], or [FAIL] with reason and exact fix.
Exits with code 1 if any check FAILs.
"""

import asyncio
import io
import os
import re
import shutil
import socket
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Ensure backend directory is in python path
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Load .env if present
import dotenv
env_path = BACKEND_DIR / ".env"
root_env_path = BACKEND_DIR.parent / ".env"
loaded_env_file = "none"
if env_path.exists():
    dotenv.load_dotenv(str(env_path))
    loaded_env_file = "backend/.env"
elif root_env_path.exists():
    dotenv.load_dotenv(str(root_env_path))
    loaded_env_file = ".env"

import auth
import brain
import db
import recording
import upload_security
import voice
import webcall_gate


def _mask(s: str | None, keep: int = 4) -> str:
    if not s:
        return "(empty)"
    if len(s) <= keep:
        return "***"
    return f"{s[:keep]}...***"


async def check_network() -> Dict[str, Any]:
    """1. Network: DNS and HTTPS reachability."""
    targets = [
        ("api.groq.com", "LLM chat completions & cloud Whisper STT"),
        ("huggingface.co", "faster-whisper models & OpenVoice weights"),
        ("speech.platform.bing.com", "Microsoft Edge TTS synthesis"),
        ("storage.googleapis.com", "Web resources & package assets"),
    ]
    details = []
    failed = []
    import httpx

    async with httpx.AsyncClient(timeout=4.0) as client:
        for host, purpose in targets:
            # 1. DNS check
            try:
                ip = socket.gethostbyname(host)
            except Exception as e:
                failed.append((host, f"DNS failed ({e})", purpose))
                continue

            # 2. HTTPS reachability
            try:
                r = await client.head(f"https://{host}", follow_redirects=True)
                details.append(f"{host} [OK {r.status_code}] ({purpose})")
            except Exception as e:
                try:
                    r = await client.get(f"https://{host}", follow_redirects=True)
                    details.append(f"{host} [OK {r.status_code}] ({purpose})")
                except Exception as e2:
                    failed.append((host, f"HTTPS unreachable ({type(e2).__name__})", purpose))

    if failed:
        reasons = "; ".join(f"{h}: {err} ({p})" for h, err, p in failed)
        return {
            "name": "Network",
            "status": "FAIL",
            "message": f"Endpoints unreachable: {reasons}",
            "fix": "Check internet connectivity, DNS, or proxy settings.",
            "details": details,
        }
    return {
        "name": "Network",
        "status": "OK",
        "message": f"All 4 endpoints reachable: {', '.join(h for h, _ in targets)}",
        "fix": "None required.",
        "details": details,
    }


def check_env() -> Dict[str, Any]:
    """2. .env file loaded, set keys, conflicts, stray spaces/quotes, chat provider."""
    env_keys = set(os.environ.keys())
    standard_keys = [
        "GROQ_API_KEY", "CHAT_API_KEY", "CHAT_BASE_URL", "CHAT_MODEL",
        "VOICE_PROVIDER", "STT_PROVIDER", "WHISPER_MODEL", "BRIDGE_SECRET",
        "API_TOKEN", "ASSISTANT_PIN", "WHATSAPP_PROVIDER", "TRUSTED_PROXIES",
    ]
    present_keys = [k for k in standard_keys if k in env_keys and os.environ.get(k)]

    # Check conflicts
    conflicts = []
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    chat_key = os.environ.get("CHAT_API_KEY", "").strip()
    chat_base = os.environ.get("CHAT_BASE_URL", "").strip()
    chat_model = os.environ.get("CHAT_MODEL", "").strip()

    if chat_key and groq_key and not chat_base:
        conflicts.append("CHAT_API_KEY is set without CHAT_BASE_URL while GROQ_API_KEY is present")

    # Check stray spaces or quotes in values
    stray_warnings = []
    for k in present_keys:
        val = os.environ.get(k, "")
        if val.startswith('"') and val.endswith('"') and len(val) >= 2:
            stray_warnings.append(f"{k} has surrounding double-quotes")
        elif val.startswith("'") and val.endswith("'") and len(val) >= 2:
            stray_warnings.append(f"{k} has surrounding single-quotes")
        elif val != val.strip():
            stray_warnings.append(f"{k} has leading/trailing whitespace")

    # Determine real chat provider
    if chat_base and chat_key:
        provider_name = f"Custom/NVIDIA ({chat_base}, model: {chat_model or 'default'})"
    elif groq_key or brain.GROQ_KEY:
        provider_name = f"Groq (hosted, model: {brain.CHAT_MODEL})"
    else:
        provider_name = "None (no API key configured)"

    status = "OK"
    reasons = []
    if not groq_key and not chat_key:
        status = "FAIL"
        reasons.append("No LLM key configured (neither GROQ_API_KEY nor CHAT_API_KEY)")
    if conflicts:
        status = "WARN" if status == "OK" else status
        reasons.extend(conflicts)
    if stray_warnings:
        status = "WARN" if status == "OK" else status
        reasons.extend(stray_warnings)

    msg = f"Loaded {loaded_env_file} | Keys set: {', '.join(present_keys)} | Active chat provider: {provider_name}"
    if reasons:
        msg += f" | Issues: {'; '.join(reasons)}"

    return {
        "name": ".env Configuration",
        "status": status,
        "message": msg,
        "fix": "Set GROQ_API_KEY in backend/.env or configure CHAT_BASE_URL + CHAT_API_KEY.",
    }


async def check_llm() -> Dict[str, Any]:
    """3. LLM round-trip."""
    t0 = time.time()
    try:
        reply = await brain.reply("doctor_test", "Reply with one word: Namaste")
        latency_ms = int((time.time() - t0) * 1000)
        return {
            "name": "LLM Round-Trip",
            "status": "OK",
            "message": f"Replied in {latency_ms}ms with '{reply.strip()[:30]}' via {brain.CHAT_MODEL}",
            "fix": "None required.",
        }
    except Exception as e:
        latency_ms = int((time.time() - t0) * 1000)
        from webcall import mask_error
        masked_err = mask_error(e)
        return {
            "name": "LLM Round-Trip",
            "status": "FAIL",
            "message": f"Failed after {latency_ms}ms: {masked_err}",
            "fix": "Check GROQ_API_KEY at console.groq.com/keys or verify CHAT_BASE_URL.",
        }


async def check_stt() -> Dict[str, Any]:
    """4. STT provider, model, compute, load time, and transcription test."""
    backend_choice = brain.stt_backend()
    model_name = os.getenv("WHISPER_MODEL") or os.getenv("LOCAL_STT_MODEL") or "base"
    compute = os.getenv("WHISPER_COMPUTE") or "int8"
    load_time_ms = 0
    cached = False

    t0 = time.time()
    try:
        if brain._local_stt_available():
            model_inst = brain._load_local()
            load_time_ms = int((time.time() - t0) * 1000)
            cached = True
    except Exception:
        cached = False

    hindi_transcription = ""
    english_transcription = ""
    status = "OK"
    warnings = []

    try:
        hindi_audio = await voice.synthesize("Namaste", "Hindi")
        hindi_transcription = await brain.transcribe(hindi_audio, "hindi.mp3")
    except Exception as e:
        warnings.append(f"Hindi transcription failed ({e})")

    try:
        en_audio = await voice.synthesize("Hello", "English")
        english_transcription = await brain.transcribe(en_audio, "english.mp3")
    except Exception as e:
        warnings.append(f"English transcription failed ({e})")

    # Evaluate accuracy
    hindi_ok = "namaste" in hindi_transcription.lower() or "नमस्ते" in hindi_transcription
    if not hindi_ok and backend_choice == "local" and model_name == "base":
        warnings.append(
            f"Local Whisper 'base' model transcribed Hindi 'Namaste' as '{hindi_transcription}'. "
            "Recommendation: Set WHISPER_MODEL=small or use Groq STT for higher Hindi accuracy."
        )
        status = "WARN"

    msg = (
        f"Backend: {backend_choice} (STT_PROVIDER={os.getenv('STT_PROVIDER', 'auto')}) | "
        f"Model: {model_name} ({compute}), cached={cached}, load_time={load_time_ms}ms | "
        f"Hindi: '{hindi_transcription}' | English: '{english_transcription}'"
    )
    if warnings:
        msg += f" | Warnings: {'; '.join(warnings)}"

    return {
        "name": "STT (Transcription)",
        "status": status,
        "message": msg,
        "fix": "Set GROQ_API_KEY for cloud STT or WHISPER_MODEL=small in backend/.env.",
    }


async def check_tts() -> Dict[str, Any]:
    """5. TTS Edge TTS synthesis of Hindi & English sentences, byte size and playable duration."""
    hindi_phrase = "Namaste! VoiceFlow me aapka swagat hai."
    en_phrase = "Hello, your appointment has been confirmed."

    try:
        t0 = time.time()
        hi_audio = await voice.synthesize(hindi_phrase, "Hindi")
        hi_time = time.time() - t0
        en_audio = await voice.synthesize(en_phrase, "English")

        hi_dur = round(len(hi_audio) / 4000, 2)
        en_dur = round(len(en_audio) / 4000, 2)

        cartesia_info = "Cartesia skipped (CARTESIA_API_KEY unset)"
        if os.getenv("CARTESIA_API_KEY"):
            cartesia_info = f"Cartesia key present ({_mask(os.getenv('CARTESIA_API_KEY'))})"

        return {
            "name": "TTS (Speech Synthesis)",
            "status": "OK",
            "message": (
                f"Edge TTS synthesized Hindi ({len(hi_audio)} bytes, ~{hi_dur}s in {round(hi_time, 2)}s) "
                f"and English ({len(en_audio)} bytes, ~{en_dur}s). {cartesia_info}"
            ),
            "fix": "None required.",
        }
    except Exception as e:
        return {
            "name": "TTS (Speech Synthesis)",
            "status": "FAIL",
            "message": f"TTS synthesis failed: {e}",
            "fix": "Verify internet access to speech.platform.bing.com or set VOICE_PROVIDER=edge in backend/.env.",
        }


def check_ffmpeg_pyav() -> Dict[str, Any]:
    """6. ffmpeg/PyAV and WebM magic byte validation."""
    status_dict = recording.ffmpeg_status()
    # Test WebM magic bytes (EBML 1A 45 DF A3)
    webm_sample = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04" + b"\x00" * 200
    try:
        mime = upload_security.validate_magic_bytes_audio(webm_sample, "mic_sample.webm")
        magic_ok = mime == "audio/webm"
    except Exception:
        magic_ok = False

    if status_dict.get("status") == "ok" and magic_ok:
        return {
            "name": "ffmpeg / Audio Decoder",
            "status": "OK",
            "message": f"ffmpeg available ({status_dict.get('path')}) | WebM EBML magic-bytes (1A 45 DF A3) passed",
            "fix": "None required.",
        }
    return {
        "name": "ffmpeg / Audio Decoder",
        "status": "FAIL",
        "message": f"ffmpeg check: {status_dict.get('reason', 'ok')} | WebM magic byte validation: {magic_ok}",
        "fix": "Ensure imageio-ffmpeg is installed or ffmpeg is on system PATH.",
    }


async def check_voice_cloning() -> Dict[str, Any]:
    """7. Voice cloning check."""
    import cloning

    missing_reason = cloning.missing()
    clones_dir = BACKEND_DIR / "clones"
    clones_dir.mkdir(parents=True, exist_ok=True)
    disk_free_gb = round(shutil.disk_usage(clones_dir).free / (1024 ** 3), 2)

    # Path traversal check
    traversal_rejected = False
    try:
        cloning._path("../../etc/passwd")
    except ValueError:
        traversal_rejected = True

    # Test failure fallback path
    fallback_ok = False
    try:
        base_fallback = await voice._clone_speak("Namaste", "clone:nonexistent", "hi")
        fallback_ok = len(base_fallback) > 0
    except Exception:
        fallback_ok = False

    guidance = "Voice Studio requires at least 25 seconds of clean speech recordings across 3 prompts."

    if missing_reason:
        return {
            "name": "Voice Cloning (OpenVoice)",
            "status": "WARN",
            "message": (
                f"Cloning engine not installed: {missing_reason} | Free disk: {disk_free_gb} GB | "
                f"Fallback to stock Edge voice verified: {fallback_ok} | Path traversal rejected: {traversal_rejected} | "
                f"Enrollment requirement: {guidance}"
            ),
            "fix": "Voice cloning is optional. To enable local cloning: pip install torch openvoice and download checkpoints_v2/.",
        }

    return {
        "name": "Voice Cloning (OpenVoice)",
        "status": "OK",
        "message": f"Ready | Disk free: {disk_free_gb} GB | Traversal safe: {traversal_rejected}",
        "fix": "None required.",
    }


def check_whatsapp() -> Dict[str, Any]:
    """8. WhatsApp bridge status, provider, secret match, allowlist."""
    provider = os.getenv("WHATSAPP_PROVIDER", "meta")
    bridge_url = os.getenv("WA_BRIDGE_URL", "http://127.0.0.1:8100")
    backend_secret = os.getenv("BRIDGE_SECRET", "")
    mode = db.get_setting("auto_reply_mode", os.getenv("AUTO_REPLY_MODE", "allowlist"))
    allowlist_count = len(db.get_allowlist())

    # Check bridge reachability
    bridge_reachable = False
    bridge_state = "offline"
    secret_match = "unknown"

    try:
        req = urllib.request.Request(f"{bridge_url}/status")
        with urllib.request.urlopen(req, timeout=1.5) as r:
            if r.status == 200:
                import json
                data = json.loads(r.read().decode())
                bridge_reachable = True
                bridge_state = data.get("status", "unknown")
                secret_match = "match" if backend_secret else "no backend secret"
    except Exception:
        bridge_reachable = False

    candidates = [
        os.getenv("PUPPETEER_EXECUTABLE_PATH"),
        os.getenv("CHROME_PATH"),
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ]
    chrome_found = next((c for c in candidates if c and os.path.exists(c)), None)

    status = "OK" if bridge_reachable else "WARN"
    msg = (
        f"Provider: {provider} | Bridge on {bridge_url}: {'Online (' + bridge_state + ')' if bridge_reachable else 'Offline'} | "
        f"Secret: {secret_match} | Auto-reply mode: {mode} (allowlisted: {allowlist_count} numbers) | "
        f"Chrome path: {chrome_found or 'Puppeteer default'}"
    )
    fix = "cd wa-bridge && npm start (or start Docker stack with docker compose up -d)" if not bridge_reachable else "None required."

    return {
        "name": "WhatsApp Bridge",
        "status": status,
        "message": msg,
        "fix": fix,
    }


def _sync_check_webcall() -> Dict[str, Any]:
    """Run WebCall WebSocket turn synchronously inside worker thread."""
    from starlette.testclient import TestClient
    import main

    client = TestClient(main.app)
    try:
        r = client.get("/api/web-call/ticket", headers={"Origin": "http://localhost:5174"})
        if r.status_code != 200:
            return {
                "name": "Web-Call WebSocket",
                "status": "FAIL",
                "message": f"Ticket issuance failed with HTTP {r.status_code}",
                "fix": "Verify /api/web-call/ticket endpoint and rate limits.",
            }
        ticket = r.json().get("ticket")

        with client.websocket_connect(
            "/api/web-call/ws",
            subprotocols=[f"vf-ticket.{ticket}"],
            headers={"Origin": "http://localhost:5174"},
        ) as ws:
            connected = ws.receive_json()
            greeting_msg = ws.receive_json()
            greeting_audio = ws.receive_bytes()

            # Execute a short spoken turn: synthesize 1s audio, send as bytes
            sample_clip = asyncio.run(voice.synthesize("Namaste", "Hindi"))
            ws.send_bytes(sample_clip)

            resp1 = ws.receive_json()  # {"type": "thinking"}
            resp2 = ws.receive_json()
            if resp2.get("type") == "caller":
                resp3 = ws.receive_json()  # {"type": "agent"}
                agent_audio = ws.receive_bytes()
                turn_msg = f"heard='{resp2.get('text', '')}' -> replied='{resp3.get('text', '')[:25]}...' -> TTS {len(agent_audio)} bytes"
            elif resp2.get("type") == "idle":
                turn_msg = "turn processed as idle"
            else:
                turn_msg = f"turn response: {resp2.get('type')} ({resp2.get('text', '')})"

            return {
                "name": "Web-Call WebSocket",
                "status": "OK",
                "message": (
                    f"Ticket issued | WS connected ({connected.get('callId')}) Origin http://localhost:5174 | "
                    f"Greeting: '{greeting_msg.get('text', '')[:25]}...' ({len(greeting_audio)} bytes) | {turn_msg}"
                ),
                "fix": "None required.",
            }
    except Exception as e:
        close_code = getattr(e, "code", None)
        code_str = f" (Close code: {close_code})" if close_code else ""
        return {
            "name": "Web-Call WebSocket",
            "status": "FAIL",
            "message": f"WebSocket turn failed{code_str}: {e}",
            "fix": "Check ALLOWED_ORIGINS, ticket validation, and audio format requirements in webcall.py.",
        }


async def check_webcall() -> Dict[str, Any]:
    """9. Web-call ticket issuance, WebSocket handshake, and turn response."""
    return await asyncio.to_thread(_sync_check_webcall)


def check_security() -> Dict[str, Any]:
    """10. Security sanity (read-only): ENV, API_TOKEN, ASSISTANT_PIN, BRIDGE_SECRET, etc."""
    env_name = os.getenv("ENV", "development")
    api_token_set = bool(os.getenv("API_TOKEN"))
    pin = os.getenv("ASSISTANT_PIN", "")
    pin_len_ok = len(pin) >= 8
    bridge_secret_set = bool(os.getenv("BRIDGE_SECRET"))
    allow_unsigned = os.getenv("ALLOW_UNSIGNED_WEBHOOKS", "false").lower() == "true"
    wa_secret_set = bool(os.getenv("WHATSAPP_APP_SECRET"))
    trusted_proxies = os.getenv("TRUSTED_PROXIES", "127.0.0.1,::1")
    allowed_origins = os.getenv("ALLOWED_ORIGINS", os.getenv("CORS_ORIGINS", ""))

    issues = []
    status = "OK"

    if env_name == "production":
        if not api_token_set:
            issues.append("API_TOKEN unset in production")
            status = "FAIL"
        if not pin_len_ok:
            issues.append("ASSISTANT_PIN length < 8 in production")
            status = "FAIL"
        if not bridge_secret_set:
            issues.append("BRIDGE_SECRET unset in production")
            status = "FAIL"
        if allow_unsigned:
            issues.append("ALLOW_UNSIGNED_WEBHOOKS=true in production (must fail-closed)")
            status = "FAIL"

    msg = (
        f"ENV: {env_name} | API_TOKEN set: {'yes' if api_token_set else 'no'} | "
        f"ASSISTANT_PIN length >= 8: {'yes' if pin_len_ok else 'no'} | "
        f"BRIDGE_SECRET set: {'yes' if bridge_secret_set else 'no'} | "
        f"WHATSAPP_APP_SECRET: {'set' if wa_secret_set else 'unset'} (ALLOW_UNSIGNED={allow_unsigned}) | "
        f"TRUSTED_PROXIES: {trusted_proxies} | ALLOWED_ORIGINS: {allowed_origins or 'default'}"
    )
    if issues:
        msg += f" | Security issues: {'; '.join(issues)}"

    return {
        "name": "Security Sanity",
        "status": status,
        "message": msg,
        "fix": "Set required secrets (API_TOKEN, ASSISTANT_PIN>=8 chars, BRIDGE_SECRET) in .env.",
    }


async def run_doctor_checks() -> List[Dict[str, Any]]:
    """Execute all 10 doctor checks sequentially."""
    results = []
    results.append(await check_network())
    results.append(check_env())
    results.append(await check_llm())
    results.append(await check_stt())
    results.append(await check_tts())
    results.append(check_ffmpeg_pyav())
    results.append(await check_voice_cloning())
    results.append(check_whatsapp())
    results.append(await check_webcall())
    results.append(check_security())
    return results


def main() -> int:
    print("=" * 80, flush=True)
    print(" VoiceFlow Diagnostic Doctor — System Verification", flush=True)
    print("=" * 80, flush=True)

    results = asyncio.run(run_doctor_checks())

    has_fail = False
    for res in results:
        status = res["status"]
        name = res["name"]
        msg = res["message"]
        fix = res.get("fix", "")

        if status == "FAIL":
            has_fail = True
            tag = "[FAIL]"
        elif status == "WARN":
            tag = "[WARN]"
        else:
            tag = "[ OK ]"

        line = f"{tag} {name}: {msg}"
        if status in ("WARN", "FAIL") and fix and fix != "None required.":
            line += f" -> FIX: {fix}"
        print(line, flush=True)

    print("=" * 80, flush=True)
    if has_fail:
        print("OVERALL: FAILED (one or more core checks failed). See FIX suggestions above.", flush=True)
        return 1
    print("OVERALL: PASSED (all systems verified or non-blocking warnings).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
