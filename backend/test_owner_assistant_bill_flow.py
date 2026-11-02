import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import brain
import db
import tools
import webcall


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    """Use an isolated SQLite database for each test."""
    db_file = tmp_path / "test_bill_flow.db"
    monkeypatch.setattr(db, "DB_PATH", db_file)
    db.init()
    yield


# ============================================================================
# 1. Assistant Loop Tests
# ============================================================================


def test_loop_stops_after_single_llm_call_on_text_response():
    """When the model returns text with no tool call, the turn ends immediately
    and that single message is returned. The LLM is never called again."""
    call_count = 0

    async def fake_chat_once(messages, with_tools=False, is_owner=False):
        nonlocal call_count
        call_count += 1
        return {"role": "assistant", "content": "Kitne ka bill hai?"}

    with patch.object(brain, "_chat_once", side_effect=fake_chat_once):
        ctx = {"owner": True, "channel": "assistant"}
        ans = asyncio.run(brain.reply("9876543210", "Bill bhejna hai Shivam ko", tools_ctx=ctx))

    assert call_count == 1
    assert "Kitne ka bill hai?" in ans


def test_loop_bounds_tool_iterations_at_max_4():
    """Tool-call rounds are capped at 4. If the model keeps emitting tool calls
    without answering, it terminates with a RuntimeError."""
    call_count = 0

    async def fake_chat_once(messages, with_tools=False, is_owner=False):
        nonlocal call_count
        call_count += 1
        # Continually emit a dummy check_stock tool call
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"call_{call_count}",
                    "type": "function",
                    "function": {"name": "check_stock", "arguments": '{"item_name": "pen"}'},
                }
            ],
        }

    with patch.object(brain, "_chat_once", side_effect=fake_chat_once):
        ctx = {"owner": True, "channel": "assistant"}
        with pytest.raises(RuntimeError, match="model kept calling tools without answering"):
            asyncio.run(brain.reply("9876543210", "Bill check karo", tools_ctx=ctx))

    assert call_count == 4


# ============================================================================
# 2. Output Sanitizer Tests
# ============================================================================


def test_sanitizer_exact_transcript():
    """Sanitizer cleans the exact user transcript from the reported bug."""
    raw = (
        "Stationery ka item name, quantity aur rate bata dijiye, fir main bill finalize kar deti hoon."
        "(Waiting for your item details)User hasn't responded yet.Aapka jawab ka intezaar kar rahi hoon."
        "User didn't respond.Stationery ka quantity aur rate kya hai?(Waiting)..."
    )
    cleaned = brain.sanitize_output(raw, is_owner=True)

    assert "(Waiting" not in cleaned
    assert "User hasn't responded" not in cleaned
    assert "intezaar" not in cleaned
    assert "User didn't respond" not in cleaned
    assert "Stationery ka item name, quantity aur rate bata dijiye" in cleaned
    assert len(cleaned.split(".")) <= 3  # at most 2 sentences


def test_sanitizer_deduplicates_sentences_and_strips_reasoning():
    raw = (
        "<think>deliberating about what bill to create</think>"
        "[thinking about phone number]"
        "User: Kitne ka bill hai?\n"
        "Kitne ka bill hai?\n"
        "Assistant: Note: Aapka intezaar hai."
    )
    cleaned = brain.sanitize_output(raw, is_owner=True)
    assert "<think>" not in cleaned
    assert "deliberating" not in cleaned
    assert "[thinking" not in cleaned
    assert "User:" not in cleaned
    assert "Assistant:" not in cleaned
    assert "Note:" not in cleaned
    assert cleaned == "Kitne ka bill hai?"


def test_sanitizer_preserves_masked_phone():
    text = "Bill BILL-0002, 1000 rupaye, Shivam Gupta (98****3210) ko bhejun?"
    cleaned = brain.sanitize_output(text, is_owner=True)
    assert "98****3210" in cleaned
    assert "Bill BILL-0002" in cleaned


def test_assistant_temperature_and_max_tokens():
    provider = {"model": "openai/gpt-oss-120b"}
    body_owner = brain._chat_body(provider, [], with_tools=False, is_owner=True)
    assert body_owner["temperature"] == 0.2
    assert body_owner["max_tokens"] <= 180

    body_customer = brain._chat_body(provider, [], with_tools=False, is_owner=False)
    assert body_customer["temperature"] == 0.6
    assert body_customer["max_tokens"] >= 400


# ============================================================================
# 3. Bill Flow & Tools Tests
# ============================================================================


def test_no_bill_created_without_amount_or_item():
    """Rule (a): Do not create a bill until amount or items exist.
    If owner only says 'bill bhejna hai', ask 'Kitne ka bill hai?'."""
    ctx = {"owner": True, "channel": "assistant"}
    res = tools.run("create_bill", {}, ctx)
    assert "Amount missing" in res
    assert "Kitne ka bill hai?" in res
    assert db.get_bills() == []


def test_no_bill_to_send_without_active_bill():
    """send_bill must not fall back to an arbitrary old bill."""
    ctx = {"owner": True, "channel": "assistant"}
    res = asyncio.run(tools.run("send_bill", {"name": "Shivam", "phone": "9876543210"}, ctx))
    assert "No bill has been created yet" in res
    assert "Kitne ka bill hai?" in res


def test_spoken_total_and_update_bill_same_bill_id():
    """Rule (b) & (c): Spoken total is enough, and update_bill edits the draft
    without creating a new BILL."""
    ctx = {"owner": True, "channel": "assistant"}

    # Initial draft: 700 rupees
    res1 = tools.run(
        "create_bill",
        {"customer_name": "Shivam Gupta", "customer_phone": "9876543210", "total": 700},
        ctx,
    )
    assert "BILL-0001" in res1
    bill_id = ctx.get("_active_bill_id")
    assert bill_id is not None
    original_bill = db.get_bill(bill_id)
    assert original_bill["total"] == 700

    # Correction: "no, the bill is 1000, stationery included" -> update_bill
    res2 = tools.run(
        "update_bill",
        {"total": 1000, "item_name": "Stationery included"},
        ctx,
    )
    assert "updated" in res2.lower()
    assert "1000" in res2

    # Verify same bill ID was preserved and updated
    bills = db.get_bills()
    assert len(bills) == 1
    updated_bill = db.get_bill(bill_id)
    assert updated_bill["id"] == original_bill["id"]
    assert updated_bill["billNumber"] == original_bill["billNumber"]
    assert updated_bill["total"] == 1000
    assert any(i["name"] == "Stationery included" for i in updated_bill["items"])


def test_send_bill_confirmation_guard_and_masked_phone():
    """Rule (e): Read back: 'Bill BILL-000X, 1000 rupaye, Shivam Gupta (98****3210) ko bhejun?'
    with phone shown masked in text and speech; send only after explicit yes."""
    ctx = {"owner": True, "channel": "assistant", "_turn": 1}
    db.set_setting("whatsapp_mode", "bridge")

    # Create bill with total=1000
    tools.run(
        "create_bill",
        {"customer_name": "Shivam Gupta", "customer_phone": "9876543210", "total": 1000},
        ctx,
    )

    with patch("tools._whatsapp_ready", return_value=None):
        res = asyncio.run(
            tools.run(
                "send_bill",
                {"name": "Shivam Gupta", "phone": "9876543210"},
                ctx,
            )
        )

    assert "ko bhejun?" in res
    assert "98****3210" in res
    assert "1000 rupaye" in res
    assert ctx.get("_pending") is not None
    assert "98****3210" in ctx["_pending"]["ask"]


def test_correction_cancels_pending_send_even_with_digits():
    """Rule (d): When owner says no/correction (e.g. 'no, the bill is 1000'),
    cancel pending send and apply edit."""
    async def _run():
        session = webcall.Session(assistant=True)
        session.tools_ctx["_pending"] = {
            "key": "bill:9876543210:BILL-0001",
            "turn": 1,
            "name": "send_bill",
            "args": {"bill_number": "BILL-0001"},
            "ask": "Bill BILL-0001, 700 rupaye, Shivam ko bhejun?",
        }

        mock_ws = AsyncMock()

        # The owner says: "no, the bill is 1000, stationery included" (contains digits '1000')
        with patch("brain.reply", new_callable=AsyncMock) as mock_reply:
            mock_reply.return_value = "Theek hai, bill 1000 ka kar diya."
            await webcall.respond(mock_ws, session, "no, the bill is 1000, stationery included")

        # Pending send must be cancelled (popped)
        assert session.tools_ctx.get("_pending") is None

    asyncio.run(_run())
