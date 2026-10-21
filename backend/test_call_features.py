"""Checks for call recording + text record, cross-call memory, barge-in,
squad handoff and evals.

    python -m pytest test_call_features.py
"""

import asyncio
import os
import subprocess
import tempfile

TMP = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(TMP, "test.db")
os.environ["RECORDINGS_DIR"] = os.path.join(TMP, "rec")

import brain  # noqa: E402
import db  # noqa: E402
import evals  # noqa: E402
import recording  # noqa: E402
import tools  # noqa: E402
import voice  # noqa: E402
import webcall  # noqa: E402

db.init()


def _tone(seconds: float, fmt: str) -> bytes:
    """A real encoded clip, so stitching is tested with real ffmpeg."""
    return subprocess.run(
        [recording.ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i",
         f"sine=frequency=440:duration={seconds}", "-f", fmt, "pipe:1"],
        capture_output=True, check=True,
    ).stdout


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, m):
        self.sent.append(m)

    async def send_bytes(self, b):
        self.sent.append(("audio", len(b)))


def test_every_call_gets_a_stitched_recording_and_a_text_record(monkeypatch):
    async def fake_tts(text, language=None, as_voice=None):
        return _tone(1.0, "mp3")

    async def fake_stt(audio, filename="x", **k):
        return "Study table ka price kya hai?"

    async def fake_reply(*a, **k):
        return "Study table 4800 rupaye ka hai."

    async def fake_analysis(transcript, language="Hindi"):
        assert "Caller: Study table" in transcript  # built from the full log
        return {"summary": "Caller asked the study table price; agent quoted 4800.",
                "name": "Ravi", "intent": "Price enquiry", "outcome": "Answered", "success": True}

    monkeypatch.setattr(voice, "synthesize", fake_tts)
    monkeypatch.setattr(brain, "transcribe", fake_stt)
    monkeypatch.setattr(brain, "reply", fake_reply)
    monkeypatch.setattr(brain, "analyse_call", fake_analysis)

    s = webcall.Session(caller_key="test-caller-0001")
    s.open_record()
    ws = FakeWS()

    async def call():
        await webcall.speak(ws, s, s.greeting)
        await webcall.handle_turn(ws, s, _tone(1.5, "webm"))
        s.close_record()
        await webcall.finalize(s)

    asyncio.run(call())

    mp3 = recording.path(s.id, "mp3")
    assert os.path.exists(mp3) and os.path.getsize(mp3) > 1000
    assert not os.path.exists(os.path.join(recording.REC_DIR, s.id))  # segments cleaned up

    text = open(recording.path(s.id, "txt"), encoding="utf-8").read()
    for expected in ("Call ID   : " + s.id, "Caller    : Ravi", "SUMMARY",
                     "agent quoted 4800", "TRANSCRIPT", "Caller: Study table ka price",
                     "Study table 4800 rupaye ka hai."):
        assert expected in text, expected

    row = next(c for c in db.get_calls() if c["id"] == s.id)
    assert row["recordingUrl"] == f"/api/calls/{s.id}/recording"
    assert row["recordUrl"] == f"/api/calls/{s.id}/record.txt"
    assert row["summary"].startswith("Caller asked")

    # Same browser calls again: the agent remembers.
    again = webcall.Session(caller_key="test-caller-0001")
    assert "their name is Ravi" in again.prompt and "study table price" in again.prompt
    assert "MEMORY" not in webcall.Session(caller_key="someone-else-99").prompt


def test_silent_call_still_gets_a_text_record():
    s = webcall.Session()
    s.open_record()
    s.close_record()
    asyncio.run(webcall.finalize(s))
    text = open(recording.path(s.id, "txt"), encoding="utf-8").read()
    assert "No summary" in text and "(nothing was said)" in text


def test_transcript_keeps_the_whole_call_not_the_prompt_window():
    s = webcall.Session()
    for i in range(10):
        s.add("user", f"question {i}")
        s.add("assistant", f"answer {i}")
    assert len(s.history) == 6
    assert "question 0" in s.transcript() and "answer 9" in s.transcript()


def test_barge_in_marks_the_cut_line():
    s = webcall.Session()
    s.add("assistant", "Hamare paas teen tarah ke table hain, pehla")
    s.interrupted()
    s.interrupted()  # twice must not double the note
    assert s.history[-1]["content"].count("interrupted") == 1
    assert any("interrupted" in text for _, who, text in s.log if who == "—")


def test_recording_paths_reject_traversal():
    for bad in ("../../etc/passwd", "a/b", "", "x" * 65):
        try:
            recording.path(bad, "mp3")
        except ValueError:
            continue
        raise AssertionError(bad)


def test_transfer_tool_only_offered_with_a_squad_and_switches_agent():
    billing = db.save_agent(None, name="Billing", prompt="Handles bills", greeting="Billing desk, boliye.",
                            language="Hindi")
    assert "transfer_to_agent" not in {t["function"]["name"] for t in tools.schemas({})}

    s = webcall.Session()
    squad = s.squad()
    assert any(a["name"] == "Billing" for a in squad)
    ctx = {"squad": squad}
    names = {t["function"]["name"] for t in tools.schemas(ctx)}
    assert "transfer_to_agent" in names

    out = tools.run("transfer_to_agent", {"agent_name": "billing"}, ctx)
    assert "accepted" in out and ctx["_handoff"] == billing["id"]
    assert s.switch_agent(ctx["_handoff"]) and s.agent["name"] == "Billing"
    assert "No such agent" in tools.run("transfer_to_agent", {"agent_name": "Nobody"}, {"squad": squad})


def test_dry_run_never_writes_a_booking():
    before = len(db.get_bookings())
    ctx = {"dry_run": True}
    out = tools.run("book_appointment", {"item": "sofa"}, ctx)
    assert "[simulation]" in out and len(db.get_bookings()) == before
    assert ctx["_tool_log"] == [{"name": "book_appointment", "args": {"item": "sofa"}}]


def test_eval_script_runs_dry_and_is_judged(monkeypatch):
    async def fake_reply(phone, message, history=None, tools_ctx=None, **k):
        tools.run("book_appointment", {"item": "study table"}, tools_ctx)
        return "Booking ho gayi."

    async def fake_chat(messages, with_tools=False):
        assert "Tools called" in messages[-1]["content"] and "book_appointment" in messages[-1]["content"]
        return {"content": '{"pass": true, "reason": "Agent booked the table."}'}

    monkeypatch.setattr(brain, "reply", fake_reply)
    monkeypatch.setattr(brain, "_chat_once", fake_chat)
    before = len(db.get_bookings())

    case_id = db.add_eval_case("Books a table", None, "script", "Study table book karna hai\nKal 5 baje",
                               "", 4, "Agent must book the study table.")
    run = asyncio.run(evals.run_case(db.get_eval_case(case_id)))
    assert run["passed"] and "booked" in run["reason"]
    assert run["transcript"].count("Caller:") == 2
    assert len(db.get_bookings()) == before  # dry run
    assert db.get_eval_cases()[0]["lastRun"]["passed"]


def test_eval_simulation_stops_when_caller_ends(monkeypatch):
    said = iter(["Namaste, sofa ka price?", "Theek hai, dhanyavaad [END]", "never reached"])

    async def fake_chat(messages, with_tools=False):
        if "grade" in messages[0]["content"]:
            return {"content": '{"pass": false, "reason": "No price given."}'}
        return {"content": next(said)}

    async def fake_reply(*a, **k):
        return "Main confirm karke batati hoon."

    monkeypatch.setattr(brain, "_chat_once", fake_chat)
    monkeypatch.setattr(brain, "reply", fake_reply)
    case_id = db.add_eval_case("Sofa price", None, "simulate", "", "Wants sofa price", 6,
                               "Agent quotes a price.")
    run = asyncio.run(evals.run_case(db.get_eval_case(case_id)))
    assert not run["passed"]
    assert "never reached" not in run["transcript"] and "[END]" not in run["transcript"]
    assert run["transcript"].count("Caller:") == 2
