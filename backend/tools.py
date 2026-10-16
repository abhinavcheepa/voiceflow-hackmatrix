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

import logging

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


_HANDLERS = {
    "search_knowledge": _search_knowledge,
    "book_appointment": _book_appointment,
    "escalate_to_human": _escalate_to_human,
    "create_bill": _create_bill,
    "add_bill_item": _add_bill_item,
    "finalize_bill": _finalize_bill,
    "check_stock": _check_stock,
    "add_stock_entry": _add_stock_entry_handler,
}


def run(name: str, args: dict, ctx: dict) -> str:
    """Execute one tool call and return a short result string for the model.

    Never raises into the reply loop: a broken tool must not drop the whole
    turn, so failures come back as text the agent can gracefully speak around.
    """
    handler = _HANDLERS.get(name)
    if not handler:
        return f"Tool '{name}' is not available."
    try:
        return handler(args or {}, ctx or {})
    except Exception as e:  # noqa: BLE001 - a tool failure must not kill the call
        log.warning("tool %s failed: %s", name, e)
        return "That could not be completed right now. Offer to take details for a callback."

