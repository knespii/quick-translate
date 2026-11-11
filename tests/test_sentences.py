import pytest

from qtrans.sentences import sentence_around


def _around(text, word):
    start = text.index(word)
    s, e = sentence_around(text, start, start + len(word))
    return text[s:e]


EX = "jak se máš brouku. stýská se mi po našich výletech."


def test_lowercase_sentences():
    assert _around(EX, "brouku") == "jak se máš brouku."
    assert _around(EX, "výletech") == "stýská se mi po našich výletech."


def test_question_and_exclamation():
    t = "How are you, little bug? I miss our trips! Come back…  Soon."
    assert _around(t, "bug") == "How are you, little bug?"
    assert _around(t, "trips") == "I miss our trips!"
    assert _around(t, "back") == "Come back…"
    assert _around(t, "Soon") == "Soon."


def test_newline_ends_sentence():
    t = "první řádek bez tečky\ndruhý řádek\n\ntřetí"
    assert _around(t, "první") == "první řádek bez tečky"
    assert _around(t, "druhý") == "druhý řádek"
    assert _around(t, "třetí") == "třetí"


@pytest.mark.parametrize("text, word, sentence", [
    ("Přijdu např. zítra. Jindy ne.", "zítra", "Přijdu např. zítra."),
    ("Sešli jsme se 5. května u řeky. Pak ne.", "řeky", "Sešli jsme se 5. května u řeky."),
    ("Mr. Smith met J. Novák, i.e. the boss. Then left.", "boss", "Mr. Smith met J. Novák, i.e. the boss."),
    ("Volal prof. Novák a doc. Malá. Pak ne.", "Malá", "Volal prof. Novák a doc. Malá."),
])
def test_abbreviations_do_not_split(text, word, sentence):
    assert _around(text, word) == sentence


def test_year_still_ends_sentence():
    assert _around("It happened in 2020. Then it stopped.", "stopped") == "Then it stopped."


def test_quotes_after_end():
    t = 'Řekl: „Pojď sem.“ A odešel.'
    assert _around(t, "Pojď") == "Řekl: „Pojď sem.“"
    assert _around(t, "odešel") == "A odešel."


def test_span_over_two_sentences_and_edges():
    t = "  One two. Three four.  "
    s = t.index("two")
    assert t[slice(*sentence_around(t, s, t.index("Three") + 5))] == "One two. Three four."
    assert sentence_around("", 0, 0) == (0, 0)
    assert t[slice(*sentence_around(t, len(t), len(t)))] == ""
