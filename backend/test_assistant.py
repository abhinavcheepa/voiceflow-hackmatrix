"""Checks for the owner assistant ("Jarvis" mode): owner-only tools, the
two-turn confirmation before anything is sent, WhatsApp threads handed to an
agent, and who may use the assistant at all.

    python -m pytest test_assistant.py
"""

import asyncio
import os
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.pop("ASSISTANT_PIN", None)

import brain  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
import tools  # noqa: E402
import whatsapp  # noqa: E402

db.init()
sent: list[tuple[str, str]] = []


def _wa(monkeypatch):
    sent.clear()

    async def fake_send(to, body):
        sent.append((to, body))
        return {"ok": True}

    monkeypatch.setattr(whatsapp, "send_text", fake_send)
    monkeypatch.setattr(whatsapp, "configured", lambda: True)


def _run(name, args, ctx):
    out = tools.run(name, args, ctx)
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


def test_phone_numbers_normalise_to_whatsapp_form():
    for raw in ("98123 45678", "+91 98123-45678", "09812345678", "919812345678"):
        assert db.normalize_phone(raw) == "919812345678", raw


def test_customers_never_get_owner_tools(monkeypatch):
    _wa(monkeypatch)
    names = {t["function"]["name"] for t in tools.schemas({"channel": "call"})}
    assert not names & {"send_whatsapp", "send_bill", "start_whatsapp_chat", "recent_activity"}
    # Even if the model invents the call, it does nothing for a customer.
    out = _run("send_whatsapp", {"phone": "9812345678", "message": "hi"}, {"channel": "call"})
    assert "not available" in out and not sent
    assert "send_whatsapp" in {t["function"]["name"] for t in tools.schemas({"owner": True})}


def test_nothing_is_sent_until_the_owner_confirms_in_a_later_turn(monkeypatch):
    _wa(monkeypatch)
    ctx = {"owner": True, "_turn": 1}
    args = {"phone": "98123 45678", "message": "Aapka order ready hai"}

    first = _run("send_whatsapp", args, ctx)
    assert "NOT SENT YET" in first and "91 98123 45678" in first and not sent
    # Same turn again (the model trying to confirm itself): still blocked.
    assert "NOT SENT YET" in _run("send_whatsapp", args, ctx) and not sent

    ctx["_turn"] = 2  # the owner said "haan"
    assert "Bhej diya" in _run("send_whatsapp", args, ctx)
    assert sent == [("919812345678", "Aapka order ready hai")]

    # A changed message is a new action and needs its own yes.
    ctx["_turn"] = 3
    assert "NOT SENT YET" in _run("send_whatsapp", {**args, "message": "Kuch aur"}, ctx)
    assert len(sent) == 1


def test_owner_yes_sends_exactly_what_was_read_back(monkeypatch):
    """The spoken "haan" path: the call loop runs the parked action itself."""
    import webcall

    _wa(monkeypatch)
    ctx = {"owner": True, "_turn": 1}
    _run("send_whatsapp", {"phone": "98123 45678", "message": "Order ready"}, ctx)
    assert not sent and webcall.is_plain_yes("हाँ भेज दो")
    ctx["_turn"] = 2
    done = asyncio.run(tools.approve_pending(ctx))
    assert "Bhej diya" in done and sent == [("919812345678", "Order ready")]
    assert "_pending" not in ctx and asyncio.run(tools.approve_pending(ctx)) is None
    # A "yes" that carries a different number is a correction, not a yes.
    assert not webcall.is_plain_yes("haan par number 98123 45679 hai")


def test_incomplete_number_is_refused(monkeypatch):
    _wa(monkeypatch)
    out = _run("send_whatsapp", {"phone": "98123", "message": "hi"}, {"owner": True, "_turn": 5})
    assert "incomplete" in out and not sent


def test_bill_is_built_finalized_and_sent(monkeypatch):
    _wa(monkeypatch)
    ctx = {"owner": True, "_turn": 1, "channel": "assistant"}
    _run("create_bill", {"customer_name": "Ravi"}, ctx)
    _run("add_bill_item", {"item_name": "Cement bag", "quantity": 2, "rate": 350}, ctx)
    assert "still open" in _run("send_bill", {"phone": "9812345678"}, ctx)
    _run("finalize_bill", {}, ctx)
    assert "NOT SENT YET" in _run("send_bill", {"phone": "9812345678"}, ctx)
    ctx["_turn"] = 2
    assert "Bhej diya" in _run("send_bill", {"phone": "9812345678"}, ctx)
    to, body = sent[0]
    assert to == "919812345678" and "Cement bag × 2" in body and "Total: ₹700" in body


def test_started_chat_is_answered_by_the_chosen_agent_with_its_goal(monkeypatch):
    _wa(monkeypatch)
    collector = db.save_agent(None, name="Collections", prompt="Polite payment follow-ups.",
                              greeting="Namaste", language="Hindi")
    db.set_allowlist(["9000000001"])  # an allowlist that does NOT include the target
    ctx = {"owner": True, "_turn": 1}
    args = {"phone": "98765 43210", "agent_name": "Collections",
            "goal": "collect the pending Rs 4800", "opening_message": "Namaste, payment ke baare mein"}
    assert "NOT SENT YET" in _run("start_whatsapp_chat", args, ctx)
    ctx["_turn"] = 2
    assert "Ho gaya" in _run("start_whatsapp_chat", args, ctx)
    assert sent == [("919876543210", "Namaste, payment ke baare mein")]
    assert db.conversation_brief("919876543210") == {"agent_id": collector["id"],
                                                     "goal": "collect the pending Rs 4800"}
    assert db.auto_reply_block("919876543210") is None  # allowlist was extended

    # Their reply is answered by Collections, steering toward the goal.
    seen = {}

    async def fake_reply(phone, message, prompt=None, **k):
        seen["prompt"] = prompt
        return "Ji, aap payment kab tak kar denge?"

    monkeypatch.setattr(brain, "reply", fake_reply)
    asyncio.run(whatsapp.reply_to("919876543210", "Haan boliye", "Haan boliye", False, "Hinglish"))
    assert "Polite payment follow-ups" in seen["prompt"]
    assert "collect the pending Rs 4800" in seen["prompt"]
    assert sent[-1] == ("919876543210", "Ji, aap payment kab tak kar denge?")
    db.set_allowlist([])


def test_recent_activity_reads_the_business():
    db.add_booking(item="sofa", name="Neha", phone="919811111111")
    assert "sofa" in tools.run("recent_activity", {"kind": "bookings"}, {"owner": True})


class FakeWS:
    def __init__(self, host, headers=None, first=None):
        self.client = type("C", (), {"host": host})()
        self.headers = headers or {}
        self._first = first

    async def receive_json(self):
        if self._first is None:
            raise RuntimeError("closed")
        return self._first


def test_assistant_access(monkeypatch):
    ok = lambda ws: asyncio.run(main._owner_ok(ws))  # noqa: E731
    assert ok(FakeWS("127.0.0.1"))                                       # this computer
    assert ok(FakeWS("127.0.0.1", {"x-forwarded-for": "::1"}))           # via local Vite
    assert not ok(FakeWS("127.0.0.1", {"x-forwarded-for": "192.168.1.9"}))  # phone, no PIN set
    # Someone hitting the port directly can't claim to be local.
    assert not ok(FakeWS("192.168.1.9", {"x-forwarded-for": "127.0.0.1"}))

    monkeypatch.setenv("ASSISTANT_PIN", "4321")
    phone = {"x-forwarded-for": "192.168.1.9"}
    assert ok(FakeWS("127.0.0.1", phone, {"type": "auth", "pin": "4321"}))
    assert not ok(FakeWS("127.0.0.1", phone, {"type": "auth", "pin": "0000"}))
    assert not ok(FakeWS("127.0.0.1", phone, None))


def test_send_by_name_finds_the_saved_contact(monkeypatch):
    """'लाविम भय' is how speech-to-text heard 'Lavish bhai'."""
    _wa(monkeypatch)
    db.touch_contact("919301012363", "Lavish")
    db.touch_contact("919300000000", "Ramesh")
    assert tools.find_contacts("लाविम भय")[0][1:] == ("Lavish", "919301012363")
    assert tools.find_contacts("Lavish bhai")[0][2] == "919301012363"
    assert tools.find_contacts("Zubair") == []

    ctx = {"owner": True, "_turn": 1}
    ask = _run("send_whatsapp", {"phone": "लाविम भय", "message": "Test message"}, ctx)
    assert "NOT SENT YET" in ask and "Lavish (91 93010 12363)" in ctx["_pending"]["ask"]
    ctx["_turn"] = 2
    assert "Bhej diya" in asyncio.run(tools.approve_pending(ctx))
    assert sent == [("919301012363", "Test message")]


def test_unknown_name_asks_for_the_number_then_remembers_it(monkeypatch):
    _wa(monkeypatch)
    ctx = {"owner": True, "_turn": 1}
    out = _run("send_whatsapp", {"phone": "Pinky didi", "message": "hi"}, ctx)
    assert "No saved contact" in out and not sent
    # Owner gives the number; the model passes number + name.
    _run("send_whatsapp", {"phone": "98111 22233", "name": "Pinky didi", "message": "hi"}, ctx)
    ctx["_turn"] = 2
    asyncio.run(tools.approve_pending(ctx))
    assert sent == [("919811122233", "hi")]
    assert tools.find_contacts("pinky")[0][2] == "919811122233"  # name alone works next time


def test_agent_cannot_claim_a_send_that_did_not_happen():
    import webcall

    assert webcall.SENT_CLAIM_RE.search("संदेश भेज दिया गया है।")
    assert not webcall.SENT_CLAIM_RE.search("Kise bhejna hai?")
    s = webcall.Session(assistant=True)
    for i in range(12):  # a long back-and-forth
        s.add("user", f"line {i}" if i else "number 93010 12363 hai")
        s.add("assistant", f"ok {i}")
    assert "93010 12363" in s.prompt  # the number survives past the window
    assert len(s.history) == 20


def test_a_number_the_owner_never_said_is_refused(monkeypatch):
    _wa(monkeypatch)
    ctx = {"owner": True, "_turn": 1, "_heard_numbers": ["919811122233"]}
    out = _run("send_whatsapp", {"phone": "98222 33344", "message": "hi"}, ctx)
    assert "never said" in out and "_pending" not in ctx
    assert "NOT SENT YET" in _run("send_whatsapp", {"phone": "98111 22233", "message": "hi"}, ctx)
    assert db.normalize_phone("९८१११ २२२३३") == "919811122233"


def test_speech_cleanup_matches_what_the_voice_misread():
    import voice

    say = voice.speakable
    assert say("Naam batayein.Ji, bataiye.(Waiting for your details).") == "Naam batayein. Ji, bataiye."
    assert say("Bill BILL-0001, ₹700, Ravi ko bhejun?") == "Bill 1, 700 rupaye, Ravi ko bhejun?"
    assert say("Spelling V-O-I-C-E hai, VICE FLOW nahi") == "Spelling Voice hai, Vice Flow nahi"
    assert say("GST 18% aur AI, 4.5 kg") == "GST 18% aur AI, 4.5 kg"  # acronyms and decimals kept
    assert say("जल्दी जल्दी भेजो।ठीक") == "जल्दी जल्दी भेजो। ठीक"


def test_primed_whisper_reciting_its_vocab_is_dropped():
    vocab = "VoiceFlow AI, WhatsApp, Kanishk, lavin"
    assert brain._echoes_vocab("VoiceFlow AI, WhatsApp, Kanishk", vocab)
    assert not brain._echoes_vocab("Kanishk ko message bhejo", vocab)


def test_auto_reply_settings_change_settings_and_send_nothing(monkeypatch):
    _wa(monkeypatch)
    db.set_allowlist(["9000000001"])
    ctx = {"owner": True, "_turn": 1}
    out = _run("auto_reply_settings", {"action": "allow", "phone": "88178 82519"}, ctx)
    assert "Ho gaya" in out and not sent
    assert db.auto_reply_block("918817882519") is None
    _run("auto_reply_settings", {"action": "block", "phone": "88178 82519"}, ctx)
    assert db.auto_reply_block("918817882519") is not None
    assert db.get_allowlist()  # never emptied — empty would mean "reply to everyone"
    assert "Only these numbers" in _run("auto_reply_settings", {"action": "status"}, ctx)
    db.set_allowlist([])
