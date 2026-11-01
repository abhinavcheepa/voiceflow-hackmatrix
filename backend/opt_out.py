"""WhatsApp STOP / Opt-Out compliance handling and suppression table.

Handles:
- Inbound opt-out / unsubscribe keywords in English, Hindi, and Hinglish.
- Inbound resubscribe keywords ("START", "शुरू करो", etc.).
- Global suppression list: checked before every campaign dial and automated reply.
"""

import logging
import re

log = logging.getLogger("voiceflow.opt_out")

# Unambiguous opt-out keywords (whole message match only, no substrings).
# Ambiguous words like 'cancel', 'end', 'halt', 'nahi chahiye', 'rok do' are excluded
# to avoid misinterpreting conversational phrases like "cancel my appointment"
# or "nahi chahiye receipt".
OPTOUT_KEYWORDS = {
    # English
    "stop", "unsubscribe", "quit", "optout", "opt-out",
    # Hinglish
    "band karo", "band kar do", "band kr do", "mat bhejo", "message mat bhejo",
    "msg mat bhejo", "msg mat karo", "kripya band karein", "kripya band karo",
    # Hindi (Devanagari)
    "रुको", "बंद करो", "मत भेजो", "समाप्त", "संदेश मत भेजो", "बंद कर दीजिए", "रोकें"
}

# Unambiguous resubscribe keywords (whole message match only).
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
    """Return True if incoming message strictly matches an unsubscribe / STOP keyword.
    
    Matches the entire normalized message, preventing false positives on sentences
    like 'cancel my appointment' or 'nahi chahiye receipt'.
    """
    clean = _clean_text(text)
    if not clean:
        return False
    return clean in OPTOUT_KEYWORDS


def is_resubscribe_keyword(text: str) -> bool:
    """Return True if incoming message strictly matches a resubscribe / START keyword."""
    clean = _clean_text(text)
    if not clean:
        return False
    return clean in RESUBSCRIBE_KEYWORDS
