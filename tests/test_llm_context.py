import json

import pytest

from qtrans.llm import LLM, LLMError

SRC = "jak se máš brouku. stýská se mi po našich výletech."
TR = "How are you, little bug? I miss our trips."


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


def _opt(word, sentence, why="důvod"):
    return {"word": word, "sentence": sentence, "why": why}


def test_alternatives_request(monkeypatch):
    llm, sent = _llm(monkeypatch, json.dumps({"options": [_opt("dear", "How are you, dear? I miss our trips.")]}))
    assert llm.alternatives_in_context(SRC, TR, "bug", "cs", "en") == [
        _opt("dear", "How are you, dear? I miss our trips.")]
    body = sent[0]
    item = body["format"]["properties"]["options"]["items"]
    assert set(item["required"]) == {"word", "sentence", "why"}
    assert body["options"]["temperature"] == 0.3
    assert body["options"]["num_predict"] == 4 * (len(TR) + 60) + 100
    system, user = body["messages"][0]["content"], body["messages"][1]["content"]
    assert "Czech-English" in system and "up to 4" in system
    assert SRC in user and TR in user and "Word to replace: bug" in user
    assert "think" not in body and body["stream"] is False


def test_alternatives_think_and_budget(monkeypatch):
    llm, sent = _llm(monkeypatch, json.dumps({"options": []}), caps=("completion", "thinking"))
    assert llm.alternatives_in_context("a", "b" * 5000, "b", "en", "cs", n=2) == []
    assert sent[0]["think"] is False
    assert sent[0]["options"]["num_predict"] == 2048
    assert "up to 2" in sent[0]["messages"][0]["content"]


def test_alternatives_filter_original_and_duplicates(monkeypatch):
    opts = [
        _opt("bug", "How are you, little bug? I miss our trips."),       # the original
        _opt("Bug!", "How are you, Bug? I miss our trips."),             # the original, other case
        _opt("a bug", "How are you, a bug? I miss our trips."),          # only an article added
        _opt("dear", TR),                                                # sentence unchanged
        _opt("sweetheart", "How are you, sweetheart? I miss our trips.", " blízkost "),
        _opt("Sweetheart", "How are you, Sweetheart? I miss our trips."),  # same word again
        _opt("honey", "How are you, sweetheart? I miss our trips."),     # same sentence again
        _opt("", "x"), {"word": "x"}, "junk", {"word": 1, "sentence": "y"},
        {"word": "love", "sentence": "How are you, love? I miss our trips."},  # no reason
        _opt("darling", "How are you, darling? I miss our trips."),
    ]
    llm, _ = _llm(monkeypatch, json.dumps({"options": opts}))
    out = llm.alternatives_in_context(SRC, TR, "bug", "cs", "en", n=4)
    assert [o["word"] for o in out] == ["sweetheart", "love", "darling"]
    assert out[0]["why"] == "blízkost" and out[1]["why"] == ""


def test_alternatives_capped_to_n(monkeypatch):
    opts = [_opt(w, f"How are you, {w}? I miss our trips.") for w in ["dear", "love", "honey", "darling"]]
    llm, _ = _llm(monkeypatch, json.dumps({"options": opts}))
    assert [o["word"] for o in llm.alternatives_in_context(SRC, TR, "bug", "cs", "en", n=2)] == ["dear", "love"]


def test_alternatives_word_taken_from_sentence(monkeypatch):
    # gemma sometimes names the word in the source language; the changed words of the sentence are used.
    llm, _ = _llm(monkeypatch, json.dumps({"options": [
        _opt("malému", "How are you, little one? I miss our trips.")]}))
    out = llm.alternatives_in_context(SRC, TR, "bug", "cs", "en")
    assert out[0]["word"] == "one" and out[0]["sentence"] == "How are you, little one? I miss our trips."


def test_alternatives_salvage_cut_off(monkeypatch):
    cut = '{"options": [{"word": "dear", "sentence": "How are you, dear? I miss our trips.", "why": "x"}, {"word": "lo'
    llm, _ = _llm(monkeypatch, cut)
    assert [o["word"] for o in llm.alternatives_in_context(SRC, TR, "bug", "cs", "en")] == ["dear"]


@pytest.mark.parametrize("content", ["", "{\"options\": [{\"word\": \"cut", "[]", "{\"options\": 5}"])
def test_alternatives_bad_answer(monkeypatch, content):
    llm, _ = _llm(monkeypatch, content)
    with pytest.raises(LLMError):
        llm.alternatives_in_context(SRC, TR, "bug", "cs", "en")


def test_alternatives_post_error(monkeypatch):
    llm = LLM("http://localhost:11434", "gemma3:4b")

    def post(path, body, timeout):
        raise LLMError("ollama neběží (spusť ollama serve)")

    monkeypatch.setattr(llm, "_post", post)
    with pytest.raises(LLMError):
        llm.alternatives_in_context(SRC, TR, "bug", "cs", "en")
    with pytest.raises(LLMError):
        llm.fix_sentence(SRC, "I miss our trip.", "trip", "cs", "en")


def test_fix_sentence_request(monkeypatch):
    llm, sent = _llm(monkeypatch, json.dumps({"sentence": "  Stýská se mi po našich výletech. "}),
                     caps=("completion", "thinking"))
    s = "Stýská se mi po našich výlet."
    assert llm.fix_sentence("I miss our trips.", s, "výlet", "en", "cs") == "Stýská se mi po našich výletech."
    body = sent[0]
    assert body["format"]["required"] == ["sentence"]
    assert body["think"] is False and body["stream"] is False
    assert body["options"]["num_predict"] == 2 * len(s) + 100
    system, user = body["messages"][0]["content"], body["messages"][1]["content"]
    assert "Czech proofreader" in system and "English text" in system
    assert "I miss our trips." in user and s in user and "výlet" in user


@pytest.mark.parametrize("content", ["", "{\"sentence\": \"cut", "[]", "{\"sentence\": \"  \"}", "{\"x\": 1}"])
def test_fix_sentence_bad_answer(monkeypatch, content):
    llm, _ = _llm(monkeypatch, content)
    with pytest.raises(LLMError):
        llm.fix_sentence(SRC, "I miss our trip.", "trip", "cs", "en")
