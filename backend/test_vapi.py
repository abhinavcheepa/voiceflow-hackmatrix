"""Checks for the Vapi assistant config — no key or network needed.

The webhook consumer (main._save_report, main._run_tools) was already written to
read analysis.structuredData and to answer a book_appointment tool call, but the
assistant() config never SENT either, so both sat dead. These tests lock the
contract between what the assistant asks Vapi to produce and what the webhook
knows how to consume — break one side and a test fails.

    python -m pytest test_vapi.py
"""

import os
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

import db  # noqa: E402
import vapi  # noqa: E402

db.init()


def test_assistant_requests_call_analysis():
    """No analysisPlan means structuredData is always empty and the dashboard
    records nothing useful. It must be sent."""
    cfg = vapi.assistant()
    assert "analysisPlan" in cfg, "assistant must request call analysis"
    assert cfg["analysisPlan"]["structuredDataSchema"]["properties"], "schema must have fields"


def test_structured_data_keys_match_what_the_webhook_stores():
    """The webhook's _save_report reads these exact keys off structuredData. If
    the schema stops offering one, that field silently becomes null forever."""
    props = vapi.assistant()["analysisPlan"]["structuredDataSchema"]["properties"]
    for key in ("name", "language", "intent", "outcome"):
        assert key in props, f"_save_report reads structuredData[{key!r}] — schema must define it"


def test_book_appointment_tool_is_declared():
    """The webhook answers a book_appointment call, but Vapi only makes that call
    if the tool is declared, with a 'slot' argument _run_tools reads."""
    fns = [
        t["function"] for t in vapi.assistant()["model"]["tools"]
        if t.get("type") == "function"
    ]
    book = next((f for f in fns if f["name"] == "book_appointment"), None)
    assert book, "book_appointment tool must be declared"
    assert "slot" in book["parameters"]["properties"], "_run_tools reads args['slot']"


def test_transfer_tool_only_when_a_number_exists():
    """Offering a transfer with no destination is a dead tool. It appears only
    when OWNER_PHONE is set."""
    saved = vapi.OWNER_PHONE
    try:
        vapi.OWNER_PHONE = ""
        types = [t["type"] for t in vapi.assistant()["model"]["tools"]]
        assert "transferCall" not in types

        vapi.OWNER_PHONE = "+919000000000"
        tools = vapi.assistant()["model"]["tools"]
        transfer = next((t for t in tools if t["type"] == "transferCall"), None)
        assert transfer, "transferCall must appear once a number is set"
        assert transfer["destinations"][0]["number"] == "+919000000000"
    finally:
        vapi.OWNER_PHONE = saved


def test_call_is_bounded_and_denoised():
    """A runaway call bills forever; a noisy line transcribes badly. Both are
    guarded in config."""
    cfg = vapi.assistant()
    assert cfg["maxDurationSeconds"] == vapi.MAX_CALL_SECONDS
    assert cfg["backgroundDenoisingEnabled"] is True
    assert cfg["endCallPhrases"], "end-call phrases let the caller hang up naturally"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all passed")
