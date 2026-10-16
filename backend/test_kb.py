"""Checks for the knowledge base — parsing, chunking, retrieval.

    python -m pytest test_kb.py
"""

import os
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

import db  # noqa: E402
import kb  # noqa: E402

db.init()


def test_extract_text_reads_csv_as_readable_rows():
    data = b"Item,Price\nStudy table,4800\nChair,1200\n"
    out = kb.extract_text("prices.csv", data)
    assert "Study table | 4800" in out
    assert "Chair | 1200" in out


def test_extract_text_rejects_unknown_types():
    try:
        kb.extract_text("logo.png", b"\x89PNG")
    except ValueError as e:
        assert "Unsupported" in str(e)
    else:
        raise AssertionError("a binary image must be rejected, not stored")


def test_chunk_keeps_short_blocks_whole_and_splits_long_ones():
    text = "Line one.\n\nLine two.\n\n" + ("x" * 1500)
    chunks = kb.chunk(text)
    assert "Line one." in chunks
    assert "Line two." in chunks
    assert all(len(c) <= kb.CHUNK_CHARS for c in chunks), "no chunk may exceed the cap"


def test_search_returns_the_relevant_chunk():
    """The whole point: a question pulls the matching document line, not noise."""
    agent = 4242
    kb.add_document(agent, "menu.md",
                    b"Paneer tikka costs 250 rupees.\n\nGulab jamun is 80 rupees per plate.")
    hits = kb.search(agent, "paneer tikka price")
    assert hits, "a matching query must return something"
    assert "Paneer tikka" in hits[0]


def test_search_is_scoped_to_the_agent_plus_global():
    """One agent's private upload must not leak into another agent's answers."""
    kb.add_document(1001, "a.txt", b"Secret alpha widget is 999.")
    kb.add_document(None, "global.txt", b"Store open till 9pm every day.")

    other = kb.search(2002, "alpha widget")
    assert not any("alpha widget" in h.lower() for h in other), "private doc leaked across agents"

    # The global doc is visible to everyone.
    assert kb.search(2002, "store open till")


def test_context_block_empty_when_nothing_matches():
    assert kb.context_block(7777, "quantum chromodynamics") == ""


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all passed")
