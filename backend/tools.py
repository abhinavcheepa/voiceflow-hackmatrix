"""Agent tools — the actions the assistant can actually take mid-conversation.

Vapi's "Tools" capability rebuilt on our own stack. The model is given these
schemas; when it decides to act it emits a tool call, brain.reply runs the
matching function here against our real database, and feeds the result back so
the agent can confirm in words. This is what turns "we'll confirm shortly" into
a booking a human can see.

Each tool is: a JSON schema (advertised to the model) plus a python function
(run against the DB). `ctx` carries who is calling — phone, name, channel — so a
booking is tied to the caller without the model having to be told the number.
"""

import inspect
import logging
import re

import db

log = logging.getLogger("voiceflow.tools")

SOURCES = ["all", "documents", "learned", "stock", "bookings", "bills", "history"]

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "Look something up before answering. Call this whenever the "
                "REFERENCE you were given does not clearly answer the question — "
                "never say 'I don't know' or invent a fact without searching first. "
                "Write a short, specific query (key nouns/names; try English and the "
                "caller's words). If the result is weak, search again with a "
                "different phrasing or source. Sources: documents = uploaded files "
                "(prices, FAQ, people, policies); learned = verified answers from "
                "past conversations; stock = live inventory; bookings / bills / "
                "history = THIS caller's own past bookings, bills and messages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look up, e.g. 'study table price'"},
                    "source": {"type": "string", "enum": SOURCES,
                               "description": "Where to look. Default 'all'."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Record a customer's booking or order once you know what they want. "
                "Call this instead of only promising a callback — it saves a real "
                "entry the shop can act on."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string",
                             "description": "What is being booked or ordered, e.g. 'study table'"},
                    "name": {"type": "string", "description": "Customer's name if known"},
                    "when": {"type": "string",
                             "description": "Requested date/time in the caller's words, e.g. 'kal shaam 5 baje'"},
                    "notes": {"type": "string", "description": "Any extra detail worth recording"},
                },
                "required": ["item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": (
                "Flag the conversation for a human to follow up when the caller asks "
                "for something you cannot handle, is unhappy, or explicitly wants a person."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why a human is needed"},
                    "name": {"type": "string", "description": "Customer's name if known"},
                },
                "required": ["reason"],
            },
        },
    },
    # --- billing tools ---
    {
        "type": "function",
        "function": {
            "name": "create_bill",
            "description": (
                "Start a new bill/invoice. Call this when the caller asks to create a bill, "
                "generate a bill, or says 'bill banao'. Ask for customer name first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string", "description": "Customer's name"},
                    "customer_phone": {"type": "string", "description": "Customer's phone number if known"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_bill_item",
            "description": (
                "Add an item to the current bill. Call this for each item the customer wants. "
                "Ask for item name, quantity, and rate. If the item exists in stock, the rate "
                "and GST will be auto-filled."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item_name": {"type": "string", "description": "Name of the item, e.g. 'cement bag'"},
                    "quantity": {"type": "number", "description": "How many, e.g. 5"},
                    "rate": {"type": "number", "description": "Price per unit in rupees, e.g. 350"},
                    "gst_percent": {"type": "number", "description": "GST percentage, e.g. 18. Default 0."},
                },
                "required": ["item_name", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_bill",
            "description": (
                "Complete and finalize the current bill. Call this when the caller says "
                "'bas', 'done', 'bill complete karo', or confirms all items are added."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    # --- stock tools ---
    {
        "type": "function",
        "function": {
            "name": "check_stock",
            "description": (
                "Check the current stock/inventory for an item. Call this when someone "
                "asks 'kitna stock hai', 'stock check karo', or asks about availability."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item_name": {"type": "string", "description": "Item to check, e.g. 'cement'"},
                },
                "required": ["item_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_stock_entry",
            "description": (
                "Record a stock entry — incoming goods, outgoing goods, or adjustment. "
                "Call this when someone says 'stock entry karo', 'maal aaya hai', or "
                "'stock update karo'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item_name": {"type": "string", "description": "Name of the stock item"},
                    "quantity": {"type": "number", "description": "How much, e.g. 50"},
                    "entry_type": {
                        "type": "string",
                        "enum": ["in", "out", "adjust"],
                        "description": "'in' for incoming stock, 'out' for outgoing, 'adjust' for manual correction",
                    },
                    "notes": {"type": "string", "description": "Any note about this entry"},
                },
                "required": ["item_name", "quantity", "entry_type"],
            },
        },
    },
]


def _book_appointment(args: dict, ctx: dict) -> str:
    item = (args.get("item") or "").strip()
    if not item:
        return "Could not book — no item was specified."
    booking = db.add_booking(
        item=item,
        name=args.get("name") or ctx.get("name"),
        phone=ctx.get("phone"),
        when_text=args.get("when"),
        notes=args.get("notes"),
        channel=ctx.get("channel", "call"),
    )
    when = f" for {booking['when']}" if booking.get("when") else ""
    return f"Booking #{booking['id']} saved: {item}{when}. Tell the customer it is noted and will be confirmed."


def _escalate_to_human(args: dict, ctx: dict) -> str:
    reason = (args.get("reason") or "customer requested a human").strip()
    booking = db.add_booking(
        item=f"Escalation: {reason}",
        name=args.get("name") or ctx.get("name"),
        phone=ctx.get("phone"),
        channel=ctx.get("channel", "call"),
        escalated=True,
    )
    return f"Escalation #{booking['id']} recorded. Tell the customer a team member will reach out."


def _create_bill(args: dict, ctx: dict) -> str:
    name = (args.get("customer_name") or ctx.get("name") or "").strip()
    phone = (args.get("customer_phone") or ctx.get("phone") or "").strip()
    bill = db.create_bill(
        customer_name=name or None,
        customer_phone=phone or None,
        agent_id=ctx.get("agent_id"),
        channel=ctx.get("channel", "call"),
    )
    # Store the bill id in ctx so subsequent add_bill_item calls find it.
    ctx["_active_bill_id"] = bill["id"]
    return (
        f"Bill {bill['billNumber']} started"
        + (f" for {name}" if name else "")
        + ". Now ask the customer what items to add. For each item, ask name, quantity, and rate."
    )


def _add_bill_item(args: dict, ctx: dict) -> str:
    item_name = (args.get("item_name") or "").strip()
    if not item_name:
        return "No item name given. Ask the customer what item to add."
    quantity = float(args.get("quantity") or 1)
    rate = float(args.get("rate") or 0)
    gst = float(args.get("gst_percent") or 0)

    # Find the active bill, or the most recent draft.
    bill_id = ctx.get("_active_bill_id")
    if not bill_id:
        active = db.get_active_bill(ctx.get("channel", "call"))
        if not active:
            return "No bill is open. Create a bill first by asking the customer."
        bill_id = active["id"]

    # Try to match against stock for auto-rate and auto-deduction.
    stock_id = None
    stock_items = db.search_stock(item_name, ctx.get("agent_id"))
    if stock_items:
        best = stock_items[0]
        stock_id = best["id"]
        if rate <= 0:
            rate = best["rate"]
        if gst <= 0:
            gst = best["gstPercent"]

    if rate <= 0:
        return f"Rate not found for '{item_name}'. Ask the customer for the price per unit."

    result = db.add_bill_item(bill_id, item_name, quantity, rate, gst, stock_id)
    bill = db.get_bill(bill_id)
    total_str = f"₹{bill['total']:.0f}" if bill else ""
    return (
        f"Added: {item_name} × {quantity} @ ₹{rate:.0f}"
        + (f" + {gst}% GST" if gst > 0 else "")
        + f" = ₹{result['amount']:.0f}."
        + (f" Bill running total: {total_str}." if total_str else "")
        + " Ask if they want to add more items, or finalize the bill."
    )


def _finalize_bill(args: dict, ctx: dict) -> str:
    bill_id = ctx.get("_active_bill_id")
    if not bill_id:
        active = db.get_active_bill(ctx.get("channel", "call"))
        if not active:
            return "No open bill found. Nothing to finalize."
        bill_id = active["id"]

    bill = db.finalize_bill(bill_id)
    if not bill:
        return "Could not finalize the bill."

    items_text = ", ".join(
        f"{i['name']} ×{i['quantity']}" for i in bill.get("items", [])
    )
    return (
        f"Bill {bill['billNumber']} finalized. "
        f"Items: {items_text}. "
        f"Subtotal: ₹{bill['subtotal']:.0f}, GST: ₹{bill['gstTotal']:.0f}, "
        f"Total: ₹{bill['total']:.0f}. "
        "Tell the customer the total and confirm."
    )


def _check_stock(args: dict, ctx: dict) -> str:
    item_name = (args.get("item_name") or "").strip()
    if not item_name:
        return "No item name given. Ask what item to check."

    results = db.search_stock(item_name, ctx.get("agent_id"))
    if not results:
        return f"'{item_name}' not found in stock. It may not be tracked, or the name may be different."

    lines = []
    for item in results[:3]:
        status = "⚠️ LOW" if item["lowStock"] else "✓"
        lines.append(
            f"{item['name']}: {item['quantity']} {item['unit']} "
            f"@ ₹{item['rate']:.0f} {status}"
        )
    return "Stock found:\n" + "\n".join(lines)


def _add_stock_entry_handler(args: dict, ctx: dict) -> str:
    item_name = (args.get("item_name") or "").strip()
    if not item_name:
        return "No item name given. Ask what item to update."
    quantity = float(args.get("quantity") or 0)
    if quantity <= 0:
        return "Quantity must be greater than 0."
    entry_type = (args.get("entry_type") or "in").strip().lower()
    if entry_type not in ("in", "out", "adjust"):
        entry_type = "in"

    # Find or create the stock item.
    results = db.search_stock(item_name, ctx.get("agent_id"))
    if results:
        stock_item = results[0]
    else:
        # Auto-create if not found
        stock_item = db.add_stock_item(
            name=item_name, agent_id=ctx.get("agent_id")
        )

    notes = args.get("notes") or f"Voice entry via {ctx.get('channel', 'call')}"
    result = db.add_stock_entry(stock_item["id"], entry_type, quantity, notes)

    type_label = {"in": "added", "out": "removed", "adjust": "adjusted to"}[entry_type]
    return (
        f"Stock updated: {item_name} — {type_label} {quantity}. "
        f"New quantity: {result['newQuantity']} {stock_item.get('unit', 'pcs')}."
    )


# Keep a tool result small: it is resent with every later round of the turn,
# and the free tier caps tokens per minute.
MAX_RESULT_CHARS = 2400


def _search_documents(q: str, ctx: dict) -> str:
    import kb

    hits = kb.search(ctx.get("agent_id"), q, k=4)
    return "\n---\n".join(hits)


def _search_learned(q: str, ctx: dict) -> str:
    import kb

    pairs = [f"Q: {a['question']}\nA: {a['answer']}"
             for a in db.approved_answers_for(ctx.get("agent_id"))]
    return "\n---\n".join(kb._bm25_ranked(pairs, q, 3)) if pairs else ""


# Filler words that would substring-match unrelated items ("hai" -> "Chair").
_STOCK_STOP = {"stock", "kitna", "kitni", "kitne", "available", "price", "rate",
               "check", "karo", "maal", "item", "items", "bacha", "bachi", "quantity",
               "hai", "hain", "the", "and", "how", "much", "many", "what", "kya"}


def _search_stock(q: str, ctx: dict) -> str:
    import kb

    # search_stock is a substring match, so try each meaningful word of the
    # query ("cement ka stock kitna hai" -> "cement").
    seen: dict[int, dict] = {}
    for word in [q, *kb._tokenize(q)]:
        if len(word) > 2 and word not in _STOCK_STOP:
            for item in db.search_stock(word, ctx.get("agent_id")):
                seen.setdefault(item["id"], item)
    return "\n".join(
        f"{i['name']}: {i['quantity']} {i['unit']} in stock @ Rs {i['rate']:.0f}"
        + (" (LOW)" if i["lowStock"] else "")
        for i in list(seen.values())[:5]
    )


def _search_bookings(q: str, ctx: dict) -> str:
    return "\n".join(
        f"#{b['id']} {b['item']}" + (f" for {b['when']}" if b["when"] else "")
        + f" — {b['status']}, {b['time']}" + (" (escalated)" if b["escalated"] else "")
        for b in db.bookings_for(ctx["phone"])
    )


def _search_bills(q: str, ctx: dict) -> str:
    return "\n".join(
        f"{b['billNumber']} ({b['status']}, {b['time']}): "
        + ", ".join(f"{i['name']} x{i['quantity']:g}" for i in b["items"])
        + f" — total Rs {b['total']:.0f}"
        for b in db.bills_for(ctx["phone"])
    )


def _search_history(q: str, ctx: dict) -> str:
    import kb

    return "\n".join(kb._bm25_ranked(db.messages_for(ctx["phone"]), q, 6))


_SEARCHERS = {
    "documents": _search_documents,
    "learned": _search_learned,
    "stock": _search_stock,
    "bookings": _search_bookings,
    "bills": _search_bills,
    "history": _search_history,
}
# Sources that read one customer's own records — need a known caller.
_PERSONAL = {"bookings", "bills", "history"}


def _search_knowledge(args: dict, ctx: dict) -> str:
    """Agentic retrieval: the model chooses the query and the source, reads the
    result, and may search again — instead of being handed one fixed retrieval."""
    query = (args.get("query") or "").strip()
    if not query:
        return "Give a query to search for."
    source = (args.get("source") or "all").lower()
    if source not in _SEARCHERS:
        source = "all"
    names = list(_SEARCHERS) if source == "all" else [source]

    sections = []
    for name in names:
        if name in _PERSONAL and not ctx.get("phone"):
            if source != "all":
                sections.append(f"[{name}] caller is not identified on this channel — ask them.")
            continue
        try:
            found = _SEARCHERS[name](query, ctx)
        except Exception as e:  # noqa: BLE001 - one broken source must not sink the rest
            log.warning("search %s failed: %s", name, e)
            continue
        if found:
            sections.append(f"[{name}]\n{found}")

    if not sections:
        return (f"Nothing found for '{query}' in {source}. Try a different wording or "
                "source once; if still nothing, tell the caller you will confirm and get back.")
    return "\n\n".join(sections)[:MAX_RESULT_CHARS]


def _transfer_schema(squad: list[dict]) -> dict:
    roster = "; ".join(f"{a['name']}: {a['about']}" if a.get("about") else a["name"] for a in squad)
    return {
        "type": "function",
        "function": {
            "name": "transfer_to_agent",
            "description": (
                "Hand the caller to another agent who is better suited, keeping the "
                "conversation. Use only when the caller's need clearly belongs to "
                f"them or they ask for that team. Available: {roster}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {"type": "string", "enum": [a["name"] for a in squad]},
                    "reason": {"type": "string", "description": "Why, in a few words"},
                },
                "required": ["agent_name"],
            },
        },
    }


def schemas(ctx: dict | None) -> list[dict]:
    """Tools offered on this turn. Transfer appears only when there is someone
    to transfer to — a tool that can never work just invites the model to try."""
    ctx = ctx or {}
    squad = ctx.get("squad")
    return (SCHEMAS + ([_transfer_schema(squad)] if squad else [])
            + (_owner_schemas() if ctx.get("owner") else []))


def _transfer_to_agent(args: dict, ctx: dict) -> str:
    wanted = (args.get("agent_name") or "").strip().lower()
    match = next((a for a in ctx.get("squad") or [] if a["name"].lower() == wanted), None)
    if not match:
        return "No such agent. Carry on helping the caller yourself."
    # The call loop does the actual switch once this reply has been spoken.
    ctx["_handoff"] = match["id"]
    return (f"Transfer to {match['name']} accepted. In ONE short sentence tell the caller "
            f"you are connecting them to {match['name']}. Do not answer their question yourself.")


# --- owner assistant tools ("Jarvis" mode) --------------------------------
# Only offered when the OWNER is talking (ctx["owner"]), never to a customer:
# these reach outside the conversation — message any number, hand a WhatsApp
# thread to an agent, read the whole business's activity.

def _owner_schemas() -> list[dict]:
    agents = [a["name"] for a in db.get_agents()]
    phone = {"type": "string", "description": "The person's 10-digit number exactly as the owner said it, OR just their "
             "name as the owner said it ('Lavish bhai') — saved contacts are looked up by name"}
    person = {"type": "string", "description": "The person's name, if the owner said one — saved "
              "with the number so next time the name alone is enough"}
    fn = lambda name, desc, props, req: {  # noqa: E731
        "type": "function",
        "function": {"name": name, "description": desc,
                     "parameters": {"type": "object", "properties": props, "required": req}},
    }
    return [
        fn("send_whatsapp", "Send a WhatsApp text from the business number to any number.",
           {"phone": phone, "name": person,
            "message": {"type": "string", "description": "The exact text to send"}},
           ["phone", "message"]),
        fn("send_bill", "Send a finalized bill to a number on WhatsApp. Defaults to the bill "
           "just made in this conversation.",
           {"phone": phone, "name": person, "bill_number": {"type": "string", "description": "e.g. INV-0012; omit for the current bill"}},
           ["phone"]),
        fn("start_whatsapp_chat",
           "Start a WhatsApp conversation with a number and let an agent carry it on: sends the "
           "opening message, then that agent answers every reply, working toward the goal.",
           {"phone": phone, "name": person,
            "goal": {"type": "string", "description": "What the agent should achieve, e.g. 'collect the pending payment of Rs 4800'"},
            "opening_message": {"type": "string", "description": "First message to send, in the customer's language"},
            "agent_name": {"type": "string", "enum": agents, "description": "Which agent talks. Default: the default agent"}},
           ["phone", "goal", "opening_message"]),
        fn("auto_reply_settings",
           "Control which WhatsApp numbers the agent answers automatically. allow = the agent "
           "replies to this person; block = stop replying to them; status = what is set; "
           "all_on/all_off = master switch. Changes settings only — sends nothing.",
           {"action": {"type": "string", "enum": ["allow", "block", "status", "all_on", "all_off"]},
            "phone": phone},
           ["action"]),
        fn("find_contact", "Look up a saved person's WhatsApp number by name.",
           {"name": {"type": "string", "description": "Name as the owner said it"}}, ["name"]),
        fn("recent_activity", "What has been happening in the business: latest bookings, bills, "
           "calls (with summaries), WhatsApp chats, or low stock.",
           {"kind": {"type": "string", "enum": ["bookings", "bills", "calls", "whatsapp", "low_stock"]}},
           ["kind"]),
    ]


def _spoken(phone: str) -> str:
    """Grouped for reading aloud: 919812345678 -> '91 98123 45678'."""
    return f"{phone[:-10]} {phone[-10:-5]} {phone[-5:]}".strip()


def _confirm(ctx: dict, name: str, args: dict, key: str, ask: str) -> str | None:
    """None means go ahead; otherwise the read-back the owner must agree to.

    Nothing outward runs in the turn it was asked for. The action is parked
    with exactly the details read back, and runs only when the owner says yes
    afterwards — the call loop recognises a plain "haan" and runs the parked
    action itself (approve_pending), so what goes out is what they heard.
    A number misheard by speech-to-text therefore never goes out unheard.
    """
    pending, turn = ctx.get("_pending"), ctx.get("_turn", 0)
    if ctx.get("_approved") == key or (pending and pending["key"] == key and pending["turn"] < turn):
        ctx.pop("_pending", None)
        ctx.pop("_approved", None)
        return None
    # `ask` is spoken to the owner verbatim by the call loop — the model once
    # said "bhej diya" over a NOT SENT YET, and the owner never said yes.
    ctx["_pending"] = {"key": key, "turn": turn, "name": name, "args": dict(args), "ask": ask}
    return f"NOT SENT YET. The owner will be asked: {ask} Their yes sends it."


async def approve_pending(ctx: dict) -> str | None:
    """Run the parked action the owner just said yes to. None if nothing is parked."""
    pending = ctx.get("_pending")
    if not pending:
        return None
    ctx["_approved"] = pending["key"]
    result = run(pending["name"], pending["args"], ctx)
    if inspect.isawaitable(result):
        result = await result
    ctx.pop("_pending", None)
    ctx.pop("_approved", None)
    return result


# Words around a name that aren't the name ("Lavish bhai ko", "Sharma ji").
_HONORIFICS = {
    "bhai", "bhaiya", "bhaiyya", "bhay", "bhaee", "ji", "jee", "sir", "sar", "didi", "di",
    "uncle", "aunty", "anti", "bhabhi", "madam", "mam", "sahab", "saab", "sahib", "mr", "mrs",
    "ko", "se", "ka", "ki", "ke", "par", "pe", "wale", "vale", "wali", "unknown",
}
# How close a spoken name must sound to a saved one. "लाविम" (Whisper's take on
# "Lavish") romanises to "lavim", 0.73 against "lavish"; unrelated names sit
# well below. Every send is read back with the number, so a near-miss is heard.
NAME_MATCH = 0.7


def _name_tokens(s: str) -> list[str]:
    import kb

    s = kb._romanize(s) if kb._has_devanagari(s) else s
    return [t for t in re.findall(r"[a-z]+", s.lower()) if t not in _HONORIFICS and len(t) > 1]


def is_contact(phone: str) -> bool:
    return any(db.normalize_phone(c["phone"]) == phone for c in db.get_contacts(limit=2000))


# Stricter than NAME_MATCH: this scans every word of a command, not a name the
# model chose to look up, so "bill" must not drag in a contact called "Bilal".
HINT_MATCH = 0.85


def contact_hints(text: str, limit: int = 3) -> list[tuple[str, str]]:
    """Saved contacts whose name appears in what the owner just said.

    Handed to the model up front, so "Kanishk ko bhejo" finds Kanishk's number
    even when the model doesn't think to look — it once asked the owner for a
    number that was already saved.
    """
    from difflib import SequenceMatcher

    words = [w for w in _name_tokens(text) if len(w) >= 3]
    if not words:
        return []
    found: dict[str, tuple[float, str]] = {}
    for c in db.get_contacts(limit=2000):
        n = [t for t in _name_tokens(c.get("name") or "") if len(t) >= 3]
        if not n or not c.get("phone"):
            continue
        score = max(SequenceMatcher(None, a, b).ratio() for a in words for b in n)
        phone = db.normalize_phone(c["phone"])
        if score >= HINT_MATCH and score > found.get(phone, (0.0,))[0]:
            found[phone] = (score, c["name"])
    best = sorted(found.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
    return [(name, phone) for phone, (_, name) in best]


def find_contacts(query: str, limit: int = 3) -> list[tuple[float, str, str]]:
    """Saved people whose name sounds like `query`: [(score, name, phone)], best first.

    Tolerant of script and spelling — the owner says a name, speech-to-text
    writes it in Devanagari or misspells it, the contact is saved in Latin.
    """
    from difflib import SequenceMatcher

    q = _name_tokens(query)
    if not q:
        return []
    best: dict[str, tuple[float, str, str]] = {}
    for c in db.get_contacts(limit=2000):
        n = _name_tokens(c.get("name") or "")
        if not n or not c.get("phone"):
            continue
        score = max(
            max(SequenceMatcher(None, a, b).ratio() for a in q for b in n),
            SequenceMatcher(None, " ".join(q), " ".join(n)).ratio(),
        )
        phone = db.normalize_phone(c["phone"])
        if score >= NAME_MATCH and score > best.get(phone, (0.0,))[0]:
            best[phone] = (score, c["name"], phone)
    return sorted(best.values(), reverse=True)[:limit]


def _resolve(args: dict, ctx: dict) -> tuple[str, str, str | None]:
    """Who to send to: (phone, how to say who it is, error to relay).

    The owner may give a number, or just a name ("Lavish bhai ko bhejo") —
    a name is looked up in saved contacts.
    """
    raw = (args.get("phone") or "").strip()
    name = (args.get("name") or "").strip()
    digits = db._digits(raw)
    if digits:
        phone = db.normalize_phone(raw)
        if len(phone) < 12:
            return "", "", "That number looks incomplete — ask the owner for the full 10-digit number."
        # In a live owner session a number must be one the owner actually said
        # or a saved contact's. The model once invented "98765 43210" for a
        # person whose number it didn't have.
        if "_heard_numbers" in ctx and phone not in ctx["_heard_numbers"] and not is_contact(phone):
            return "", "", ("The owner never said that number and it isn't a saved contact — "
                            "don't make one up. Ask the owner for the number.")
        return phone, (f"{name} ({_spoken(phone)})" if name else _spoken(phone)), None
    who = raw or name
    if not who:
        return "", "", "Ask the owner who it is for — a name or a number."
    hits = find_contacts(who)
    if not hits:
        return "", "", (f"No saved contact matches '{who}'. Ask the owner for the number, then "
                        f"call again with the number AND name='{who}' — it gets saved for next time.")
    if len(hits) > 1 and hits[0][0] - hits[1][0] < 0.05:
        options = "; ".join(f"{n} ({_spoken(p)})" for _, n, p in hits)
        return "", "", f"More than one contact matches '{who}': {options}. Ask the owner which one."
    _, cname, phone = hits[0]
    return phone, f"{cname} ({_spoken(phone)})", None


def _find_contact(args: dict, ctx: dict) -> str:
    hits = find_contacts(args.get("name") or "")
    return "\n".join(f"{n}: {_spoken(p)}" for _, n, p in hits) or "No saved contact by that name."


def _whatsapp_ready() -> str | None:
    import whatsapp

    return None if whatsapp.configured() else (
        "WhatsApp is not connected — link it on the WhatsApp page first. Nothing was sent.")


async def _deliver(phone: str, text: str, ctx: dict, name: str | None = None) -> None:
    import whatsapp

    await whatsapp.send_text(phone, text)
    # Storing the message with the owner's name for this person also saves the
    # contact, so next time "Lavish bhai ko bhejo" works without the number.
    db.add_message(phone, "us", "text", text, name=name or None)
    # The call loop checks this before letting the agent say "bhej diya".
    ctx["_delivered_turn"] = ctx.get("_turn", 0)


async def _send_whatsapp(args: dict, ctx: dict) -> str:
    phone, who, bad = _resolve(args, ctx)
    text = (args.get("message") or "").strip()
    if bad or not text:
        return bad or "What should the message say? Ask the owner."
    if err := _whatsapp_ready():
        return err
    if gate := _confirm(ctx, "send_whatsapp", args, f"wa:{phone}:{text}",
                        f'{who} ko ye message bhejun: "{text}"?'):
        return gate
    await _deliver(phone, text, ctx, args.get("name"))
    return f"Bhej diya — {who} ko message chala gaya."


def _bill_text(bill: dict) -> str:
    business = (db.get_agent() or {}).get("name") or "Our shop"
    lines = [f"*{business}* — Bill {bill['billNumber']}"]
    if bill.get("customerName") and bill["customerName"] != "—":
        lines.append(f"Customer: {bill['customerName']}")
    lines += [f"• {i['name']} × {i['quantity']:g} @ ₹{i['rate']:.0f} = ₹{i['amount']:.0f}"
              for i in bill["items"]]
    lines += [f"Subtotal: ₹{bill['subtotal']:.0f}", f"GST: ₹{bill['gstTotal']:.0f}",
              f"*Total: ₹{bill['total']:.0f}*", "Thank you!"]
    return "\n".join(lines)


async def _send_bill(args: dict, ctx: dict) -> str:
    phone, who, bad = _resolve(args, ctx)
    if bad:
        return bad
    wanted = (args.get("bill_number") or "").strip().upper()
    if wanted:
        match = next((b for b in db.get_bills(200) if b["billNumber"].upper() == wanted), None)
        bill = db.get_bill(match["id"]) if match else None
    else:
        bill_id = ctx.get("_active_bill_id") or next(iter(b["id"] for b in db.get_bills(1)), None)
        bill = db.get_bill(bill_id) if bill_id else None
    if not bill:
        return "No such bill. Make one first (create_bill, add_bill_item, finalize_bill)."
    if bill["status"] == "draft":
        return "That bill is still open — call finalize_bill first, then send it."
    if err := _whatsapp_ready():
        return err
    if gate := _confirm(ctx, "send_bill", {**args, "bill_number": bill["billNumber"]},
                        f"bill:{phone}:{bill['billNumber']}",
                        f"Bill {bill['billNumber']}, ₹{bill['total']:.0f}, {who} ko bhejun?"):
        return gate
    await _deliver(phone, _bill_text(bill), ctx, args.get("name"))
    return f"Bhej diya — bill {bill['billNumber']} (₹{bill['total']:.0f}) {who} ko."


async def _start_whatsapp_chat(args: dict, ctx: dict) -> str:
    phone, who, bad = _resolve(args, ctx)
    goal = (args.get("goal") or "").strip()
    opening = (args.get("opening_message") or "").strip()
    if bad or not goal or not opening:
        return bad or "Ask the owner what the agent should achieve and what to say first."
    name = (args.get("agent_name") or "").strip().lower()
    agent = next((a for a in db.get_agents() if a["name"].lower() == name), None) if name else None
    agent = agent or db.get_agent() or {}
    if err := _whatsapp_ready():
        return err
    if gate := _confirm(ctx, "start_whatsapp_chat", args, f"chat:{phone}:{agent.get('id')}:{opening}",
                        f'{agent.get("name", "Agent")} se {who} ke saath WhatsApp baat '
                        f'shuru karun? Pehla message: "{opening}". Goal: {goal}.'):
        return gate
    db.assign_conversation(phone, agent.get("id"), goal, args.get("name"))
    await _deliver(phone, opening, ctx, args.get("name"))
    note = "" if db.auto_reply_global() else (
        " Dhyan dein: auto-reply abhi band hai, isliye unke jawab aapke liye rukenge.")
    return (f"Ho gaya — {agent.get('name', 'agent')} ne {who} se WhatsApp par baat "
            f"shuru kar di, ab wahi jawab dega. Goal: {goal}.{note}")


def _recent_activity(args: dict, ctx: dict) -> str:
    kind = args.get("kind") or "bookings"
    if kind == "bookings":
        rows = [f"#{b['id']} {b['item']} for {b['when']} — {b['name']} {b['phone']} ({b['time']})"
                for b in db.get_bookings(5)]
    elif kind == "bills":
        rows = [f"{b['billNumber']} ₹{b['total']:.0f} {b['status']} — {b['customerName']} ({b['time']})"
                for b in db.get_bills(5)]
    elif kind == "calls":
        rows = [f"{c['time']}: {c['name']} — {c['summary'] or c['intent']} [{c['outcome']}]"
                for c in db.get_calls(5)]
    elif kind == "whatsapp":
        rows = [f"{c['name']} ({c['phone']}): {c['last']} — {c['time']}, {c['unread']} unread"
                for c in db.get_conversations()[:5]]
    else:
        rows = [f"{s['name']}: {s['quantity']} {s['unit']} left (min {s['minStock']})"
                for s in db.get_stock_items() if s["lowStock"]]
    return "\n".join(rows) or f"Nothing in {kind} yet."


def _auto_reply_settings(args: dict, ctx: dict) -> str:
    """Who the agent answers on WhatsApp. Pure settings — nothing is sent.

    The owner once asked to "allow this number" and, with no tool for it, the
    model "set it up" by messaging that number instead.
    """
    action = args.get("action") or "status"
    if action == "status":
        allow = db.get_allowlist()
        return ("Auto-reply is " + ("ON" if db.auto_reply_global() else "OFF") + " overall. "
                + (f"Only these numbers get replies: {', '.join(_spoken(db.normalize_phone(a)) for a in allow)}."
                   if allow else "Every number gets replies (no allowlist)."))
    if action in ("all_on", "all_off"):
        db.set_auto_reply_global(action == "all_on")
        return "Ho gaya — auto-reply ab sabke liye " + ("chalu" if action == "all_on" else "band") + " hai."
    phone, who, bad = _resolve(args, ctx)
    if bad:
        return bad
    if action == "allow":
        allow = db.get_allowlist()
        if allow and not db.allowed(phone):
            db.set_allowlist([*allow, phone])
        db.set_thread_auto_reply(phone, True)
        note = "" if db.auto_reply_global() else " Lekin auto-reply abhi sabke liye band hai — chalu karna ho to boliye."
        return f"Ho gaya — {who} ke messages ka jawab ab agent khud dega.{note}"
    # "block": switch this thread off. The allowlist is left alone on purpose —
    # removing its last number would empty it, and an empty list means EVERYONE.
    db.set_thread_auto_reply(phone, False)
    return f"Ho gaya — {who} ko ab agent jawab nahi dega; wo aapke liye rukega."


_OWNER_HANDLERS = {
    "send_whatsapp": _send_whatsapp,
    "send_bill": _send_bill,
    "start_whatsapp_chat": _start_whatsapp_chat,
    "recent_activity": _recent_activity,
    "find_contact": _find_contact,
    "auto_reply_settings": _auto_reply_settings,
}


_HANDLERS = {
    "search_knowledge": _search_knowledge,
    "transfer_to_agent": _transfer_to_agent,
    "book_appointment": _book_appointment,
    "escalate_to_human": _escalate_to_human,
    "create_bill": _create_bill,
    "add_bill_item": _add_bill_item,
    "finalize_bill": _finalize_bill,
    "check_stock": _check_stock,
    "add_stock_entry": _add_stock_entry_handler,
    **_OWNER_HANDLERS,
}


# Tools that change real records. In a dry run (evals/simulations) they are
# described, not executed — a test conversation must never create a booking.
MUTATING = {"book_appointment", "escalate_to_human", "create_bill", "add_bill_item",
            "finalize_bill", "add_stock_entry"}


def run(name: str, args: dict, ctx: dict) -> str:
    """Execute one tool call and return a short result string for the model.

    Never raises into the reply loop: a broken tool must not drop the whole
    turn, so failures come back as text the agent can gracefully speak around.
    """
    ctx = ctx if ctx is not None else {}
    # Every call is logged on the context, so evals can assert "it booked".
    ctx.setdefault("_tool_log", []).append({"name": name, "args": args or {}})
    if ctx.get("dry_run") and name in MUTATING:
        return f"[simulation] {name} succeeded with {args}. Confirm it to the caller as usual."
    handler = _HANDLERS.get(name)
    # Owner tools reach outside the conversation; a customer must never trigger
    # one even if the model hallucinates its name.
    if not handler or (name in _OWNER_HANDLERS and not ctx.get("owner")):
        return f"Tool '{name}' is not available."
    try:
        result = handler(args or {}, ctx)
    except Exception as e:  # noqa: BLE001 - a tool failure must not kill the call
        log.warning("tool %s failed: %s", name, e)
        return "That could not be completed right now. Offer to take details for a callback."
    # Async tools (sending on WhatsApp) come back as a coroutine; the caller
    # awaits it, and its failures are caught the same way.
    return _guarded(name, result, ctx) if inspect.isawaitable(result) else _note_done(ctx, result)


async def _guarded(name: str, pending, ctx: dict) -> str:
    try:
        return _note_done(ctx, await pending)
    except Exception as e:  # noqa: BLE001
        log.warning("tool %s failed: %s", name, e)
        return f"That failed: {e}. Nothing more was done — tell the owner."


def _note_done(ctx: dict, result):
    """Remember owner actions that completed this turn, so the call loop can
    make sure the owner is told — the model has replied a bare "aur kuch?"
    right after changing a setting."""
    if isinstance(result, str) and result.startswith(("Ho gaya", "Bhej diya")):
        ctx.setdefault("_done", []).append((ctx.get("_turn"), result))
    return result

