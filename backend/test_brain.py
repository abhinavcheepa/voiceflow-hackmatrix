"""Checks for the parts that are pure logic — no keys or network needed.

    python test_brain.py

Several of these exist because the bug happened in production. Read the
docstring before "simplifying" one away.
"""

import asyncio
import os
import tempfile

import httpx

# Point the database somewhere disposable before db is imported — these tests
# write rows, and they must never touch the real voiceflow.db.
_TMP_DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_PATH"] = _TMP_DB

import brain  # noqa: E402
import db  # noqa: E402
import tools  # noqa: E402
import whatsapp  # noqa: E402


def test_detect_language():
    cases = [
        ("Namaste, kya aaj shaam ka slot mil sakta hai?", "Hinglish"),
        ("Namaste! Aaj shaam ka slot check karke batata hoon.", "Hinglish"),
        ("Theek hai, bhej dijiye", "Hinglish"),
        ("Do you have the new collection in stock?", "English"),
        ("The main entrance is closed for repairs today.", "English"),
        ("Please confirm my appointment for tomorrow morning.", "English"),
        ("उद्या सकाळी येऊ शकतो का?", "Marathi"),
        ("क्या आज शाम का स्लॉट मिलेगा?", "Hindi"),
        ("ஆர்டர் எப்போ வரும்?", "Tamil"),
    ]
    for text, expected in cases:
        got = brain.detect_language(text)
        assert got == expected, f"{text!r} -> {got}, expected {expected}"


def test_drops_whisper_hallucinations():
    """Whisper answers silence with stock subtitle phrases; never reply to those."""
    for junk in [
        "Thank you.", "you", "Thanks for watching!", "Bye.", " okay ", "ok",
        "धन्यवाद", "a",
        "I'm going to go to the next episode of the episode of the episode of the",
    ]:
        assert brain._clean(junk) == "", f"should have dropped {junk!r}"


def test_keeps_real_speech():
    for real in [
        "kya aaj shaam ka slot khaali hai?",
        "Thank you, aur cleaning ka charge kitna hai?",   # starts like junk, isn't
        "नमस्ते, आज शाम का स्लॉट मिलेगा?",
        "Do you have an appointment tomorrow?",
    ]:
        assert brain._clean(real) == real.strip(), f"should have kept {real!r}"


def test_parse_ignores_status_receipts():
    """Delivery/read receipts arrive on the same webhook and must not reply."""
    receipts = {"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]}
    assert whatsapp.parse(receipts) == []


def test_parse_text_and_voice():
    payload = {
        "entry": [{"changes": [{"value": {
            "contacts": [{"wa_id": "919911087000", "profile": {"name": "Priya"}}],
            "messages": [
                {"from": "919911087000", "type": "text", "text": {"body": "hello"}},
                {"from": "919911087000", "type": "audio", "audio": {"id": "MEDIA-1"}},
            ],
        }}]}]
    }
    text, audio = whatsapp.parse(payload)
    assert text["name"] == "Priya" and text["text"] == "hello"
    assert audio["media_id"] == "MEDIA-1" and audio["type"] == "audio"


def test_error_description_is_never_empty():
    """httpx timeouts stringify to "", which once produced the useless
    "[auto-reply failed: ]" on a customer's thread. Always name the type."""
    import httpx

    for err in [httpx.ReadTimeout(""), httpx.ConnectTimeout(""), Exception(), ValueError("")]:
        described = whatsapp.describe(err)
        assert described.strip(), f"{type(err).__name__} produced an empty description"
        assert type(err).__name__ in described

    # A real message must still survive.
    assert "boom" in whatsapp.describe(RuntimeError("boom"))


def test_unanswered_message_is_queued_then_cleared():
    """A reply runs as a background task, so a crash between "stored" and
    "sent" used to lose it silently. needs_reply is what survives that."""
    db.init()
    phone = "919000000042"  # unique to this test — the temp DB is shared

    mid = db.add_message(phone, "them", "text", "kya khula hai?", needs_reply=True)
    assert mid, "add_message should return the new row id"

    queued = [r for r in db.pending_replies() if r["phone"] == phone]
    assert len(queued) == 1, "an unanswered message must be picked up"
    assert queued[0]["body"] == "kya khula hai?"

    db.clear_needs_reply(mid)
    assert not [r for r in db.pending_replies() if r["phone"] == phone], (
        "a message must not be answered twice"
    )


def test_pending_replies_ignores_old_messages():
    """Answering yesterday's message is worse than not answering it."""
    from datetime import datetime, timedelta, timezone

    db.init()
    phone = "919000000002"
    old = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    db.add_message(phone, "them", "text", "purana message", needs_reply=True, at=old)

    assert not [r for r in db.pending_replies() if r["phone"] == phone]


def test_reply_needs_all_three_switches():
    """Global, allowlist and per-thread must all agree before the agent speaks —
    this is what keeps a personal number from answering friends."""
    db.init()
    phone = "919000000003"
    db.add_message(phone, "them", "text", "hi")

    db.set_allowlist([])
    db.set_auto_reply_global(True)
    db.set_auto_reply_for(phone, True)
    assert db.should_auto_reply(phone), "empty allowlist means everyone"

    db.set_auto_reply_global(False)
    assert not db.should_auto_reply(phone), "global off must silence everyone"

    db.set_auto_reply_global(True)
    db.set_allowlist(["9198000000099"])
    assert not db.should_auto_reply(phone), "a number outside the allowlist must stay silent"

    db.set_allowlist([phone])
    assert db.should_auto_reply(phone), "an allowlisted number must be answered"

    db.set_auto_reply_for(phone, False)
    assert not db.should_auto_reply(phone), "per-thread off must win"

    db.set_allowlist([])  # leave the temp db tidy for the next test


def test_allowlist_matches_any_number_format():
    """The same person arrives as 9812345678, 919812345678 and +91 98123 45678
    depending on where the number came from."""
    db.init()
    db.set_auto_reply_global(True)
    db.set_allowlist(["9000000001"])
    for form in ("9000000001", "919000000001", "+91 90000 00001", "91-90000-00001"):
        assert db.allowed(form), f"{form} should match the allowlist"
    assert not db.allowed("919999999999")
    db.set_allowlist([])


def test_a_call_can_be_ended_after_it_was_opened():
    """Ending a call supplies no phone, and phone is NOT NULL.

    INSERT .. ON CONFLICT built the insert row first and died there, inside a
    WebSocket `finally` where nothing surfaced it. Every web call stayed
    'active' with no duration or transcript, and the dashboard read 0 calls.
    """
    db.init()
    cid = "WEB-TESTCALL"
    db.upsert_call(cid, id=cid, phone="Web call", status="active",
                   started_at="2026-01-01T00:00:00+00:00")

    db.upsert_call(cid, status="ended", duration_sec=42, intent="Web call",
                   outcome="Answered", transcript="Caller: hi")

    with db.connect() as conn:
        row = dict(conn.execute(
            "SELECT id, status, duration_sec, transcript, started_at FROM calls "
            "WHERE vapi_call_id = ?", (cid,)).fetchone())

    assert row["status"] == "ended", row
    assert row["duration_sec"] == 42, row
    assert row["transcript"] == "Caller: hi", row
    assert row["id"] == cid, "the primary key must not be rewritten on update"
    # An update must not redate the call to the moment it ended.
    assert row["started_at"] == "2026-01-01T00:00:00+00:00", row


def test_blocked_reply_says_which_switch_stopped_it():
    """Three switches can each stop a reply. Silence about which one is the bug.

    "WhatsApp isn't replying" cost hours more than once because nothing in the
    log distinguished the global toggle from the allowlist from the per-thread
    setting.
    """
    db.init()
    phone = "919000000088"

    db.set_auto_reply_global(False)
    db.set_allowlist([])
    assert "globally" in db.auto_reply_block(phone)

    db.set_auto_reply_global(True)
    db.set_allowlist(["9000000001"])
    reason = db.auto_reply_block(phone)
    assert "allowlist" in reason and "9000000001" in reason, reason

    db.set_allowlist([])
    assert db.auto_reply_block(phone) is None, "an allowed number must not be blocked"

    db.add_message(phone, "them", "text", "hi")
    db.set_auto_reply_for(phone,False)
    assert "this conversation" in db.auto_reply_block(phone)

    db.set_auto_reply_for(phone,True)
    assert db.auto_reply_block(phone) is None


def test_history_excludes_failed_replies():
    """A failure note is not something the agent said — never feed it back.

    Left in, the model reads '[auto-reply failed: …]' as its own previous turn,
    and it costs tokens against a per-minute limit that is already the thing
    causing the failures.
    """
    db.init()
    phone = "919000000077"  # unique to this test — the temp DB is shared
    db.add_message(phone, "them", "text", "Kitne ka hai?")
    db.add_message(phone, "us", "text", "[auto-reply failed: 429]", failed=True)
    db.add_message(phone, "them", "text", "Hello?")

    bodies = [m["content"] for m in db.get_history(phone)]
    assert bodies == ["Kitne ka hai?", "Hello?"], bodies


def _stub_client(responses):
    """An httpx client that replays `responses` in order, no network involved."""
    queue = iter(responses)
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda _req: next(queue)))


def test_rate_limit_is_waited_out_not_given_up_on():
    """A Groq 429 clears in seconds. Losing a customer's reply to it is a bug.

    This happened live: one 429 during a burst turned into '[auto-reply failed]'
    and the customer waited two minutes for the sweep to try again.
    """
    responses = [
        httpx.Response(429, headers={"retry-after": "0"},
                       json={"error": {"message": "Rate limit reached."}}),
        httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]}),
    ]

    async def go():
        async with _stub_client(responses) as client:
            r = await brain._post(client, "https://stub/chat/completions")
            return r.json()

    assert asyncio.run(go())["choices"][0]["message"]["content"] == "hi"


def test_rate_limit_error_names_the_actual_limit():
    """A bare '429 Too Many Requests' says nothing. Keep the provider's reason."""
    reason = "Rate limit reached for model `llama-3.3-70b-versatile` on tokens per minute"
    responses = [
        httpx.Response(429, headers={"retry-after": "0"}, json={"error": {"message": reason}})
        for _ in range(brain.RATE_LIMIT_TRIES)
    ]

    async def go():
        async with _stub_client(responses) as client:
            await brain._post(client, "https://stub/chat/completions")

    try:
        asyncio.run(go())
    except RuntimeError as e:
        assert reason in str(e), f"error lost the reason: {e}"
    else:
        raise AssertionError("exhausting the retries must raise")


def test_rate_limit_wait_comes_from_the_provider():
    body = {"error": {"message": "Limit 6000, Used 6000. Please try again in 5.04s."}}
    assert brain._rate_limit_wait(httpx.Response(429, json=body)) == 5.54
    # An explicit header wins over parsing prose out of the body.
    assert brain._rate_limit_wait(httpx.Response(429, headers={"retry-after": "3"}, json=body)) == 3
    # Never sleep forever because a provider asked for an hour.
    assert brain._rate_limit_wait(
        httpx.Response(429, headers={"retry-after": "3600"})
    ) == brain.MAX_BACKOFF_SECONDS


def test_empty_reply_is_an_error_not_silence():
    """A reasoning model can spend all of max_tokens thinking and return "".

    That empty string reached TTS, which produced no audio, and the caller
    simply heard nothing — reported as "the agent randomly stops responding".
    """
    responses = [httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})]

    async def go():
        async with _stub_client(responses) as client:
            brain.http = lambda: client  # noqa: E731 - swap the pool for the stub
            return await brain.reply("919000000099", "hello", history=[], prompt="p")

    real_http, real_key = brain.http, brain.CHAT_API_KEY
    brain.CHAT_API_KEY = "test-key"  # no network happens; the stub answers
    try:
        asyncio.run(go())
    except RuntimeError as e:
        assert "empty reply" in str(e), e
    else:
        raise AssertionError("an empty completion must raise, not return ''")
    finally:
        brain.http, brain.CHAT_API_KEY = real_http, real_key


def test_retired_model_error_says_the_model_is_gone():
    """Groq retired llama-3.3-70b-versatile and every reply began 404ing.

    A bare "404 Not Found" reads like a wrong URL, which is where the time went.
    The message must name the model and how to find a live one.
    """
    responses = [httpx.Response(404, json={"error": {"message": "model not found"}})]

    async def go():
        async with _stub_client(responses) as client:
            await brain._post(client, "https://stub/chat/completions")

    try:
        asyncio.run(go())
    except RuntimeError as e:
        assert brain.CHAT_MODEL in str(e), e
        assert "/models" in str(e), "must say how to list the live models"
    else:
        raise AssertionError("a 404 on chat/completions must raise a clear error")


def test_non_429_errors_still_raise_immediately():
    """Only rate limits are retried — a bad key must fail loudly, not four times."""
    calls = []

    def handler(_req):
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await brain._post(client, "https://stub/chat/completions")

    try:
        asyncio.run(go())
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("a 401 must raise")
    assert len(calls) == 1, f"401 was retried {len(calls)} times"


def test_chat_fails_over_to_the_next_provider():
    """A dead primary (retired model, 404) must not drop the reply — the next
    provider answers. This is the Groq 404 outage, survived instead of fatal."""
    responses = [
        httpx.Response(404, json={"error": {"message": "model decommissioned"}}),
        httpx.Response(200, json={"choices": [{"message": {"content": "backup answered"}}]}),
    ]

    async def go():
        async with _stub_client(responses) as client:
            brain.http = lambda: client  # noqa: E731
            return await brain._chat_once([{"role": "user", "content": "hi"}], with_tools=False)

    saved = (brain.http, brain.CHAT_API_KEY, brain.FALLBACK_BASE_URL,
             brain.FALLBACK_API_KEY, brain.FALLBACK_MODEL)
    brain.CHAT_API_KEY = "primary-key"
    brain.FALLBACK_BASE_URL = "https://backup.example/v1"
    brain.FALLBACK_API_KEY = "backup-key"
    brain.FALLBACK_MODEL = "backup-model"
    try:
        msg = asyncio.run(go())
    finally:
        (brain.http, brain.CHAT_API_KEY, brain.FALLBACK_BASE_URL,
         brain.FALLBACK_API_KEY, brain.FALLBACK_MODEL) = saved

    assert msg["content"] == "backup answered"


def test_chat_providers_lists_primary_then_fallbacks():
    """The order is what matters: primary first, then the same-key Groq model,
    then any external provider."""
    saved = (brain.CHAT_API_KEY, brain.GROQ_KEY, brain.CHAT_MODEL,
             brain.CHAT_FALLBACK_MODEL, brain.FALLBACK_BASE_URL,
             brain.FALLBACK_API_KEY, brain.FALLBACK_MODEL)
    brain.CHAT_API_KEY = brain.GROQ_KEY = "gk"
    brain.CHAT_MODEL = "openai/gpt-oss-120b"
    brain.CHAT_FALLBACK_MODEL = "openai/gpt-oss-20b"
    brain.FALLBACK_BASE_URL = "https://nim.example/v1"
    brain.FALLBACK_API_KEY = "nk"
    brain.FALLBACK_MODEL = "meta/llama-3.3-70b"
    try:
        models = [p["model"] for p in brain.chat_providers()]
    finally:
        (brain.CHAT_API_KEY, brain.GROQ_KEY, brain.CHAT_MODEL,
         brain.CHAT_FALLBACK_MODEL, brain.FALLBACK_BASE_URL,
         brain.FALLBACK_API_KEY, brain.FALLBACK_MODEL) = saved

    assert models == ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "meta/llama-3.3-70b"]


def test_tool_run_books_a_real_row():
    """book_appointment must write an actual booking, not just echo text."""
    db.init()
    before = len(db.get_bookings())
    out = tools.run("book_appointment",
                    {"item": "study table", "when": "kal shaam"},
                    {"name": "Raghav", "phone": "919000000055", "channel": "whatsapp"})
    assert "saved" in out.lower()
    rows = db.get_bookings()
    assert len(rows) == before + 1
    assert rows[0]["item"] == "study table" and rows[0]["name"] == "Raghav"


def test_tool_run_unknown_tool_is_graceful():
    assert "not available" in tools.run("teleport", {}, {}).lower()


def test_reply_executes_a_tool_then_answers():
    """The full loop: model asks to book, we run it, model confirms in words —
    and the booking is really in the database afterwards."""
    db.init()
    before = len(db.get_bookings())
    tool_msg = {
        "choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "book_appointment",
                             "arguments": '{"item":"office chair","when":"Monday"}'},
            }],
        }}]
    }
    final_msg = {"choices": [{"message": {"role": "assistant",
                                          "content": "Ho gaya, office chair Monday ke liye note kar liya."}}]}
    responses = [httpx.Response(200, json=tool_msg), httpx.Response(200, json=final_msg)]

    async def go():
        async with _stub_client(responses) as client:
            brain.http = lambda: client  # noqa: E731
            return await brain.reply("919000000056", "chair chahiye Monday ko",
                                     history=[], prompt="p",
                                     tools_ctx={"phone": "919000000056", "channel": "whatsapp"})

    real_http, real_key = brain.http, brain.CHAT_API_KEY
    brain.CHAT_API_KEY = "test-key"
    try:
        answer = asyncio.run(go())
    finally:
        brain.http, brain.CHAT_API_KEY = real_http, real_key

    assert "office chair" in answer.lower()
    rows = db.get_bookings()
    assert len(rows) == before + 1, "the tool round must have written a booking"
    assert rows[0]["item"] == "office chair"


def test_extract_json_tolerates_prose_around_the_object():
    assert brain._extract_json('{"a": 1}') == {"a": 1}
    assert brain._extract_json('Sure!\n{"a": 1}\nHope that helps') == {"a": 1}
    assert brain._extract_json("no json here") == {}


def test_analyse_call_skips_empty_transcript_without_calling_out():
    """No transcript, no network — analysis of nothing must be a cheap no-op."""
    assert asyncio.run(brain.analyse_call("")) == {}


def test_analyse_call_keeps_only_known_fields():
    """The model may return extra or wrong-typed keys; only the storable ones,
    correctly typed, survive to the database."""
    payload = {
        "summary": "Caller asked about a study table.",
        "name": "Raghav", "intent": "price enquiry", "item": "study table",
        "outcome": "Answered", "success": True,
        "sentiment": "positive",   # spurious — must be dropped
        "confidence": 0.9,          # spurious — must be dropped
    }
    import json
    responses = [httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})]

    async def go():
        async with _stub_client(responses) as client:
            brain.http = lambda: client  # noqa: E731
            return await brain.analyse_call("Caller: table kitne ka? Agent: 4800.")

    real_http, real_key = brain.http, brain.CHAT_API_KEY
    brain.CHAT_API_KEY = "test-key"
    try:
        out = asyncio.run(go())
    finally:
        brain.http, brain.CHAT_API_KEY = real_http, real_key

    assert set(out) == {"summary", "name", "intent", "item", "outcome", "success"}
    assert out["success"] is True
    assert "sentiment" not in out and "confidence" not in out


def test_facts_block_forbids_invented_numbers_when_empty():
    """No facts must not mean silence about the rule — the blank is exactly where
    the model invented prices. The empty block still carries the instruction."""
    out = brain.facts_block("")
    assert "never invent" in out.lower()
    assert "confirm" in out.lower()


def test_facts_block_scopes_quotes_to_the_facts():
    facts = "Study table: 4800. Chair: 1200. Open 10am-7pm."
    out = brain.facts_block(facts)
    assert "4800" in out
    assert "only" in out.lower(), "must tell the model these are the ONLY quotable facts"


def test_system_prompt_carries_agent_facts():
    """The WhatsApp path must inject the selected agent's facts, or replies quote
    prices from nowhere."""
    db.init()
    db.save_agent(None, name="Test Furniture", prompt="Be helpful.",
                  greeting="Namaste", facts="Sofa: 12000. Bed: 9000.", is_default=True)
    prompt = brain.system_prompt()
    assert "12000" in prompt and "BUSINESS FACTS" in prompt


def test_estimate_seconds_never_zero():
    assert whatsapp._estimate_seconds("ok") == 1
    assert whatsapp._estimate_seconds(" ".join(["word"] * 25)) == 10


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all passed")
