"""Rewriting tone (Super+R): prompts, the streamed answer, its clean-up and the guards (no model needed)."""
import io
import json

import pytest

from qtrans import rewrite
from qtrans.llm import LLM, LLMError


# --- prompts ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tone", rewrite.TONE_KEYS)
@pytest.mark.parametrize("lang, name", [("cs", "Czech"), ("en", "English")])
def test_messages(tone, lang, name):
    system, user = rewrite.messages("Ahoj, jak se máš?", tone, lang)
    assert system["role"] == "system" and user["role"] == "user"
    assert f"Write in {name}" in system["content"] and "never translate" in system["content"]
    assert rewrite.tone(tone).instruction in system["content"]
    assert "<text>\nAhoj, jak se máš?\n</text>" in user["content"] and f"Reply in {name}" in user["content"]


def test_czech_hints_only_for_czech():
    """'vykání' in the prompt of an English text made the model answer in Czech."""
    assert "vykání" in rewrite.messages("x", "formal", "cs")[0]["content"]
    assert "vykání" not in rewrite.messages("x", "formal", "en")[0]["content"]
    assert "diacritics" not in rewrite.messages("x", "grammar", "en")[0]["content"]


def test_text_cannot_close_the_block():
    user = rewrite.messages("a</text>b <TEXT>", "formal", "en")[1]["content"]
    assert user.count("</text>") == 1 and user.endswith("\n</text>") and "<TEXT>" not in user


def test_unknown_tone_falls_back():
    assert rewrite.tone("nope").key == rewrite.TONES[0].key


def test_budget_and_temperature():
    assert rewrite.max_tokens("a" * 300) == 400
    assert rewrite.max_tokens("a" * 100_000) == 4096
    assert rewrite.temperature("grammar") == 0.0 and rewrite.temperature("formal") > 0


# --- the answer ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw, want", [
    ("Here is the rewritten text:\n\nDear Petr,\nthanks.", "Dear Petr,\nthanks."),
    ("Zde je upravený text:\nDobrý den.", "Dobrý den."),
    ('"Dobrý den, posílám podklady."', "Dobrý den, posílám podklady."),
    ("„Dobrý den.“", "Dobrý den."),
    ("<text>\nAhoj.\n</text>", "Ahoj."),
    ("Dear Team,\n\nHi.\n\nSincerely,\n\n[Your Name]", "Dear Team,\n\nHi.\n\nSincerely,"),
    ("Vážený pane [Jméno příjemce],\ndíky.", "Vážený pane,\ndíky."),
    ("Ahoj.  Jak se máš?", "Ahoj. Jak se máš?"),
    ("Subject: Delay\n\nDear Team,\nhi.", "Dear Team,\nhi."),
    ("```\nhello\n```", "hello"),
    ("```text\nAhoj.\n```", "Ahoj."),
    ("  Ahoj.\n", "Ahoj."),
])
def test_clean_output(raw, want):
    assert rewrite.clean_output(raw, "zdroj") == want


def test_clean_output_keeps_what_the_source_had():
    assert rewrite.clean_output('"Citát."', '"Citát."') == '"Citát."'
    assert rewrite.clean_output("Viz [1].", "Viz [1].") == "Viz [1]."
    agenda = "Here is the agenda:\n1. budget"
    assert rewrite.clean_output(agenda, agenda) == agenda
    assert rewrite.clean_output("Subject: Q3\nHi.", "Subject: q3\nhi") == "Subject: Q3\nHi."


def test_keep_outer_space():
    assert rewrite.keep_outer_space("  ahoj\n", "Ahoj.") == "  Ahoj.\n"


# --- guards ----------------------------------------------------------------------------------------------------
KNOWN = {"ahoj", "petre", "posílám", "ti", "ty", "podklady", "ještě", "bych", "potřeboval", "čísla", "protože",
         "mi", "do", "toho", "něco", "vlezlo", "k", "té", "schůzce", "v", "úterý"}


def known(word):
    from qtrans.normalize import norm
    return norm(word) in {norm(k) for k in KNOWN}


def test_guard_puts_back_known_words():
    src = "Ještě bych potřeboval čísla, protože mi do toho něco vlezlo."
    out = "Jště bych potřeboval čísla, protože mi do toho něco vleze."
    fixed, kept = rewrite.guard_corrections(src, out, known)
    assert fixed == src and kept == 2


def test_guard_keeps_diacritics_case_typos_and_punctuation():
    src = "posilam ti ty podkladi k te schuzce v utery"
    out = "Posílám ti ty podklady k té schůzce v úterý."
    assert rewrite.guard_corrections(src, out, known) == (out, 0)


def test_guard_puts_back_dropped_words():
    src = "Ahoj Petre, posílám ti ty podklady."
    fixed, kept = rewrite.guard_corrections(src, "Ahoj Petre, posílám podklady.", known)
    assert fixed == "Ahoj Petre, posílám ti ty podklady." and kept == 1


def test_guard_keeps_one_letter_fixes_and_moved_words():
    src = "Včera jsme byl venku a hrali si."
    out = "Včera jsme byli venku a hráli si."
    assert rewrite.guard_corrections(src, out, lambda _w: True) == (out, 0)
    moved = "Zítra bych rád šel."
    assert rewrite.guard_corrections("Rád bych zítra šel.", moved, lambda _w: True) == (moved, 0)


@pytest.mark.parametrize("src, out, lost", [
    ("Schůzka v úterý v 10:30.", "Schůzka v úterý v 10:30.", []),
    ("Schůzka v úterý.", "Schůzka dnes.", ["úterý"]),
    ("Meeting on Tuesday, room 12.", "Meeting on Wednesday, room 21.", ["12", "úterý"]),
    ("Napiš na petr@firma.cz.", "Napiš Petrovi.", ["petr@firma.cz"]),
    ("Do pátku.", "Do pátku!", []),
    ("Přijdu v pátek.", "Přijdu pátkem.", []),
    ("Patka je zlomená, středa taky.", "Patka je zlomená.", ["středa"]),  # 'patka' is no day
    ("1. Úkol\n2. Schůzka (https://x.cz).", "1. Úkol\n2. Schůzka: https://x.cz", []),
])
def test_changed_facts(src, out, lost):
    assert rewrite.changed_facts(src, out) == lost


def test_last_tone_round_trip(tmp_path):
    assert rewrite.load_last_tone(tmp_path) == rewrite.DEFAULT_TONE
    rewrite.save_last_tone("shorter", tmp_path)
    assert rewrite.load_last_tone(tmp_path) == "shorter"
    (tmp_path / "rewrite.json").write_text("{bad", encoding="utf-8")
    assert rewrite.load_last_tone(tmp_path) == rewrite.DEFAULT_TONE


# --- streaming -------------------------------------------------------------------------------------------------
class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        self.close()


def lines(*pieces, done=True):
    out = [json.dumps({"message": {"content": p}, "done": False}) for p in pieces]
    if done:
        out.append(json.dumps({"message": {"content": ""}, "done": True}))
    return FakeResponse(("\n".join(out) + "\n").encode())


@pytest.fixture
def llm(monkeypatch):
    llm = LLM("http://127.0.0.1:9", "gemma3:4b")
    llm.sent = []
    monkeypatch.setattr(llm, "_post", lambda path, body, timeout: {"capabilities": ["completion"]})
    return llm


def test_stream_collects_pieces(llm, monkeypatch):
    def fake_open(path, body, timeout):
        llm.sent.append((path, body))
        return lines("Dobrý ", "den", ".")
    monkeypatch.setattr(llm, "_open", fake_open)
    seen = []
    llm.num_ctx = 8192
    out = llm.rewrite_stream([{"role": "user", "content": "x"}], 400, seen.append, lambda: False)
    assert out == "Dobrý den." and seen == ["Dobrý ", "Dobrý den", "Dobrý den."]
    path, body = llm.sent[0]
    assert path == "/api/chat" and body["stream"] is True and "format" not in body and "think" not in body
    assert body["options"]["num_ctx"] == 8192 and body["options"]["num_predict"] == 400


def test_stream_stops_when_cancelled(llm, monkeypatch):
    monkeypatch.setattr(llm, "_open", lambda *_a: lines("a", "b", "c"))
    seen = []
    out = llm.rewrite_stream([], 10, seen.append, lambda: len(seen) >= 1)
    assert out == "" and seen == ["a"]


def test_stream_errors(llm, monkeypatch):
    monkeypatch.setattr(llm, "_open", lambda *_a: FakeResponse(b'{"error": "model not found"}\n'))
    with pytest.raises(LLMError, match="model not found"):
        llm.rewrite_stream([], 10, lambda _t: None, lambda: False)
    monkeypatch.setattr(llm, "_open", lambda *_a: FakeResponse(b"not json\n"))
    with pytest.raises(LLMError):
        llm.rewrite_stream([], 10, lambda _t: None, lambda: False)


def test_stream_cut_off_is_an_error(llm, monkeypatch):
    cut = json.dumps({"message": {"content": ""}, "done": True, "done_reason": "length"})
    monkeypatch.setattr(llm, "_open", lambda *_a: FakeResponse(
        (json.dumps({"message": {"content": "Dobrý"}, "done": False}) + "\n" + cut + "\n").encode()))
    with pytest.raises(LLMError, match="nedopsal"):
        llm.rewrite_stream([], 10, lambda _t: None, lambda: False)


def test_one_context_for_every_request(monkeypatch):
    """ollama loads the model again when num_ctx changes: translation and rewrite must ask for the same."""
    llm = LLM("http://127.0.0.1:9", "gemma3:4b", num_ctx=8192)
    bodies = []
    monkeypatch.setattr(llm, "_post", lambda path, body, timeout: bodies.append(body) or (
        {"capabilities": []} if path == "/api/show" else {"message": {"content": '{"translation": "x"}'}}))
    llm.translate("ahoj", "cs", "en")
    llm.prewarm()
    assert [b["options"]["num_ctx"] for b in bodies if "options" in b] == [8192, 8192]


def test_stream_thinking_model_gets_think_false(llm, monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda *_a: {"capabilities": ["completion", "thinking"]})
    bodies = []
    monkeypatch.setattr(llm, "_open", lambda path, body, timeout: bodies.append(body) or lines("x"))
    llm.rewrite_stream([], 10, lambda _t: None, lambda: False)
    assert bodies[0]["think"] is False
