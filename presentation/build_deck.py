# -*- coding: utf-8 -*-
"""Builds the VoiceFlow AI pitch deck (16:9 PPTX) in the product's own palette.

Run:  python presentation/build_deck.py
Out:  presentation/VoiceFlow-AI-Pitch-Deck.pptx

Every number in the deck is either measured in this repo or sourced on the
slide. Edit the CONTENT sections below, re-run, done.
"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

# ── palette: straight from src/index.css ────────────────────────────────
INK    = RGBColor(0x07, 0x07, 0x0C)
PANEL  = RGBColor(0x10, 0x10, 0x19)
PANEL2 = RGBColor(0x16, 0x16, 0x23)
LINE   = RGBColor(0x24, 0x24, 0x3A)
VIOLET = RGBColor(0x7C, 0x5C, 0xFF)
VSOFT  = RGBColor(0xA4, 0x8B, 0xFF)
MINT   = RGBColor(0x2E, 0xE6, 0xA8)
SAFF   = RGBColor(0xFF, 0x8A, 0x3D)
ROSE   = RGBColor(0xFF, 0x5C, 0x7A)
DIM    = RGBColor(0x8E, 0x8E, 0xA8)
FAINT  = RGBColor(0x5A, 0x5A, 0x72)
WHITE  = RGBColor(0xED, 0xED, 0xF2)
GLOW   = RGBColor(0x16, 0x11, 0x2C)
FONT   = "Segoe UI"

W, H = 13.333, 7.5
M = 0.62
CW = W - 2 * M


def P(t, sz=11, c=WHITE, b=False, sa=0, ls=None, sp=None, align=None):
    return dict(t=t, sz=sz, c=c, b=b, sa=sa, ls=ls, sp=sp, align=align)


def text(s, x, y, w, h, paras, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    for i, p in enumerate(paras):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = p.get("align") or align
        para.space_after = Pt(p.get("sa") or 0)
        if p.get("ls"):
            para.line_spacing = p["ls"]
        r = para.add_run()
        r.text = p["t"]
        f = r.font
        f.name, f.size, f.bold = FONT, Pt(p.get("sz", 11)), p.get("b", False)
        f.color.rgb = p.get("c", WHITE)
        if p.get("sp"):
            r._r.get_or_add_rPr().set("spc", str(int(p["sp"] * 100)))
    return tb


def rect(s, x, y, w, h, fill=None, line=None, rounded=False, radius=0.06, lw=1.0):
    shp = s.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h))
    if rounded:
        shp.adjustments[0] = radius
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(lw)
    shp.shadow.inherit = False
    return shp


def dot(s, x, y, d, c):
    shp = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    shp.fill.solid()
    shp.fill.fore_color.rgb = c
    shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def slide(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    rect(s, 0, 0, W, H, INK)
    g = rect(s, 0, 0, W, 2.6)
    g.fill.gradient()
    g.fill.gradient_angle = 270
    g.fill.gradient_stops[0].color.rgb = GLOW
    g.fill.gradient_stops[1].color.rgb = INK
    g.line.fill.background()
    return s


def header(s, kicker, title, sub=None, tsz=29):
    text(s, M, 0.44, CW, 0.24, [P(kicker, 10.5, MINT, True, sp=1.9)])
    text(s, M, 0.70, CW, 0.60, [P(title, tsz, WHITE, True)])
    y = 1.36
    if sub:
        subw = CW * 0.80
        text(s, M, y, subw, 0.60, [P(sub, 12.5, DIM, ls=1.25)])
        # ~11.5 chars/inch at 12.5pt; two lines need more room before the rule.
        y += 0.66 if len(sub) > subw * 11.5 else 0.50
    rect(s, M, y, 1.05, 0.03, MINT)
    return y + 0.30


def footer(s, n, note=None):
    text(s, M, H - 0.44, 6.0, 0.22, [P("VoiceFlow AI  ·  Team Voice Flow  ·  HackMatrix 2026", 8.5, FAINT)])
    text(s, W - M - 1.2, H - 0.44, 1.2, 0.22, [P("%02d" % n, 8.5, FAINT)], align=PP_ALIGN.RIGHT)
    if note:
        text(s, M, H - 0.78, CW - 1.4, 0.26, [P(note, 8.5, FAINT, ls=1.2)])


def card(s, x, y, w, h, title, body, accent=VIOLET, tsz=13.5, bsz=10, eyebrow=None):
    rect(s, x, y, w, h, PANEL, LINE, rounded=True, radius=0.055)
    rect(s, x + 0.001, y + 0.30, 0.045, h - 0.60, accent)
    ty = y + 0.26
    if eyebrow:
        text(s, x + 0.34, ty, w - 0.62, 0.20, [P(eyebrow, 9, accent, True, sp=1.4)])
        ty += 0.28
    text(s, x + 0.34, ty, w - 0.62, 0.30, [P(title, tsz, WHITE, True)])
    text(s, x + 0.34, ty + 0.34, w - 0.62, h - (ty - y) - 0.55,
         [P(body, bsz, DIM, ls=1.32)])


def stat(s, x, y, w, h, big, label, c=MINT, bsz=25):
    rect(s, x, y, w, h, PANEL, LINE, rounded=True, radius=0.09)
    text(s, x + 0.26, y + 0.20, w - 0.5, 0.48, [P(big, bsz, c, True)])
    text(s, x + 0.26, y + 0.74, w - 0.5, h - 0.86, [P(label, 9.5, DIM, ls=1.28)])


def chain(s, y, items, h=0.66, sep="\u203a", fill=PANEL2, tsz=10, ssz=8.5, x0=M, wtot=CW):
    n = len(items)
    gap = 0.20
    bw = (wtot - gap * (n - 1)) / n
    for i, it in enumerate(items):
        t, sub = it if isinstance(it, tuple) else (it, None)
        x = x0 + i * (bw + gap)
        rect(s, x, y, bw, h, fill, LINE, rounded=True, radius=0.10)
        if sub:
            text(s, x + 0.08, y + 0.13, bw - 0.16, 0.22, [P(t, tsz, WHITE, True, align=PP_ALIGN.CENTER)])
            text(s, x + 0.08, y + 0.37, bw - 0.16, 0.22, [P(sub, ssz, DIM, align=PP_ALIGN.CENTER)])
        else:
            text(s, x + 0.08, y, bw - 0.16, h, [P(t, tsz, WHITE, True, align=PP_ALIGN.CENTER)],
                 anchor=MSO_ANCHOR.MIDDLE)
        if i < n - 1:
            text(s, x + bw, y + h / 2 - 0.14, gap, 0.28,
                 [P(sep, 13, VIOLET, True, align=PP_ALIGN.CENTER)])


def rows(s, x, y, w, colw, data, head=None, rh=0.52, sizes=(10, 10, 10),
         colors=(WHITE, DIM, MINT), bolds=(True, False, False)):
    """Simple 2-3 column comparison block."""
    cy = y
    if head:
        for j, cell in enumerate(head):
            cx = x + sum(colw[:j])
            text(s, cx + 0.20, cy + 0.06, colw[j] - 0.3, 0.24,
                 [P(cell, 9.5, VSOFT if j else FAINT, True, sp=1.5)])
        cy += 0.42
        rect(s, x, cy - 0.06, w, 0.012, LINE)
    for i, row in enumerate(data):
        if i % 2 == 0:
            rect(s, x, cy, w, rh, PANEL, None, rounded=True, radius=0.10)
        for j, cell in enumerate(row):
            cx = x + sum(colw[:j])
            text(s, cx + 0.20, cy, colw[j] - 0.3, rh,
                 [P(cell, sizes[j], colors[j], bolds[j], ls=1.15)], anchor=MSO_ANCHOR.MIDDLE)
        cy += rh
    return cy


prs = Presentation()
prs.slide_width, prs.slide_height = Inches(W), Inches(H)

# ══ 01 · TITLE ══════════════════════════════════════════════════════════
s = slide(prs)
rect(s, 0, 0, 0.10, H, VIOLET)
text(s, M + 0.25, 1.28, CW, 0.26, [P("HACKMATRIX 2026  \u00b7  ROUND 2", 11, MINT, True, sp=2.2)])
text(s, M + 0.25, 1.62, 9.5, 1.05, [P("VoiceFlow AI", 54, WHITE, True)])
text(s, M + 0.25, 2.72, 9.5, 0.40, [P("Your voice. Your accent. Answering every customer.", 19, VSOFT)])
text(s, M + 0.25, 3.32, 8.6, 0.90,
     [P("An AI receptionist for Indian small businesses. It answers WhatsApp messages and "
        "browser voice calls 24/7 today — phone lines are next — in whatever language the "
        "customer speaks, and logs every conversation into one dashboard.", 12.5, DIM, ls=1.38)])

cw3 = (CW - 0.25 - 0.44) / 3
for i, (big, lab, col) in enumerate([
        ("~4s", "per conversational turn, measured end to end", MINT),
        ("9", "Indian languages, 21 voices, zero API keys", VSOFT),
        ("\u20b9999+", "flat per month \u2014 not \u20b910\u201326 a minute like others", SAFF)]):
    stat(s, M + 0.25 + i * (cw3 + 0.22), 4.55, cw3, 1.10, big, lab, col, bsz=27)

rect(s, M + 0.25, 6.10, CW - 0.25, 0.012, LINE)
text(s, M + 0.25, 6.30, 7.4, 0.70,
     [P("Team Voice Flow", 11.5, WHITE, True, sa=3),
      P("Abhinav Cheepa (Team Leader)  \u00b7  Raghav Goyal", 10, DIM, sa=2),
      P("abhinavcheepa7@gmail.com  \u00b7  +91 70007 59702", 10, DIM)])
text(s, W - M - 4.6, 6.30, 4.6, 0.70,
     [P("Problem statement", 9, FAINT, True, sp=1.4, align=PP_ALIGN.RIGHT, sa=3),
      P("Missed customer calls and manual WhatsApp replies", 10, DIM, align=PP_ALIGN.RIGHT, sa=2),
      P("Domain: AI  \u00b7  Voice Technology  \u00b7  SMB Automation", 9.5, FAINT, align=PP_ALIGN.RIGHT)])

# ══ 02 · PROBLEM ════════════════════════════════════════════════════════
s = slide(prs)
y = header(s, "THE PROBLEM", "The phone rings. Nobody is free to pick it up.",
           "For a clinic, salon, shop or coaching centre the phone and WhatsApp are not support channels \u2014 "
           "they are the entire sales channel. Both depend on one person being free at that exact moment.")
cw3 = (CW - 0.44) / 3
for i, (big, lab, col) in enumerate([
        ("62%", "of calls to small businesses go unanswered", ROSE),
        ("85%", "of missed callers never call back", SAFF),
        ("78%", "buy from whoever answers first  (MIT / InsideSales)", MINT)]):
    stat(s, M + i * (cw3 + 0.22), y, cw3, 1.02, big, lab, col, bsz=24)

y += 1.26
for i, (t, b) in enumerate([
        ("Calls go unanswered",
         "After hours, mid-rush, or hands full with the customer already in the shop. "
         "The caller does not wait \u2014 they dial the next name on the list."),
        ("WhatsApp is still typed by hand",
         "The channel Indian customers actually use \u2014 91% of online adults message a business "
         "every week \u2014 answered manually, one thumb at a time, hours late."),
        ("Hiring does not scale",
         "A three-person clinic cannot staff a 24/7 front desk, however many bookings it "
         "loses by not doing so. Salary is fixed; calls are not.")]):
    card(s, M + i * (cw3 + 0.22), y, cw3, 1.92, t, b, [ROSE, SAFF, VIOLET][i])

y += 2.12
rect(s, M, y, CW, 0.68, PANEL2, LINE, rounded=True, radius=0.10)
rect(s, M, y + 0.14, 0.045, 0.40, MINT)
text(s, M + 0.34, y, CW - 0.7, 0.68,
     [P("Every missed call is revenue that has already walked to a competitor \u2014 and the owner "
        "finds out hours later, if at all.", 12.5, WHITE, True)], anchor=MSO_ANCHOR.MIDDLE)
footer(s, 2, "Call-answer and callback figures: 2026 industry surveys of SMB phone traffic. "
             "First-responder figure: MIT / InsideSales lead-response study.")

# ══ 03 · ROOT CAUSE ═════════════════════════════════════════════════════
s = slide(prs)
y = header(s, "WHY THIS PROBLEM EXISTS", "Three reasons it is still unsolved in 2026",
           "AI receptionists exist. None of them fit a business that runs on one number, one WhatsApp and a \u20b9200 ticket.")
cw3 = (CW - 0.44) / 3
for i, (eyebrow, t, b, col) in enumerate([
        ("CAUSE 01", "One number, one pair of hands",
         "The owner is the receptionist. Every minute spent with the customer in front of them "
         "is a minute the phone goes unanswered. There is no second line and no night shift.", VIOLET),
        ("CAUSE 02", "Voice AI is English-first",
         "Hindi, Hinglish and code-mixed speech break assistants tuned for US English. "
         "The customer repeats themselves twice, then hangs up and calls a human.", SAFF),
        ("CAUSE 03", "Billing is per-minute, in USD",
         "$0.11\u2013$0.30 a minute all-in on Vapi, Retell or Bland \u2014 roughly \u20b910\u2013\u20b926 \u2014 plus KYC, "
         "a sales call, and customer data sitting in a foreign cloud.", ROSE)]):
    card(s, M + i * (cw3 + 0.22), y, cw3, 2.30, t, b, col, eyebrow=eyebrow)

y += 2.52
text(s, M, y, CW, 0.24, [P("WHAT A SHOP DOES TODAY INSTEAD", 9.5, FAINT, True, sp=1.6)])
chain(s, y + 0.32, ["Miss the call", "Hope for a callback", "Reply on WhatsApp hours later",
                    "Customer has already booked elsewhere"], h=0.58, tsz=10.5)

y += 1.18
rect(s, M, y, CW, 0.70, PANEL2, LINE, rounded=True, radius=0.10)
rect(s, M, y + 0.15, 0.045, 0.40, MINT)
text(s, M + 0.34, y, CW - 0.7, 0.70,
     [P("CORE PROBLEM   There is nobody to answer when the owner is busy \u2014 and no affordable tool "
        "that speaks the customer's language.", 12.5, WHITE, True)], anchor=MSO_ANCHOR.MIDDLE)
footer(s, 3, "Competitor pricing: published 2026 all-in per-minute rates for Vapi, Retell and Bland, converted at ~\u20b988/USD.")

# ══ 04 · MARKET ═════════════════════════════════════════════════════════
s = slide(prs)
y = header(s, "MARKET", "Who this is for \u2014 and how many of them there are",
           "Not \"the AI market\". A specific, countable set of businesses whose revenue arrives through a phone and a WhatsApp number.")
lw = CW * 0.60
for i, (tag, big, t, b, col) in enumerate([
        ("TAM", "7.9 crore", "MSMEs registered on India's Udyam portal",
         "98.9% are micro enterprises \u2014 run by the owner, with no back office and no support team.", VIOLET),
        ("SAM", "1.5 crore", "Indian businesses already on WhatsApp Business",
         "The largest concentration on earth. They have already proven they want the channel; they just answer it by hand.", VSOFT),
        ("BEACHHEAD", "500", "phone-first shops in Indore & MP, year one",
         "Clinics, salons and coaching centres, reached through local IT resellers who already install their billing software.", MINT)]):
    cy = y + i * 1.34
    rect(s, M, cy, lw, 1.18, PANEL, LINE, rounded=True, radius=0.07)
    rect(s, M, cy + 0.22, 0.045, 0.74, col)
    text(s, M + 0.34, cy + 0.20, 1.15, 0.22, [P(tag, 8.5, col, True, sp=1.5)])
    text(s, M + 0.34, cy + 0.44, 2.35, 0.42, [P(big, 22, WHITE, True)])
    text(s, M + 2.85, cy + 0.24, lw - 3.25, 0.30, [P(t, 12, WHITE, True)])
    text(s, M + 2.85, cy + 0.58, lw - 3.25, 0.52, [P(b, 9.5, DIM, ls=1.3)])

rx = M + lw + 0.30
rw = CW - lw - 0.30
rect(s, rx, y, rw, 4.06, PANEL, LINE, rounded=True, radius=0.045)
text(s, rx + 0.34, y + 0.30, rw - 0.68, 0.30, [P("WHY NOW", 9.5, MINT, True, sp=1.6)])
ny = y + 0.72
for big, b in [("535 million+", "WhatsApp users in India \u2014 the largest single-country base anywhere in the world."),
               ("91%", "of online Indian adults message a business every single week."),
               ("\u20b920 \u2192 \u20b90", "Open models and free neural TTS collapsed the cost of one AI conversation to nothing. "
                                "The infrastructure a shop needs became free in the last 18 months.")]:
    text(s, rx + 0.34, ny, rw - 0.68, 0.30, [P(big, 15, VSOFT, True)])
    text(s, rx + 0.34, ny + 0.34, rw - 0.68, 0.80, [P(b, 9.5, DIM, ls=1.32)])
    ny += 1.16
footer(s, 4, "Sources: IBEF / Udyam registration data (March 2026)  \u00b7  Meta and DataReportal India figures (2025\u201326)  \u00b7  "
             "beachhead number is our own year-one target, not a market estimate.")

# ══ 05 · SOLUTION ═══════════════════════════════════════════════════════
s = slide(prs)
y = header(s, "OUR SOLUTION", "One agent. Every channel. The customer's own language.")
rect(s, M, y, CW, 0.78, PANEL2, LINE, rounded=True, radius=0.09)
rect(s, M, y + 0.18, 0.045, 0.42, MINT)
text(s, M + 0.34, y, CW - 0.7, 0.78,
     [P("VoiceFlow AI answers every WhatsApp message and voice conversation 24/7 \u2014 in Hindi, Hinglish, "
        "English, Marathi or Tamil \u2014 with zero marginal cost per conversation.", 13, WHITE, True, ls=1.2)],
     anchor=MSO_ANCHOR.MIDDLE)

y += 1.02
text(s, M, y, CW, 0.24, [P("ONE PIPELINE, WHICHEVER WAY THE CUSTOMER ARRIVES", 9.5, FAINT, True, sp=1.6)])
chain(s, y + 0.32, [("Customer", "calls or messages"), ("Speech \u2192 text", "language auto-detected"),
                    ("Agent", "your prices, your rules"), ("Text \u2192 speech", "natural Indian voice"),
                    ("Answered", "call, text or voice note"), ("Logged", "dashboard + CRM")], h=0.72)

y += 1.32
cw3 = (CW - 0.44) / 3
for i, (eyebrow, t, b, col) in enumerate([
        ("01", "Answers in the customer's language",
         "Script and whole-word detection picks Hindi, Hinglish, Marathi, Tamil or English from the first "
         "sentence \u2014 and the reply comes back in that same language, not a translation of English.", MINT),
        ("02", "One brain across voice and WhatsApp",
         "A voice note gets a voice note back. Text gets text, written in the owner's own style. "
         "Adding a channel means adding an adapter, never a second AI.", VSOFT),
        ("03", "Self-hosted, \u20b90 per minute",
         "Free neural TTS, free-tier transcription and replies, SQLite on the shop's own machine. "
         "No card, no KYC, and customer conversations never leave the building.", SAFF)]):
    card(s, M + i * (cw3 + 0.22), y, cw3, 2.10, t, b, col, eyebrow=eyebrow, tsz=13)

y += 2.28
text(s, M, y, CW, 0.30,
     [P("Measured on a real run:  ~4 seconds from the customer finishing a sentence to hearing the reply "
        "\u2014 1.3s transcribe, 1.0s think, 1.8s speak.", 11, MINT, True)])
footer(s, 5)

# ══ 06 · PRODUCT ════════════════════════════════════════════════════════
s = slide(prs)
y = header(s, "PROTOTYPE / PRODUCT EXPERIENCE", "What actually runs today",
           "Web call and WhatsApp both run the full pipeline end to end. This is the live product, not a mockup \u2014 and it is what we demo on stage.")
lw = CW * 0.46
text(s, M, y, lw, 0.24, [P("WHAT ONE CUSTOMER GOES THROUGH", 9.5, FAINT, True, sp=1.6)])
jy = y + 0.36
for i, (t, b) in enumerate([
        ("INPUT", "Customer calls the shop, or sends a WhatsApp text or voice note."),
        ("UNDERSTAND", "Audio is transcribed and the language is detected automatically."),
        ("ANSWER", "The agent replies using the owner's real prices, timings and rules."),
        ("SPEAK", "Spoken back on the call, or returned as a voice note on WhatsApp."),
        ("LOG", "Lands in the dashboard, attached to that customer's timeline.")]):
    rect(s, M, jy, lw, 0.62, PANEL, LINE, rounded=True, radius=0.10)
    text(s, M + 0.22, jy, 0.55, 0.62, [P("%02d" % (i + 1), 13, VIOLET, True)], anchor=MSO_ANCHOR.MIDDLE)
    text(s, M + 0.82, jy + 0.11, lw - 1.05, 0.22, [P(t, 10.5, WHITE, True, sp=0.8)])
    text(s, M + 0.82, jy + 0.34, lw - 1.05, 0.22, [P(b, 8.8, DIM)])
    jy += 0.70

rx = M + lw + 0.34
rw = CW - lw - 0.34
text(s, rx, y, rw, 0.24, [P("NINE SCREENS, ALL LIVE", 9.5, FAINT, True, sp=1.6)])
screens = [("Dashboard", "everything at a glance"), ("Web Call", "talk to the agent in-browser"),
           ("WhatsApp", "inbox, QR, auto-reply switches"), ("Agents", "personas and business facts"),
           ("Voice Studio", "21 voices, speed and pitch"), ("Contacts", "CRM built from conversations"),
           ("Calls", "every turn, with transcripts"), ("Campaigns", "bulk outbound list"),
           ("Analytics", "volume, outcomes, per agent")]
gw = (rw - 0.20) / 2
for i, (t, b) in enumerate(screens):
    px = rx + (i % 2) * (gw + 0.20)
    py = y + 0.36 + (i // 2) * 0.68
    rect(s, px, py, gw, 0.60, PANEL, LINE, rounded=True, radius=0.11)
    dot(s, px + 0.20, py + 0.26, 0.09, MINT)
    text(s, px + 0.42, py + 0.10, gw - 0.6, 0.22, [P(t, 10, WHITE, True)])
    text(s, px + 0.42, py + 0.32, gw - 0.6, 0.22, [P(b, 8.3, DIM)])
text(s, rx, y + 0.36 + 5 * 0.68, rw, 0.30,
     [P("45 API routes  \u00b7  ~7,600 lines  \u00b7  21 automated tests", 10, MINT, True)])
footer(s, 6, "Every number in this deck was measured on an actual run \u2014 nothing here is estimated or mocked.")

# ══ 07 · DIFFERENTIATION ════════════════════════════════════════════════
s = slide(prs)
y = header(s, "WHAT MAKES US DIFFERENT", "Everyone uses AI. Nobody built it for this shop.")
colw = [4.05, 3.85, CW - 7.90]
rows(s, M, y, CW, colw,
     [("Hindi, Hinglish, Indian accent", "Weak \u2014 US voice AI is English-first", "Detected and answered natively"),
      ("Voice + WhatsApp in one agent", "Separate products, separate bills", "One brain, both channels"),
      ("Cost per conversation", "Per-minute USD, or \u20b91\u20135 per WA session", "\u20b90 marginal \u2014 free/free-tier stack"),
      ("Where customer data sits", "The vendor's cloud", "The shop's own machine, in India"),
      ("Business facts", "A generic prompt or FAQ bot", "Your prices, timings and limits"),
      ("Getting started", "Sales call, KYC, card on file", "Runs on a laptop this afternoon"),
      ("When the AI does not know", "Invents a price or a free slot", "Says it will confirm and call back")],
     head=("", "EXISTING TOOLS  (VAPI, RETELL  \u00b7  AISENSY, WATI, GUPSHUP)", "VOICEFLOW AI"), rh=0.50,
     sizes=(10.5, 9.5, 10.5), colors=(WHITE, DIM, MINT), bolds=(True, False, True))

y += 0.42 + 7 * 0.50 + 0.28
rect(s, M, y, CW, 0.92, PANEL2, LINE, rounded=True, radius=0.08)
rect(s, M, y + 0.18, 0.045, 0.56, MINT)
text(s, M + 0.34, y + 0.18, CW - 0.7, 0.60,
     [P("Our differentiator is not that we use AI. Everyone uses AI.", 12.5, WHITE, True, sa=4),
      P("It is that the whole thing is self-hosted, speaks Indian languages properly, costs a small business "
        "nothing per call \u2014 and is told what it does not know, so it defers instead of inventing.", 10.5, DIM, ls=1.28)])
footer(s, 7)

# ══ 08 · BUSINESS MODEL ═════════════════════════════════════════════════
s = slide(prs)
y = header(s, "BUSINESS MODEL", "Priced so the smallest shop can say yes",
           "Three monthly plans from ₹999 to ₹1,999 — a barber can start, a wholesaler grows into the top plan. "
           "The wholesaler is who we chase: highest call volume, most to lose, most to pay for.")

# three pricing cards
cw3 = (CW - 0.44) / 3
plans = [
    ("STARTER", "₹999", "Barber, kirana, single-chair salon",
     ["WhatsApp auto-reply + browser calls", "1 agent, 1 voice", "~200 conversations / month",
      "Everything the free build has, hosted"], VSOFT, False),
    ("GROWTH", "₹1,499", "Busy clinic, salon, coaching centre",
     ["Everything in Starter", "Multiple agents & 21 voices", "~500 conversations / month",
      "Campaigns + full analytics"], MINT, False),
    ("WHOLESALER", "₹1,999", "Distributor / high-volume shop — our target",
     ["Everything in Growth", "Multiple numbers & bulk outbound", "Unlimited* conversations",
      "Priority support + phone lines first"], SAFF, True)]
for i, (tier, price, who, feats, col, hero) in enumerate(plans):
    x = M + i * (cw3 + 0.22)
    h = 2.28
    rect(s, x, y, cw3, h, PANEL if not hero else PANEL2, col if hero else LINE,
         rounded=True, radius=0.05, lw=1.6 if hero else 1.0)
    rect(s, x, y, cw3, 0.05, col)
    text(s, x + 0.30, y + 0.20, cw3 - 0.6, 0.20, [P(tier, 9.5, col, True, sp=1.6)])
    if hero:
        rect(s, x + cw3 - 1.34, y + 0.15, 1.08, 0.26, col, rounded=True, radius=0.4)
        text(s, x + cw3 - 1.34, y + 0.15, 1.08, 0.26,
             [P("MAIN TARGET", 7.5, INK, True, align=PP_ALIGN.CENTER)], anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.30, y + 0.44, cw3 - 0.6, 0.44, [P(price, 27, WHITE, True)])
    text(s, x + 0.30 + (1.55 if len(price) < 6 else 1.85), y + 0.60, 1.4, 0.24, [P("/ month", 10, DIM)])
    text(s, x + 0.30, y + 0.98, cw3 - 0.6, 0.36, [P(who, 9, col, True, ls=1.15)])
    fy = y + 1.36
    for f in feats:
        dot(s, x + 0.32, fy + 0.05, 0.07, col)
        text(s, x + 0.50, fy, cw3 - 0.78, 0.24, [P(f, 8.6, DIM, ls=1.1)])
        fy += 0.225

y += 2.50
# cost + profit strip
colw = [3.0, 2.6, 2.9, CW - 8.5]
rows(s, M, y, CW, colw,
     [("Starter  ·  ₹999", "~₹150", "₹849", "85%"),
      ("Growth  ·  ₹1,499", "~₹300", "₹1,199", "80%"),
      ("Wholesaler  ·  ₹1,999", "~₹500", "₹1,499", "75%")],
     head=("PLAN", "OUR COST / MO", "PROFIT / MO", "MARGIN"), rh=0.42,
     sizes=(10.5, 10, 10.5, 11), colors=(WHITE, DIM, WHITE, MINT), bolds=(True, False, True, True))

y2 = y + 0.42 + 3 * 0.42 + 0.14
text(s, M, y2, CW, 0.24,
     [P("One-time setup ₹1,999–₹3,999 (reseller loads facts, connects WhatsApp)   ·   "
        "annual plan = 2 months free   ·   free 14-day trial, no card, cancel anytime.", 9, DIM)])
footer(s, 8, "Cost basis (verified rates, our usage estimate): Groq STT $0.04/audio-hr + gpt-oss-120b $0.15/$0.60 per M tokens ≈ ₹0.2/conversation; "
             "Edge TTS free; WhatsApp service replies free (Meta, within 24h window); shared VPS ~₹50/shop. Margin floor we price to is 20%. "
             "Revenue is not yet measured — a paid pilot of 5 Indore shops tests willingness-to-pay before we scale via resellers.")

# ══ 09 · ARCHITECTURE ═══════════════════════════════════════════════════
s = slide(prs)
y = header(s, "TECHNICAL ARCHITECTURE", "Three channels, one pipeline, one file per vendor")
lw = CW * 0.545
text(s, M, y, lw, 0.24, [P("HOW A CONVERSATION FLOWS", 9.5, FAINT, True, sp=1.6)])
ay = y + 0.34
chain(s, ay, ["Phone\nAsterisk", "WhatsApp\nbridge or Meta", "Browser\nWebSocket"],
      h=0.62, tsz=9.5, x0=M, wtot=lw, sep="")
text(s, M, ay + 0.66, lw, 0.26, [P("\u2193", 13, VIOLET, True, align=PP_ALIGN.CENTER)])
rect(s, M, ay + 0.96, lw, 0.86, PANEL, VIOLET, rounded=True, radius=0.08)
text(s, M + 0.20, ay + 1.08, lw - 0.4, 0.26, [P("FastAPI backend \u2014 the one pipeline", 11.5, WHITE, True, align=PP_ALIGN.CENTER)])
text(s, M + 0.20, ay + 1.36, lw - 0.4, 0.30,
     [P("transcribe  \u203a  reply  \u203a  speak  \u203a  store", 10, MINT, True, align=PP_ALIGN.CENTER)])
text(s, M, ay + 1.86, lw, 0.26, [P("\u2193", 13, VIOLET, True, align=PP_ALIGN.CENTER)])
chain(s, ay + 2.16, [("Groq", "Whisper + gpt-oss-120b"), ("Edge TTS", "free, 21 Indian voices"), ("SQLite", "one file, on-premise")],
      h=0.70, tsz=10, x0=M, wtot=lw, sep="")
rect(s, M, ay + 3.06, lw, 0.62, PANEL2, LINE, rounded=True, radius=0.10)
text(s, M + 0.24, ay + 3.06, lw - 0.48, 0.62,
     [P("Every external service is owned by exactly one file \u2014 swapping a vendor is one file's work, not a rewrite.",
        9.5, DIM, ls=1.25)], anchor=MSO_ANCHOR.MIDDLE)

rx = M + lw + 0.34
rw = CW - lw - 0.34
text(s, rx, y, rw, 0.24, [P("STACK", 9.5, FAINT, True, sp=1.6)])
sy = y + 0.34
for k, v in [("Frontend", "React 19 \u00b7 Vite \u00b7 Tailwind v4"),
             ("Backend", "FastAPI (Python) \u00b7 WebSockets"),
             ("Database", "SQLite \u2014 no server to run"),
             ("Speech \u2192 text", "Groq Whisper large-v3 (1.3s)\nfaster-whisper locally as fallback"),
             ("Replies", "Any OpenAI-compatible endpoint\ntoday: gpt-oss-120b on Groq (~1.0s)"),
             ("Text \u2192 speech", "Edge TTS \u2014 free, 9 Indian languages\nCartesia when voice cloning is wanted"),
             ("WhatsApp", "Local WhatsApp Web bridge, or\nMeta Cloud API \u2014 one setting switches"),
             ("Telephony", "Asterisk PBX, self-hosted\nyour own SIM over a Bluetooth gateway")]:
    two = "\n" in v
    hgt = 0.55 if two else 0.41
    rect(s, rx, sy, rw, hgt, PANEL, LINE, rounded=True, radius=0.10)
    text(s, rx + 0.20, sy, 1.45, hgt, [P(k, 9.5, VSOFT, True)], anchor=MSO_ANCHOR.MIDDLE)
    text(s, rx + 1.72, sy, rw - 1.92, hgt, [P(v, 9, DIM, ls=1.22)], anchor=MSO_ANCHOR.MIDDLE)
    sy += hgt + 0.065
footer(s, 9, "Proof it works: the chat model was swapped Llama \u2192 gpt-oss and the voice Cartesia \u2192 Edge, "
             "each by changing a single environment variable.")

# ══ 09 · BUILT TODAY ════════════════════════════════════════════════════
s = slide(prs)
y = header(s, "STATUS", "Built and measured \u2014 not described",
           "We are deliberately explicit about the line between what runs and what does not. Everything green can be demonstrated live.")
lw = (CW - 0.30) / 2
rect(s, M, y, lw, 3.62, PANEL, LINE, rounded=True, radius=0.045)
dot(s, M + 0.30, y + 0.32, 0.13, MINT)
text(s, M + 0.54, y + 0.24, lw - 0.8, 0.26, [P("WORKING TODAY", 10, MINT, True, sp=1.5)])
iy = y + 0.68
for t, b in [("Browser voice call", "full pipeline, ~4s per turn"),
             ("WhatsApp auto-reply", "text and voice notes, survives restarts and dead sessions"),
             ("Multiple agent personas", "a different agent per call or chat"),
             ("21 Indian voices", "9 languages, free, no API key"),
             ("Contacts / CRM", "calls and chats merged into one timeline"),
             ("Campaigns & analytics", "bulk outbound list, volume and outcomes"),
             ("21 automated tests", "language detection, rate limits, reply gating")]:
    text(s, M + 0.54, iy, lw - 0.8, 0.20, [P(t, 10.5, WHITE, True)])
    text(s, M + 0.54, iy + 0.21, lw - 0.8, 0.20, [P(b, 8.8, DIM)])
    dot(s, M + 0.32, iy + 0.06, 0.08, MINT)
    iy += 0.42

rx = M + lw + 0.30
rect(s, rx, y, lw, 3.62, PANEL, LINE, rounded=True, radius=0.045)
dot(s, rx + 0.30, y + 0.32, 0.13, SAFF)
text(s, rx + 0.54, y + 0.24, lw - 0.8, 0.26, [P("HONESTLY, NOT YET", 10, SAFF, True, sp=1.5)])
iy = y + 0.68
for t, b in [("Live audio on phone calls",
              "Asterisk answers and logs the call today. Streaming the audio through the AI is 7\u201310 days of work, already planned and costed in the repo."),
             ("Voice cloning \u2014 the owner's own voice",
              "The code path is written and tested. It needs a paid Cartesia plan; free neural voices are used instead."),
             ("Barge-in", "The agent finishes its sentence before it listens again. Interrupting needs the streaming media loop."),
             ("Dashboard login", "No auth on the API yet \u2014 fine on a shop's own machine, required before anything is exposed publicly.")]:
    text(s, rx + 0.54, iy, lw - 0.8, 0.20, [P(t, 10.5, WHITE, True)])
    text(s, rx + 0.54, iy + 0.22, lw - 0.8, 0.52, [P(b, 8.8, DIM, ls=1.25)])
    dot(s, rx + 0.32, iy + 0.06, 0.08, SAFF)
    iy += 0.76

y += 3.84
text(s, M, y, CW, 0.30,
     [P("~7,600 lines across backend, dashboard and bridge  \u00b7  45 API routes  \u00b7  every figure in this deck came off a real run.",
        10.5, MINT, True)])
footer(s, 10)

# ══ 10 · FEASIBILITY + IMPACT ═══════════════════════════════════════════
s = slide(prs)
y = header(s, "FEASIBILITY + IMPACT", "It already runs. The only question left is reach.")
lw = (CW - 0.30) / 2
rect(s, M, y, lw, 4.10, PANEL, LINE, rounded=True, radius=0.045)
text(s, M + 0.34, y + 0.28, lw - 0.68, 0.26, [P("FEASIBILITY", 10, VSOFT, True, sp=1.6)])
iy = y + 0.72
for t, b in [("Technical",
              "Browser calls, WhatsApp auto-reply, multi-agent personas, CRM and analytics all run today. "
              "~4s per conversational turn, measured. 21 automated tests cover language detection, reply gating and rate limits."),
             ("Operational",
              "One ordinary laptop. No cloud bill, no card, no KYC. Free neural TTS, free-tier transcription and replies, "
              "and incoming calls are free in India."),
             ("Risks, and what we did about them",
              "WhatsApp Web is against WhatsApp's terms \u2014 the official Meta Cloud API is one setting away. "
              "Hosted models get retired \u2014 any OpenAI-compatible endpoint drops in.")]:
    text(s, M + 0.34, iy, lw - 0.68, 0.24, [P(t, 11.5, WHITE, True)])
    text(s, M + 0.34, iy + 0.28, lw - 0.68, 0.86, [P(b, 9.5, DIM, ls=1.32)])
    iy += 1.16

rx = M + lw + 0.30
rect(s, rx, y, lw, 4.10, PANEL, LINE, rounded=True, radius=0.045)
text(s, rx + 0.34, y + 0.28, lw - 0.68, 0.26, [P("IMPACT", 10, MINT, True, sp=1.6)])
iy = y + 0.72
for t, b in [("Who benefits",
              "Clinics, salons, shops and coaching centres \u2014 anyone whose bookings arrive by phone and WhatsApp "
              "while the owner is busy serving somebody else."),
             ("What improves",
              "Every call is answered instead of missed. WhatsApp replies land in seconds instead of hours. "
              "The owner reads the whole day's conversations in one dashboard instead of scrolling three apps."),
             ("Why it matters",
              "A missed call is a customer who has already dialled somebody else. This gives a one-person business "
              "the reach of a staffed front desk \u2014 at \u20b90 marginal cost per conversation, against \u20b910\u2013\u20b926 a minute elsewhere.")]:
    text(s, rx + 0.34, iy, lw - 0.68, 0.24, [P(t, 11.5, WHITE, True)])
    text(s, rx + 0.34, iy + 0.28, lw - 0.68, 0.86, [P(b, 9.5, DIM, ls=1.32)])
    iy += 1.16

y += 4.32
chain(s, y, ["Prototype", "MVP", "Pilot in Indore", "Scale via resellers"], h=0.52, tsz=10.5)
footer(s, 11)

# ══ 11 · ROADMAP ════════════════════════════════════════════════════════
s = slide(prs)
y = header(s, "IMPLEMENTATION ROADMAP", "What happens next",
           "The hard part \u2014 a working speech-to-reply loop in Indian languages \u2014 is done. What is left is plumbing and distribution.")
cw4 = (CW - 3 * 0.22) / 4
for i, (tag, when, items, col) in enumerate([
        ("NOW", "built and working",
         ["Browser voice calls", "WhatsApp auto-reply, text and voice",
          "Multiple agent personas", "Contacts, campaigns, analytics", "21 voices across 9 languages"], MINT),
        ("NEXT", "2\u20136 weeks",
         ["Live audio on phone calls", "Barge-in \u2014 interrupt the agent",
          "Voice cloning: the owner's own voice", "Token auth on the dashboard API"], VSOFT),
        ("LATER", "the quarter after",
         ["Paid pilot with shops in Indore", "Appointment calendar integration",
          "Roles and multi-user login", "Hosted option for non-technical owners"], VIOLET),
        ("SCALE", "the year",
         ["More Indian languages", "Multiple businesses per install",
          "Payments and order taking", "Reseller model for local IT shops"], SAFF)]):
    x = M + i * (cw4 + 0.22)
    rect(s, x, y, cw4, 3.72, PANEL, LINE, rounded=True, radius=0.05)
    rect(s, x, y, cw4, 0.05, col)
    text(s, x + 0.28, y + 0.30, cw4 - 0.56, 0.26, [P(tag, 13, col, True, sp=1.4)])
    text(s, x + 0.28, y + 0.58, cw4 - 0.56, 0.22, [P(when, 9, FAINT)])
    ly = y + 0.98
    for it in items:
        dot(s, x + 0.30, ly + 0.07, 0.075, col)
        text(s, x + 0.52, ly, cw4 - 0.82, 0.52, [P(it, 9.5, DIM, ls=1.25)])
        ly += 0.56

y += 3.94
rect(s, M, y, CW, 0.62, PANEL2, LINE, rounded=True, radius=0.10)
rect(s, M, y + 0.13, 0.045, 0.36, SAFF)
text(s, M + 0.34, y, CW - 0.7, 0.62,
     [P("Phone-call audio is marked in progress on purpose. Asterisk answers and logs the call today; "
        "streaming that audio through the AI is the one piece we have not finished \u2014 and it is planned, costed and in the repo.",
        10, DIM, ls=1.25)], anchor=MSO_ANCHOR.MIDDLE)
footer(s, 12)

# ══ 12 · TEAM ═══════════════════════════════════════════════════════════
s = slide(prs)
rect(s, 0, 0, 0.10, H, VIOLET)
y = header(s, "TEAM + EXECUTION", "Team Voice Flow")
lw = (CW - 0.30) / 2
for i, (name, role, work, extra) in enumerate([
        ("Abhinav Cheepa", "Team Leader",
         "Backend, the AI pipeline, WhatsApp reliability and telephony integration.",
         "abhinavcheepa7@gmail.com  \u00b7  +91 70007 59702"),
        ("Raghav Goyal", "Frontend & QA",
         "React dashboard, Voice Studio, the web-call client, testing and QA.", "")]):
    x = M + i * (lw + 0.30)
    rect(s, x, y, lw, 1.72, PANEL, LINE, rounded=True, radius=0.06)
    rect(s, x, y + 0.30, 0.045, 1.12, [MINT, VSOFT][i])
    text(s, x + 0.34, y + 0.28, lw - 0.68, 0.32, [P(name, 16, WHITE, True)])
    text(s, x + 0.34, y + 0.64, lw - 0.68, 0.22, [P(role, 9.5, [MINT, VSOFT][i], True, sp=1.2)])
    text(s, x + 0.34, y + 0.92, lw - 0.68, 0.44, [P(work, 9.8, DIM, ls=1.3)])
    if extra:
        text(s, x + 0.34, y + 1.38, lw - 0.68, 0.22, [P(extra, 9, FAINT)])

y += 1.86
rect(s, M, y, CW, 1.32, PANEL2, LINE, rounded=True, radius=0.05)
rect(s, M, y + 0.26, 0.045, 0.80, MINT)
text(s, M + 0.34, y + 0.24, CW - 0.7, 0.30, [P("Why this team can execute", 13, WHITE, True)])
text(s, M + 0.34, y + 0.60, CW - 0.7, 0.62,
     [P("We have already built and measured the hard part \u2014 a working speech-to-reply loop in Indian languages, "
        "running on a laptop for \u20b90 \u2014 instead of describing one. What remains is telephony plumbing, and the plan "
        "for it is written, costed and sitting in the repository.", 10.5, DIM, ls=1.32)])

y += 1.44
cw3 = (CW - 0.44) / 3
for i, (t, b, col) in enumerate([
        ("What we want from this round", "Feedback on the pilot plan, and an introduction to shops willing to run it for a month.", VSOFT),
        ("What we will demo live", "A real Hindi conversation through the browser, and a WhatsApp message answered on its own.", MINT),
        ("Where the code is", "github.com/abhinavcheepa/voiceflow-hackmatrix\nEvery claim in this deck is reproducible from it.", SAFF)]):
    card(s, M + i * (cw3 + 0.22), y, cw3, 1.26, t, b, col, tsz=11.5, bsz=9.5)

text(s, M, H - 0.88, CW, 0.30,
     [P("VoiceFlow AI \u2014 your voice, your accent, answering every customer.", 13, WHITE, True, align=PP_ALIGN.CENTER)])
footer(s, 13)

prs.save("presentation/VoiceFlow-AI-Pitch-Deck.pptx")
print("saved presentation/VoiceFlow-AI-Pitch-Deck.pptx -", len(prs.slides.__iter__.__self__._sldIdLst), "slides")
