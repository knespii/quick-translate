import json

import pytest

from qtrans.llm import LLM, LLMError
from qtrans.paragraph import MAX_PARAGRAPH_CHARS, cap, clean_selection, guess_direction, is_paragraph

CS_PARA = ("Pole se maže při každém zavření, nejen přes Esc, ale i přes Super+T, kliknutím mimo okno a po "
           "zkopírování Enterem. Vymaže se až po doběhnutí zavírací animace, aby text při zavírání neblikl.")
EN_PARA = ("The field is cleared every time the window closes, not only with Esc. It is cleared only after the "
           "closing animation has finished, so that the text does not flicker.")


@pytest.mark.parametrize("q", ["", "  ", "pes", "dog", "dát si pozor", "give up", "to be or not to be",
                               "Ahoj.", "What?", "jedna dva tři čtyři pět šest sedm osm"])
def test_queries_are_not_paragraphs(q):
    assert not is_paragraph(q)


@pytest.mark.parametrize("q", ["jedna dva tři čtyři pět šest sedm osm devět", "Ahoj. Jak se máš",
                               "Stop! Wait", "Hm… no", "první\ndruhý", CS_PARA, EN_PARA])
def test_paragraphs(q):
    assert is_paragraph(q)


def test_eight_vs_nine_words():
    assert not is_paragraph(" ".join(["slovo"] * 8))
    assert is_paragraph(" ".join(["slovo"] * 9))


@pytest.mark.parametrize("text,want", [
    (CS_PARA, "cs-en"),
    ("Pole se maze pri kazdem zavreni, nejen pres Esc, ale i pres Super+T a po zkopirovani. "
     "Vymaze se az po dobehnuti animace, aby text nebliknul.", "cs-en"),
    ("Je to tak, ale ne pro ten den, kdy jsme byli v Praze.", "cs-en"),
    (EN_PARA, "en-cs"),
    ("I met Jiří Dvořák in Brno and we talked about the weather for hours.", "en-cs"),
    ("Mixed: the program is called Překladač and it is fast. Ten program je fakt rychlý, " 
     "ale to je jedno, protože já ho mám rád a používám ho každý den.", "cs-en"),
    ("", "en-cs"),
    ("123 456", "en-cs"),
])
def test_guess_direction(text, want):
    assert guess_direction(text) == want


def test_clean_hard_wrapped_selection():
    sel = ("  The field is cleared   every time\nthe window closes,\r\nnot only with Esc.\n\n\n\n"
           "Second\tparagraph\nhere.  \n")
    assert clean_selection(sel) == ("The field is cleared every time the window closes, not only with Esc."
                                    "\n\nSecond paragraph here.")


def test_clean_keeps_list_items_and_drops_invisibles():
    sel = "Steps:\n- open it\n- close it\n1. done\nnon\u00a0breaking soft\u00adhyphen"
    assert clean_selection(sel) == "Steps:\n- open it\n- close it\n1. done non breaking softhyphen"


def test_clean_blank_lines_with_spaces():
    assert clean_selection("a\n  \n \n\nb") == "a\n\nb"


def test_clean_caps_on_sentence_boundary():
    text = " ".join(["Toto je věta číslo %d." % i for i in range(200)])
    out = clean_selection(text)
    assert len(out) <= MAX_PARAGRAPH_CHARS
    assert out.endswith(".") and out in text and len(out) > MAX_PARAGRAPH_CHARS // 2


def test_clean_caps_on_word_boundary():
    text = " ".join(["slovo"] * 600)
    out = clean_selection(text)
    assert len(out) <= MAX_PARAGRAPH_CHARS and out.endswith("slovo")


def test_cap_below_the_maximum_cuts_at_a_sentence_end():
    # ([prefill] max_paragraph_chars under 2000: the prefill cuts the same way as the maximum.)
    text = "První věta je tady. Druhá věta je o něco delší než ta první."
    assert cap(text, 30) == "První věta je tady."
    assert cap(text, 200) == text


def test_clean_short_text_unchanged():
    assert clean_selection("give up") == "give up"


def _llm(monkeypatch, content, caps=("completion",)):
    llm = LLM("http://localhost:11434/", "gemma3:4b")
    sent = []

    def post(path, body, timeout):
        if path == "/api/show":
            return {"capabilities": list(caps)}
        sent.append(body)
        return {"message": {"content": content}}

    monkeypatch.setattr(llm, "_post", post)
    return llm, sent


def test_llm_translate_request(monkeypatch):
    llm, sent = _llm(monkeypatch, json.dumps({"translation": "  Hello.\n\nBye.  "}))
    assert llm.translate("Ahoj.\n\nČau.", "cs", "en") == "Hello.\n\nBye."
    body = sent[0]
    assert body["format"]["properties"]["translation"]["type"] == "string"
    assert body["options"]["temperature"] == 0.2
    assert body["options"]["num_predict"] == 3 * len("Ahoj.\n\nČau.") // 2 + 200
    assert body["messages"][-1] == {"role": "user", "content": "Ahoj.\n\nČau."}
    assert "Czech" in body["messages"][0]["content"] and "English" in body["messages"][0]["content"]
    assert "think" not in body and body["stream"] is False


def test_llm_translate_think_and_cap(monkeypatch):
    llm, sent = _llm(monkeypatch, json.dumps({"translation": "x"}), caps=("completion", "thinking"))
    llm.translate("a" * 10000, "en", "cs")
    assert sent[0]["think"] is False
    assert sent[0]["options"]["num_predict"] <= 4096


@pytest.mark.parametrize("content", ["", "{\"translation\": \"cut off", "[]", "{\"translation\": \"  \"}",
                                     "{\"other\": 1}"])
def test_llm_translate_bad_answer(monkeypatch, content):
    llm, _ = _llm(monkeypatch, content)
    with pytest.raises(LLMError):
        llm.translate("Ahoj.", "cs", "en")
