"""Adaptive learning — the agent gets smarter with every conversation.

After each call or chat, the transcript is analysed to extract question-answer
pairs that the business actually handled. These are stored as `learned_answers`
with a `pending` status, and once the owner approves them from the dashboard,
they are injected into future prompts as known facts.

The chain for answering an unknown question is:
    1. KB documents (uploaded files)
    2. Learned answers (approved, from past conversations)
    3. Generative AI (LLM generates a best-effort answer from context)
    4. Honest fallback ("I don't know, let me check")

This is the "agentic AI" capability — the agent reasons about what it knows,
tries multiple sources, and only gives up when it truly has nothing.
"""

import json
import logging
import os
import re

import db

log = logging.getLogger("voiceflow.adaptive")

# Minimum turns before a conversation is worth extracting from.
MIN_TURNS_FOR_LEARNING = 2

EXTRACT_SYSTEM = """You review a finished customer conversation and extract useful Q&A pairs.

Return a JSON array of objects, each with:
- "question": the customer's question or request (in their language)
- "answer": the business's response or how it was handled
- "confidence": 0.0 to 1.0 — how certain the answer is correct and reusable

Rules:
- Only extract factual business information (prices, timings, availability, services).
- Skip greetings, small talk, and generic pleasantries.
- Skip anything where the agent said "I'll check" or "let me confirm" — those are NOT answers.
- If no useful Q&A pairs exist, return an empty array [].
- Return ONLY the JSON array, nothing else.

Example output:
[
  {"question": "Aapki dukaan kab tak khuli rehti hai?", "answer": "Humari dukaan subah 9 se shaam 8 baje tak khuli rehti hai.", "confidence": 0.9},
  {"question": "Study table ka price kya hai?", "answer": "Study table 4500 rupaye se shuru hota hai.", "confidence": 0.8}
]
"""


async def extract_learnings(transcript: str, agent_id: int | None = None,
                            language: str = "Hindi") -> list[dict]:
    """Extract Q&A pairs from a finished conversation transcript.

    Runs asynchronously after a call ends — never blocks the caller. Returns the
    list of saved learned_answer records, each in 'pending' status.
    """
    transcript = (transcript or "").strip()
    if not transcript:
        return []

    # Count turns — too short means no real content.
    turns = transcript.count("Caller:") + transcript.count("Customer:")
    if turns < MIN_TURNS_FOR_LEARNING:
        return []

    import brain

    try:
        body = {
            "model": brain.CHAT_MODEL,
            "messages": [
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": f"Language: {language}\n\nTranscript:\n{transcript}"},
            ],
            "temperature": 0,
            "max_tokens": brain.MAX_REPLY_TOKENS,
            "response_format": {"type": "json_object"},
        }
        if brain.REASONING_MODEL:
            body["reasoning_effort"] = os.getenv("REASONING_EFFORT") or "low"

        r = await brain._post(
            brain.http(),
            f"{brain.CHAT_BASE_URL}/chat/completions",
            headers={**brain._chat_headers(), "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        content = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log.warning("learning extraction failed: %s", e)
        return []

    # Parse the JSON array from the response.
    pairs = _parse_pairs(content)
    if not pairs:
        return []

    saved = []
    for pair in pairs:
        q = (pair.get("question") or "").strip()
        a = (pair.get("answer") or "").strip()
        conf = min(max(float(pair.get("confidence", 0.5)), 0.0), 1.0)
        if q and a and len(q) > 5 and len(a) > 5:
            result = db.add_learned_answer(agent_id, q, a,
                                           source="conversation", confidence=conf)
            saved.append(result)

    if saved:
        log.info("extracted %d Q&A pairs from conversation (agent %s)", len(saved), agent_id)
    return saved


def _parse_pairs(text: str) -> list[dict]:
    """Parse a JSON array from model output, tolerating stray prose."""
    text = (text or "").strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        # Some models wrap in {"pairs": [...]} or similar.
        for v in data.values():
            if isinstance(v, list):
                return v
    except (json.JSONDecodeError, AttributeError):
        pass
    # Try to find array in the text.
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return []


def learned_context_block(agent_id: int | None, query: str) -> str:
    """Build a prompt block from approved learned answers relevant to the query.

    Uses simple keyword overlap — the same approach kb.py uses, and for the same
    reason: at a small-business scale, keyword matching is fast and adequate.
    """
    approved = db.approved_answers_for(agent_id)
    if not approved:
        return ""

    query_words = set(re.findall(r"\w+", query.lower()))
    if not query_words:
        return ""

    scored = []
    for item in approved:
        q_words = set(re.findall(r"\w+", item["question"].lower()))
        overlap = len(query_words & q_words)
        if overlap > 0:
            scored.append((overlap, item))

    if not scored:
        return ""

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:3]

    lines = []
    for _, item in top:
        lines.append(f"Q: {item['question']}\nA: {item['answer']}")

    return (
        "LEARNED FROM PAST CONVERSATIONS (use these if relevant — they are "
        "verified business answers):\n" + "\n\n".join(lines)
    )


async def agentic_fallback(query: str, agent_id: int | None = None,
                           agent_context: str = "") -> str | None:
    """Multi-source answer chain — the "agentic AI" capability.

    Tries KB, then learned answers, then generates a new answer from context.
    Returns the answer text, or None if nothing useful could be produced.
    """
    import kb as kb_mod

    # 1. KB search
    kb_hits = kb_mod.search(agent_id, query)
    if kb_hits:
        return None  # KB has it, let the normal reply flow handle it

    # 2. Learned answers (approved)
    approved = db.approved_answers_for(agent_id)
    query_words = set(re.findall(r"\w+", query.lower()))
    for item in approved:
        q_words = set(re.findall(r"\w+", item["question"].lower()))
        overlap = len(query_words & q_words)
        if overlap >= 2:  # At least 2 words match
            return item["answer"]

    # 3. Generative AI — let the LLM try with whatever context it has
    # This is handled by the normal reply chain, so return None to let it proceed
    return None
