import pytest

from qtrans.search import Hit
from qtrans.wordpick import alternatives, replace_span, word_at

TEXT = "Stýská se mi po tobě, brouku — a po našich výletech (s Jean-Lucem). I’m fine, don't worry: 2024-ish!"


def span(text, i):
    r = word_at(text, i)
    return None if r is None else text[r[0]:r[1]]


@pytest.mark.parametrize("word", ["Stýská", "tobě", "brouku", "našich", "výletech", "Jean-Lucem", "I’m", "don't",
                                  "2024-ish", "fine"])
def test_word_at_every_letter(word):
    start = TEXT.index(word)
    for i in range(start, start + len(word)):
        assert span(TEXT, i) == word


@pytest.mark.parametrize("ch", [",", " ", "—", "(", ")", ":", "!", "."])
def test_word_at_between_words(ch):
    assert span(TEXT, TEXT.index(ch)) is None


def test_word_at_edges():
    assert span("ahoj", 0) == "ahoj"
    assert span("ahoj", 3) == "ahoj"
    assert span("ahoj", 4) is None
    assert span("ahoj", -1) is None
    assert span("", 0) is None


def test_word_at_trims_dashes_and_quotes():
    t = "a - b 'quoted' -dash- 12 2024"
    assert span(t, t.index("-")) is None
    assert span(t, t.index("quoted")) == "quoted"
    assert span(t, t.index("'")) == "quoted"
    assert span(t, t.index("dash")) == "dash"
    assert span(t, t.index("12")) is None  # numbers only: nothing to look up
    assert span(t, len(t) - 1) is None


def test_word_at_decomposed_diacritics():
    t = "léto"  # "léto" with a combining accent
    assert span(t, 2) == t


def test_replace_span():
    t = "How are you, bug? Bug!"
    i = t.index("bug")
    assert replace_span(t, i, i + 3, "honey") == "How are you, honey? Bug!"
    j = t.index("Bug")
    assert replace_span(t, j, j + 3, "honey") == "How are you, bug? Honey!"
    assert replace_span(t, j, j + 3, "iPhone") == "How are you, bug? IPhone!"
    assert replace_span(t, 0, 3, "what") == "What are you, bug? Bug!"
    assert replace_span(t, i, i + 3, "Joe") == "How are you, Joe? Bug!"  # a capital stays
    assert replace_span(t, i, i + 3, "sweet pea") == "How are you, sweet pea? Bug!"
    assert replace_span("čau", 0, 3, "ahoj") == "ahoj"
    assert replace_span("Čau", 0, 3, "ahoj") == "Ahoj"
    assert replace_span("Čau", 0, 3, "") == ""


class FakeSearcher:
    """search(raw) -> hits by casefolded text; translations from a table."""

    def __init__(self, entries):
        self.hits = {}
        self.trans = {}
        for i, (lang, form, text, trans, *tier) in enumerate(entries):
            h = Hit(i, lang, text, text.lower(), len(text.split()), 4.0, 0, "n", tier=tier[0] if tier else 1000)
            self.hits.setdefault(form.casefold(), []).append(h)
            self.trans[i] = [(t, None) for t in trans]

    def search(self, raw, limit=14):
        return self.hits.get(raw.casefold(), [])[:limit]

    def translations(self, h, n=6):
        return self.trans[h.term_id][:n]


SEARCHER = FakeSearcher([
    ("cs", "brouku", "brouk", ["beetle", "love", "bug", "honey", "sweetheart", "creepy-crawly", "darling"]),
    ("cs", "lásko", "láska", ["love", "affection"]),
    ("cs", "lasko", "láska", ["love", "affection"]),
    ("cs", "výletech", "výlet", ["trip", "outing", "excursion"]),
    ("en", "bug", "bug", ["chyba", "brouk"]),
    ("en", "trips", "trip", ["výlet"]),
    ("en", "error", "error", ["chyba", "omyl"]),
    ("cs", "chyba", "chyba", ["mistake", "error", "fault", "bug"]),
    ("cs", "omyl", "omyl", ["mistake", "error", "misunderstanding"]),
    ("en", "im", "image", ["obraz"], 638),  # a prefix match of "I'm", not the word itself
    ("cs", "obraz", "obraz", ["picture", "image"]),
])
SOURCE = "jak se mas brouku. mam se dobre lasko. stýská se mi po našich výletech."


def test_alternatives_from_the_source_word():
    groups = alternatives(SEARCHER, "bug", 0.2, SOURCE, "cs", "en")
    assert [(k, h.text, tok) for k, h, tok, _a in groups] == [("src", "brouk", "brouku")]
    assert groups[0][3] == ["beetle", "love", "honey", "sweetheart", "creepy-crawly"]  # max 5, without "bug"


def test_alternatives_inflected_word_and_order_by_position():
    groups = alternatives(SEARCHER, "trips", 0.9, SOURCE, "cs", "en")
    assert groups[0][1].text == "výlet" and groups[0][3] == ["outing", "excursion"]
    # "love" comes from both "brouku" and "lasko": the one nearer the clicked position first
    near_end = alternatives(SEARCHER, "Love", 0.55, SOURCE, "cs", "en")
    assert [h.text for _k, h, _t, _a in near_end] == ["láska", "brouk"]
    assert near_end[0][3] == ["affection"]
    near_start = alternatives(SEARCHER, "love", 0.1, SOURCE, "cs", "en")
    assert [h.text for _k, h, _t, _a in near_start] == ["brouk", "láska"]


def test_alternatives_synonyms_when_no_source_word_fits():
    groups = alternatives(SEARCHER, "error", 0.5, SOURCE, "cs", "en")
    assert [(k, h.text) for k, h, _t, _a in groups] == [("syn", "error")]
    assert groups[0][3] == ["mistake", "fault", "bug", "misunderstanding"]


def test_alternatives_nothing():
    assert alternatives(SEARCHER, "zebra", 0.5, SOURCE, "cs", "en") == []
    assert alternatives(SEARCHER, "im", 0.5, SOURCE, "cs", "en") == []  # no synonyms of a prefix match


def test_alternatives_cache():
    cache = {}
    alternatives(SEARCHER, "bug", 0.2, SOURCE, "cs", "en", cache)
    assert "brouku" in cache and "výletech" in cache


def test_grammatical_words_get_no_alternatives():
    from qtrans.wordpick import alternatives, is_grammatical
    for w in ("the", "The", "I", "it", "a", "of", "se", "na", "is"):
        assert is_grammatical(w), w
        assert alternatives(None, w, 0.5, "x", "cs", "en") == []
    for w in ("bug", "beetle", "brouk", "výlet", "trip"):
        assert not is_grammatical(w), w


def test_find_token_whole_word_nearest():
    from qtrans.wordpick import find_token
    t = "jak se máš brouku. brouk je brouku podobný, Brouku!"
    assert find_token(t, "brouku", 0.0) == (11, 17)
    assert find_token(t, "brouku", 1.0) == (44, 50)
    assert find_token(t, "brouk", 0.5) == (19, 24)  # not inside "brouku"
    assert find_token(t, "pes", 0.5) is None
