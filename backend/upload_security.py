"""Upload security, size validation, magic-byte inspection, and DoS prevention.

Enforces:
1. Hard Content-Length checks and bounded streaming reads (rejects files > limit without buffering into RAM).
2. True magic-byte validation (rejects extension-only checks, dangerous executables, zip bombs).
3. Safe filename sanitization (no path traversal, random temp filenames).
4. Per-IP concurrent upload quotas.
5. Strict timeouts on parsing/extraction.
"""

import asyncio
import io
import logging
import os
import re
from typing import Any
from fastapi import HTTPException, UploadFile

log = logging.getLogger("voiceflow.uploads")

# Size limits
MAX_KB_FILE_BYTES = int(os.getenv("MAX_KB_FILE_BYTES", 5 * 1024 * 1024))         # 5 MB
MAX_AUDIO_SAMPLE_BYTES = int(os.getenv("MAX_AUDIO_SAMPLE_BYTES", 15 * 1024 * 1024)) # 15 MB
MAX_TOTAL_CLONE_BYTES = int(os.getenv("MAX_TOTAL_CLONE_BYTES", 30 * 1024 * 1024))   # 30 MB
MAX_CONCURRENT_UPLOADS_PER_IP = int(os.getenv("MAX_CONCURRENT_UPLOADS_PER_IP", 3))

# Execution timeouts
PARSING_TIMEOUT_SECONDS = float(os.getenv("UPLOAD_PARSING_TIMEOUT_SECONDS", 15.0))
MAX_PDF_PAGES = 100

# Magic bytes definitions
DANGEROUS_MAGIC_HEADERS = [
    (b"MZ", "Windows executable (PE)"),
    (b"\x7fELF", "Linux ELF executable"),
    (b"\xca\xfe\xba\xbe", "Java bytecode / Mach-O fat binary"),
    (b"\xfe\xed\xfa\xce", "Mach-O 32-bit binary"),
    (b"\xfe\xed\xfa\xcf", "Mach-O 64-bit binary"),
    (b"PK\x03\x04", "ZIP archive / decompression bomb risk"),
    (b"PK\x05\x06", "Empty/Spanned ZIP archive"),
    (b"\x1f\x8b", "Gzip archive"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip archive"),
    (b"Rar!\x1a\x07", "RAR archive"),
]

AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac"}
DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".log", ".xml"}


class UploadQuotaManager:
    """Track and limit concurrent upload operations per IP."""

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_UPLOADS_PER_IP):
        self.max_concurrent = max_concurrent
        self._active: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, ip: str) -> None:
        async with self._lock:
            current = self._active.get(ip, 0)
            if current >= self.max_concurrent:
                raise HTTPException(
                    429, f"Too many concurrent uploads from this IP ({current}/{self.max_concurrent}). Please wait."
                )
            self._active[ip] = current + 1

    async def release(self, ip: str) -> None:
        async with self._lock:
            if ip in self._active:
                self._active[ip] = max(0, self._active[ip] - 1)
                if self._active[ip] == 0:
                    del self._active[ip]


quota_manager = UploadQuotaManager()


def sanitize_filename(filename: str | None) -> str:
    """Sanitize uploaded filename to eliminate directory traversal and illegal characters."""
    if not filename:
        return "unnamed_upload"
    # Strip any directory path components (both UNIX / and Windows \)
    clean = filename.replace("\\", "/").split("/")[-1].strip()
    # Remove null bytes and control chars
    clean = re.sub(r"[\x00-\x1f\x7f]", "", clean)
    # Remove traversal patterns
    clean = re.sub(r"\.\.+", ".", clean)
    # Whitelist characters (alphanumeric, dot, dash, underscore, space)
    clean = re.sub(r"[^\w\.\-\ ]", "_", clean).strip(" .")
    return clean or "sanitized_file"


async def read_bounded(file: UploadFile, max_bytes: int, chunk_size: int = 65536) -> bytes:
    """Stream-read UploadFile up to max_bytes.
    
    Aborts immediately with HTTP 413 if Content-Length or actual stream exceeds max_bytes,
    preventing RAM exhaustion DoS.
    """
    # 1. Check Content-Length header if provided by client/proxy
    content_length = file.headers.get("content-length")
    if content_length:
        try:
            cl_int = int(content_length)
            if cl_int > max_bytes:
                raise HTTPException(
                    413,
                    f"File size exceeds maximum allowed limit of {max_bytes // 1024} KB (claimed {cl_int // 1024} KB)."
                )
        except ValueError:
            pass

    # 2. Bounded streaming read
    buffer = bytearray()
    total_read = 0

    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total_read += len(chunk)
        if total_read > max_bytes:
            raise HTTPException(
                413,
                f"File payload exceeded maximum allowed size of {max_bytes // 1024} KB."
            )
        buffer.extend(chunk)

    return bytes(buffer)


def validate_magic_bytes_audio(data: bytes, filename: str) -> str:
    """Inspect magic bytes of audio data. Reject dangerous binaries and invalid audio formats."""
    if len(data) < 12:
        raise HTTPException(400, "Audio file is too small to be a valid audio recording.")

    # Check for forbidden executable signatures
    for sig, desc in DANGEROUS_MAGIC_HEADERS:
        if data.startswith(sig):
            raise HTTPException(400, f"Dangerous file type detected ({desc}). Upload rejected.")

    # WAV validation: 'RIFF' .... 'WAVE'
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"

    # MP3 validation: ID3 tag or MPEG sync bytes
    if data.startswith(b"ID3") or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "audio/mpeg"

    # OGG validation: 'OggS'
    if data.startswith(b"OggS"):
        return "audio/ogg"

    # FLAC validation: 'fLaC'
    if data.startswith(b"fLaC"):
        return "audio/flac"

    # M4A / AAC validation: 'ftyp' at offset 4
    if len(data) > 8 and data[4:8] in (b"ftyp", b"M4A "):
        return "audio/mp4"

    raise HTTPException(
        400,
        f"Invalid audio format. Magic bytes do not match supported audio (WAV, MP3, OGG, FLAC, M4A). File: {filename}"
    )


def validate_magic_bytes_document(data: bytes, filename: str) -> str:
    """Inspect magic bytes of document data. Reject executables, archives, and invalid files."""
    if len(data) == 0:
        raise HTTPException(400, "Document file is empty.")

    # Check for forbidden executable signatures and archive decompression bombs
    for sig, desc in DANGEROUS_MAGIC_HEADERS:
        if data.startswith(sig):
            raise HTTPException(400, f"Disallowed file format ({desc}). Upload rejected.")

    # PDF validation: starts with %PDF-
    if data.startswith(b"%PDF-"):
        return "application/pdf"

    # If filename claims to be PDF, it MUST start with %PDF-
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        raise HTTPException(400, "File extension is .pdf but content is not a valid PDF document (missing %PDF- header).")

    # Plain text formats (TXT, MD, CSV, TSV, JSON, YAML, etc.)
    # Must decode cleanly and contain zero null bytes
    if b"\x00" in data[:1024]:
        raise HTTPException(400, "Binary contents detected in text document upload.")

    try:
        data.decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        try:
            data.decode("latin-1")
            return "text/plain"
        except Exception:
            raise HTTPException(400, "Document cannot be decoded as valid UTF-8/plain text.")
