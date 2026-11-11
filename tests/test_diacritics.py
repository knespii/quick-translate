"""Diacritics restoration on the real dictionary (skipped when data/dict.sqlite is missing)."""
import re
import time

import pytest

from qtrans.normalize import norm
from qtrans.paths import DB

# Everyday Czech written for this test: chat, a short story, news and instructions.
SAMPLE = """\
Ahoj, jak se máš? Už jsem doma a stýská se mi po tobě. Zítra ráno musím jít do práce, ale večer bych tě rád viděl.
Můžeš mi napsat, kdy budeš mít čas? Zapomněl jsem ti říct, že jsme včera byli s Petrem v kině a ten film byl
opravdu skvělý. Nevím, jestli se ti bude líbit, ale určitě bys ho měla vidět. Dej mi vědět, jestli chceš jít
se mnou, koupím lístky dopředu. Děkuji za dárek, moc mě potěšil.
Když jsme přijeli k chalupě, pršelo a celá zahrada byla pod vodou. Děti hned běžely do kuchyně, kde babička
pekla koláče. Dědeček seděl u kamen a četl noviny. Řekl nám, že letos byla velmi dlouhá zima a že sníh ležel
až do dubna. Večer jsme hráli karty a povídali si o tom, co všechno se za poslední rok změnilo. Nikdo z nás
nechtěl jít spát, protože venku byla krásná hvězdná noc.
Vláda ve středu schválila nový zákon o daních, který začne platit od příštího roku. Podle ministra financí
by měl přinést do státního rozpočtu několik miliard korun. Opozice tvrdí, že zákon poškodí malé podnikatele
a zvýší ceny potravin. Většina ekonomů však očekává, že dopad na domácnosti bude jen mírný.
Před prvním použitím přístroj nabijte. Stiskněte tlačítko na boční straně a držte ho tři sekundy, dokud se
nerozsvítí zelená kontrolka. Pokud se přístroj nezapne, zkontrolujte, zda je baterie správně vložená.
Nepoužívejte ho ve vlhkém prostředí a chraňte ho před přímým slunečním světlem.
"""
ENGLISH = [
    "I want to take the list to most people.",
    "Can you send me the file before the meeting tomorrow?",
    "The cast of the movie was great and the sale starts on Monday.",
    "take", "list", "most", "pet", "dog",
]
WORD = re.compile(r"[^\W\d_]+")


def strip(text: str) -> str:
    """Remove Czech diacritics, keep case and everything else."""
    return "".join((norm(c).upper() if c.isupper() else norm(c)) if c.isalpha() and not c.isascii() else c
                   for c in text)


@pytest.fixture(scope="module")
def dia():
    if not DB.exists():
        pytest.skip("run python -m dictbuild.build_db")
    from qtrans.diacritics import Diacritizer
    from qtrans.search import Searcher
    d = Diacritizer(Searcher(DB).con)
    if not d.ok:
        pytest.skip("dictionary without the undiac table: rebuild with python -m dictbuild.build_db")
    return d


@pytest.mark.parametrize("typed, want", [
    ("jak se mas", "jak se máš"),
    ("jak se mas brouku, styska se mi", "jak se máš brouku, stýská se mi"),
    ("styska se mi po tobe", "stýská se mi po tobě"),
    ("zapomnel jsem", "zapomněl jsem"),
    ("Dobry den, pane Novaku", "Dobrý den, pane Nováku"),
    ("DEKUJI MOC", "DĚKUJI MOC"),
    ("rekl, ze prijde ze skoly", "řekl, že přijde ze školy"),
    ("miluju te a v te dobe jsem to nevedel", "miluju tě a v té době jsem to nevěděl"),
    ("Novaku, pojd sem!", "Nováku, pojď sem!"),
    ("hledam kachnovitych ptaku", "hledám kachnovitých ptáků"),  # unknown word: regular ending
])
def test_restore(dia, typed, want):
    assert dia.restore(typed) == want


def test_punctuation_and_whitespace_kept(dia):
    typed = "  Ahoj!!\n\tJak se mas?  (fakt)  …styska -- se mi;  "
    got = dia.restore(typed)
    assert strip(got) == typed
    assert "máš?" in got and "…stýská --" in got


def test_existing_diacritics_untouched(dia):
    assert dia.restore("zapomněl jsem klice") == "zapomněl jsem klíče"
    assert dia.restore_word("máš") == "máš"
    assert dia.restore_word("Zapomnel") == "Zapomněl"
    assert dia.restore_word("xyzzy") == "xyzzy"


def test_code_left_alone(dia):
    assert dia.restore("jak se mas, viz https://seznam.cz/zpravy a soubor prace_2.txt") == \
        "jak se máš, viz https://seznam.cz/zpravy a soubor prace_2.txt"


@pytest.mark.parametrize("text", ENGLISH)
def test_english_unchanged(dia, text):
    assert not dia.looks_czech(text)
    assert dia.restore(text) == text


def test_english_words_alone(dia):
    assert dia.restore_word("take") == "take"
    assert dia.restore_word("Most") == "Most"


@pytest.mark.parametrize("text", ["jak se mas", "styska se mi", "dekuji", "zapomnel jsem klice", "Ahoj, jak se máš?"])
def test_looks_czech(dia, text):
    assert dia.looks_czech(text)


def word_accuracy(dia, text: str) -> float:
    got = WORD.findall(dia.restore(strip(text)))
    want = WORD.findall(text)
    assert len(got) == len(want)
    return sum(g == w for g, w in zip(got, want)) / len(want)


def test_sample_accuracy(dia):
    acc = word_accuracy(dia, SAMPLE)
    print(f"diacritics word accuracy: {acc:.1%}")
    assert acc >= 0.90


def test_speed(dia):
    para = strip(" ".join(SAMPLE.split())[:500])
    dia.restore(para)  # warm the page cache
    fresh = type(dia)(dia.con)  # no memoized lookups: the cost of a new paragraph
    t0 = time.perf_counter()
    fresh.restore(para)
    ms = (time.perf_counter() - t0) * 1000
    print(f"restore of {len(para)} chars: {ms:.2f} ms")
    assert ms < 10


@pytest.mark.parametrize("typed, restored", [
    ("dal si pozor", "dal si pozor"),            # dal/dál: both common, left as typed
    ("koupil jsem si byt", "koupil jsem si byt"),  # byt (flat) is a common word itself
    ("a ze zahrady", "a ze zahrady"),            # the preposition before z-
    ("rekl ze prijde", "řekl že přijde"),
    ("zapomnel jsem klice", "zapomněl jsem klíče"),
])
def test_careful_homographs(dia, typed, restored):
    assert dia.restore(typed) == restored
