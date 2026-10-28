"""Checks for speaker-gender agreement and the agentic search tool.

    python -m pytest test_gender_rag.py
"""

import os
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.pop("VOICE_GENDER", None)

import brain  # noqa: E402
import db  # noqa: E402
import tools  # noqa: E402
import voice  # noqa: E402

db.init()


def test_female_voice_gets_feminine_first_person():
    f = lambda t: brain.fix_gender(t, "female")  # noqa: E731
    assert f("Main aapki madad kar sakta hoon.") == "Main aapki madad kar sakti hoon."
    assert f("main dekh raha hoon") == "main dekh rahi hoon"
    assert f("Main confirm karunga.") == "Main confirm karungi."
    assert f("मैं आपकी मदद कर सकता हूँ।") == "मैं आपकी मदद कर सकती हूँ।"
    assert f("मैं समझ गया हूँ") == "मैं समझ गई हूँ"
    assert f("मैं बता दूँगा") == "मैं बता दूँगी"


def test_male_voice_gets_masculine_first_person():
    m = lambda t: brain.fix_gender(t, "male")  # noqa: E731
    assert m("Main kar sakti hoon") == "Main kar sakta hoon"
    assert m("main samajh gayi hoon") == "main samajh gaya hoon"
    assert m("main bata dungi") == "main bata dunga"
    assert m("मैं जानती हूँ") == "मैं जानता हूँ"


def test_names_and_other_people_are_left_alone():
    f = lambda t: brain.fix_gender(t, "female")  # noqa: E731
    for text in ("Main Geeta hoon.", "मैं गीता हूँ", "Aap kar sakte hain.",
                 "Wo kal aayega.", "Ek lungi chahiye", "Main hum ke saath hoon"):
        assert f(text) == text, text


def test_voice_gender_comes_from_the_voice():
    assert voice.gender("hi-IN-MadhurNeural") == "male"
    assert voice.gender("hi-IN-SwaraNeural") == "female"
    assert "WOMAN" in brain.gender_rule("female") and "MAN" in brain.gender_rule("male")


def test_search_reads_stock_and_scopes_personal_data_to_the_caller():
    db.add_stock_item(name="Cement bag", quantity=40, rate=350)
    out = tools.run("search_knowledge", {"query": "cement ka stock kitna hai", "source": "stock"}, {})
    assert "Cement bag" in out

    db.add_booking(item="study table", phone="911111", channel="whatsapp")
    db.add_booking(item="sofa", phone="922222", channel="whatsapp")
    mine = tools.run("search_knowledge", {"query": "my booking", "source": "bookings"},
                     {"phone": "911111"})
    assert "study table" in mine and "sofa" not in mine

    anon = tools.run("search_knowledge", {"query": "my booking", "source": "bookings"}, {})
    assert "not identified" in anon


def test_search_says_so_when_nothing_matches():
    out = tools.run("search_knowledge", {"query": "quantum chromodynamics", "source": "documents"},
                    {"agent_id": 4242})
    assert out.startswith("Nothing found")
