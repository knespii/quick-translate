"""Automatic diacritics in the input (qtrans/autodiac.py): the edit helpers with a fake restorer, then typing
key by key on the real dictionary (skipped when data/dict.sqlite or its undiac table is missing)."""
import pytest

from qtrans.autodiac import (AutoDiacritics, has_czech_letter, strip_diacritics, token_edits,
                             written_with_diacritics)
from qtrans.paths import DB


class FakeDiacritizer:
    """Restores a fixed word list; 'ze' before an s-word stays the preposition (like the real context rule)."""
    ok = True
    WORDS = {"mas": "máš", "uz": "už", "skoly": "školy", "dekuji": "děkuji"}

    def looks_czech(self, text):
        return "take" not in text

    def restore(self, text, check=True):
        words = text.split(" ")
        out = []
        for i, w in enumerate(words):
            core = w.strip(",.?")
            nxt = words[i + 1] if i + 1 < len(words) else ""
            new = self.WORDS.get(core, core)
            if core == "ze":
                new = "ze" if nxt.startswith("s") else "že"
            out.append(w.replace(core, new) if core else w)
        return " ".join(out)


def type_text(auto, typed):
    text = ""
    for ch in typed:
        text += ch
        for s, e, new in sorted(auto.typed(text, len(text)), reverse=True):
            text = text[:s] + new + text[e:]
    return text


def test_token_edits_only_changed_words_in_range():
    assert token_edits("jak se mas, uz", "jak se máš, už") == [(7, 10, "máš"), (12, 14, "už")]
    assert token_edits("jak se mas, uz", "jak se máš, už", 0, 11) == [(7, 10, "máš")]
    assert token_edits("a b", "a b c") == []  # not a restored copy: nothing


def test_helpers():
    assert strip_diacritics("Dál si POZOR, Šárko!") == "Dal si POZOR, Sarko!"
    assert has_czech_letter("Ř") and not has_czech_letter("hello")
    assert written_with_diacritics("Koupil jsem byt v Praze, je krásný.")
    assert not written_with_diacritics("koupil jsem si byt")
    assert not written_with_diacritics("")


def test_typed_word_is_restored_when_finished():
    auto = AutoDiacritics(FakeDiacritizer())
    assert auto.typed("jak se mas", 10) == []  # still typing the word
    assert auto.typed("jak se mas ", 11) == [(7, 10, "máš")]
    assert auto.typed("jak se mas,", 11) == [(7, 10, "máš")]
    assert auto.typed("jak se máš ", 11) == []  # typed with diacritics
    assert auto.typed("take mas ", 9) == []  # not Czech


def test_typing_key_by_key():
    auto = AutoDiacritics(FakeDiacritizer())
    assert type_text(auto, "jak se mas, uz? ") == "jak se máš, už? "
    assert type_text(auto, "mas") == "mas"  # the last word waits for a space


def test_ze_is_respelled_by_the_next_word():
    auto = AutoDiacritics(FakeDiacritizer())
    assert type_text(auto, "ze skoly ") == "ze školy "
    assert type_text(auto, "rekl ze mas ") == "rekl že máš "


def test_ze_typed_by_the_user_after_undo_is_kept():
    auto = AutoDiacritics(FakeDiacritizer())
    text = "ze "
    assert auto.typed(text, 3) == [(0, 2, "že")]
    # The user undid it ('ze' again) and typed on: the next word must not change it back.
    text = "ze mas "
    assert auto.typed(text, 7) == [(3, 6, "máš")]


def test_whole_text():
    auto = AutoDiacritics(FakeDiacritizer())
    assert auto.whole("dekuji, uz jdu") == [(0, 6, "děkuji"), (8, 10, "už")]
    assert auto.whole("dekuji, uz jdu", 7, 14) == [(8, 10, "už")]
    assert auto.whole("Děkuji, uz jdu domů") == []  # written with diacritics: its ASCII words are meant


@pytest.fixture(scope="module")
def auto():
    if not DB.exists():
        pytest.skip("run python -m dictbuild.build_db")
    from qtrans.diacritics import Diacritizer
    from qtrans.search import Searcher
    d = Diacritizer(Searcher(DB).con)
    if not d.ok:
        pytest.skip("dictionary without the undiac table: rebuild with python -m dictbuild.build_db")
    return AutoDiacritics(d)


@pytest.mark.parametrize("typed, expected", [
    ("jak se mas brouku, uz jsi doma? ", "jak se máš brouku, už jsi doma? "),
    ("ze skoly jsem sel domu. ", "ze školy jsem šel domu. "),  # domu/domů: too close to call, left as typed
    ("rikal, ze se vrati. ", "říkal, že se vrátí. "),
    ("te doby jsem nevedel. ", "té doby jsem nevěděl. "),
    ("miluju te moc. ", "miluju tě moc. "),
    ("take care of my pet ", "take care of my pet "),
    ("list ", "list "),
    ("soubor config_file.txt je tam ", "soubor config_file.txt je tam "),
])
def test_typing_on_the_real_dictionary(auto, typed, expected):
    assert type_text(auto, typed) == expected
