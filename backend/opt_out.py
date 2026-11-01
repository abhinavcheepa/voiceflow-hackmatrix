"""WhatsApp STOP / Opt-Out compliance handling and suppression table.

Handles:
- Inbound opt-out / unsubscribe keywords in English, Hindi, and Hinglish.
- Inbound resubscribe keywords ("START", "शुरू करो", etc.).
- Global suppression list: checked before every campaign dial and automated reply.
"""

import logging
import re

log = logging.getLogger("voiceflow.opt_out")

OPTOUT_KEYWORDS = {
    # English
    "stop", "unsubscribe", "cancel", "end", "quit", "optout", "opt-out", "halt",
    # Hinglish
    "band karo", "band kar do", "band kr do", "mat bhejo", "rok do", "message mat bhejo",
    "msg mat bhejo", "msg mat karo", "nahi chahiye", "kripya band karein", "kripya band karo",
    # Hindi (Devanagari)
    "रुको", "बंद करो", "मत भेजो", "रोक दो", "समाप्त", "संदेश मत भेजो", "बंद कर दीजिए", "रोकें"
}

RESUBSCRIBE_KEYWORDS = {
    # English
    "start", "unstop", "subscribe", "resume",
    # Hinglish
    "shuru karo", "shuru kar do", "chalu karo", "chalu kar do", "phir se shuru karo",
    # Hindi (Devanagari)
    "शुरू करो", "चालू करो", "पुनः प्रारंभ करें", "शुरू करें", "जारी रखें"
}

ACK_OPTOUT_TEXT = (
    "You have been unsubscribed and will not receive further automated messages. Reply START to resubscribe. "
    "/ आपको अनसब्सक्राइब कर दिया गया है। पुनः शुरू करने के लिए START लिखें।"
)

ACK_RESUBSCRIBE_TEXT = (
    "You have successfully resubscribed to messages. How can we help you? "
    "/ आपका स्वागत है! आपको पुनः सब्सक्राइब कर दिया गया है।"
)


def _clean_text(text: str) -> str:
    """Normalize text for keyword comparison."""
    if not text:
        return ""
    t = re.sub(r"[^\w\s\u0900-\u097F]", " ", text.lower())
    return " ".join(t.split())


def is_opt_out_keyword(text: str) -> bool:
    """Return True if incoming message expresses an unsubscribe / STOP intent."""
    clean = _clean_text(text)
    if not clean:
        return False
    if clean in OPTOUT_KEYWORDS:
        return True
    for kw in OPTOUT_KEYWORDS:
        if clean == kw or clean.startswith(kw + " ") or clean.endswith(" " + kw):
            return True
    return False


def is_resubscribe_keyword(text: str) -> bool:
    """Return True if incoming message expresses a resubscribe / START intent."""
    clean = _clean_text(text)
    if not clean:
        return False
    if clean in RESUBSCRIBE_KEYWORDS:
        return True
    for kw in RESUBSCRIBE_KEYWORDS:
        if clean == kw or clean.startswith(kw + " ") or clean.endswith(" " + kw):
            return True
    return False
