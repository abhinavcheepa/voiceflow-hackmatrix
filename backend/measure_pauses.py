# -*- coding: utf-8 -*-
"""Real pause per punctuation mark, in the app's exact Edge TTS config.

Edge outputs a constant 48 kbit/s mono mp3 (6000 bytes/sec), so audio
duration = len(bytes)/6000. Same two words, only the mark between them
changes; the extra audio length is the silence that mark adds. Averaged
over N runs because Edge varies slightly run to run.

Run:  venv/Scripts/python.exe measure_pauses.py [voice] [rate] [runs]
"""
import asyncio, sys, edge_tts

VOICE = sys.argv[1] if len(sys.argv) > 1 else "hi-IN-SwaraNeural"
RATE  = sys.argv[2] if len(sys.argv) > 2 else "+15%"
RUNS  = int(sys.argv[3]) if len(sys.argv) > 3 else 3
PITCH = "+0Hz"
BPS   = 6000.0  # bytes/sec at 48 kbit/s

CASES = [
    ("space  (baseline)", "aaj kal"),
    (",  comma",          "aaj, kal"),
    (".  full stop",      "aaj. kal"),
    ("!  exclamation",    "aaj! kal"),
    ("?  question",       "aaj? kal"),
    (";  semicolon",      "aaj; kal"),
    (":  colon",          "aaj: kal"),
    ("...  ellipsis",     "aaj... kal"),
    ("-  hyphen-space",   "aaj - kal"),
    ("newline",           "aaj\nkal"),
]

async def duration(text):
    n = 0
    async for ch in edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH).stream():
        if ch["type"] == "audio":
            n += len(ch["data"])
    return n / BPS

async def avg(text):
    xs = [await duration(text) for _ in range(RUNS)]
    return sum(xs) / len(xs)

async def main():
    print(f"voice={VOICE}  rate={RATE}  pitch={PITCH}  runs={RUNS}\n")
    base = await avg(CASES[0][1])
    print(f"{'mark':20}{'total (s)':>11}{'pause added':>13}")
    print("-" * 44)
    for label, text in CASES:
        d = await avg(text)
        print(f"{label:20}{d:>11.3f}{d-base:>+13.3f}")
    print(f"\nbaseline (plain word gap) = {base:.3f}s")
    print("'pause added' = extra silence vs the baseline, in seconds.")

asyncio.run(main())
