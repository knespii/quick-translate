"""Describe a word ('?…'): the rude-word filter and the LLM's answer (no model needed)."""
import json

import pytest

from qtrans.describe import asks_rude, rude
from qtrans.llm import LLM, LLMError


@pytest.mark.parametrize("head, translations", [
    ("jebat", ["fuck", "roger"]), ("mrdat", ["fuck"]), ("fuck", []), ("čurák", []),
    ("píča", []), ("souložit", []), ("shit", []),
])
def test_rude(head, translations):
    assert rude(head, translations)


@pytest.mark.parametrize("head, translations", [
    ("screw", ["šroub", "šroubovat"]), ("hammer", ["kladivo"]), ("cocktail", []), ("cockpit", []), ("pic", []),
    ("picnic", []), ("sukně", []), ("Dickens", []), ("houkat", ["hoot"]), ("kohout", ["cock"]),
    ("cock", ["kohout"]), ("bitch", ["fena"]), ("pussy", ["kočička"]),
])
def test_not_rude(head, translations):
    assert not rude(head, translations[:1])


def test_asks_rude():
    assert not asks_rude("trojúhelník co je na servu a hýbá tahlem")
    assert asks_rude("sprosté slovo pro souložit") and asks_rude("vulgární výraz pro hlupáka")


def make_llm(monkeypatch, answers):
    llm = LLM("http://127.0.0.1:9", "gemma3:4b")
    sent = []

    def post(path, body, timeout):
        if path == "/api/show":
            return {"capabilities": ["completion"]}
        sent.append(body)
        return {"message": {"content": answers.pop(0)}}
    monkeypatch.setattr(llm, "_post", post)
    return llm, sent


def test_suggest_reads_the_thing_and_tidies(monkeypatch):
    answer = json.dumps({"thing": "A hammer", "candidates": [
        {"en": "hammer", "cs": "klepací_nástroj", "why": "nástroj"}, {"en": " \"mallet\" ", "cs": "palice", "why": ""}]})
    llm, sent = make_llm(monkeypatch, [answer])
    out = llm.suggest("nástroj na zatloukání hřebíků")
    assert [c["en"] for c in out] == ["hammer", "mallet"] and out[0]["cs"] == "klepací nástroj"
    assert llm.last_thing == "A hammer"
    assert sent[0]["format"]["required"] == ["thing", "candidates"] and "Dictionary" not in sent[0]["messages"][1]["content"]


def test_suggest_retries_broken_json(monkeypatch):
    good = json.dumps({"thing": "x", "candidates": [{"en": "scissors", "cs": "nůžky", "why": "stříhání"}]})
    llm, sent = make_llm(monkeypatch, ['{"thing": "x", "candidates": [{"en": "sci', good])
    assert llm.suggest("a tool to cut paper")[0]["en"] == "scissors"
    assert [b["options"]["temperature"] for b in sent] == [0, 0.4]


def test_suggest_gives_up(monkeypatch):
    llm, _sent = make_llm(monkeypatch, ["nonsense", "still nonsense"])
    with pytest.raises(LLMError):
        llm.suggest("x")
