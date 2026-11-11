"""Diacritics restoration table: folded word -> its most frequent Czech spelling ('stýská' for 'styska').

Candidates are the SYN2015 word forms plus the Czech dictionary terms and forms; the plain ASCII spelling
competes too, so 'list' or 'most' keep no row. margin = best zipf - second best zipf (0 = a coin toss);
a key with a single dictionary-only spelling gets zipf DICT_ONLY_ZIPF. Unknown words fall back to suffix rules.
"""
from collections import Counter, defaultdict

from qtrans.normalize import norm

_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzáčďéěíňóřšťúůýž")
DICT_ONLY_ZIPF = 1.0  # below the corpus minimum (10 occurrences ~ zipf 1.9)
SUFFIX_LENGTHS = (3, 4, 5)
SUFFIX_MIN_WORDS, SUFFIX_MIN_SHARE = 30, 0.9


def _spelling(word: str) -> tuple[str, str] | None:
    """(key, word) for a single Czech word whose folding keeps one letter per letter, else None."""
    w = word.lower()
    if len(w) < 2 or not set(w) <= _LETTERS:
        return None
    key = norm(w)
    return (key, w) if len(key) == len(w) else None


def best_spellings(cs_word: dict[str, float], dict_words) -> dict[str, list[tuple[str, float]]]:
    """key -> [(spelling, zipf)] most frequent first."""
    cands = defaultdict(dict)  # key -> {spelling: zipf}
    for w, z in cs_word.items():
        s = _spelling(w)
        if s:
            cands[s[0]][s[1]] = z
    for w in dict_words:
        s = _spelling(w)
        if s:
            cands[s[0]].setdefault(s[1], DICT_ONLY_ZIPF)
    return {key: sorted(sp.items(), key=lambda kv: (-kv[1], kv[0])) for key, sp in cands.items()}


def rows(ranked: dict[str, list]) -> list[tuple[str, str, float, float, str | None, float]]:
    """(key, word, zipf, margin, alt, plain) for every key whose best spelling differs from the key, sorted by
    key; alt is the second best spelling (maybe the key itself), for context rules ('brouků' vs vocative
    'brouku'); plain is the zipf of the key spelled as it is, without diacritics (0: not a word), so the app
    can leave common words like 'byt' or 'dal' as typed."""
    out = []
    for key, spellings in ranked.items():
        word, z = spellings[0]
        if word == key:
            continue
        alt, second = spellings[1] if len(spellings) > 1 else (None, 0.0)
        plain = dict(spellings).get(key, 0.0)
        out.append((key, word, round(z, 3), round(z - second, 3), alt, round(plain, 3)))
    out.sort()
    return out


def suffix_rows(ranked: dict[str, list]) -> list[tuple[str, str]]:
    """(suffix, ending) for word endings that nearly always carry the same diacritics ('ych' -> 'ých'),
    for words the corpus does not know ('kachnovitych'). Counted over word types, not occurrences."""
    out = []
    for n in SUFFIX_LENGTHS:
        endings = defaultdict(Counter)
        for key, spellings in ranked.items():
            if len(key) >= n + 3:
                endings[key[-n:]][spellings[0][0][-n:]] += 1
        for suffix, counts in endings.items():
            ending, hits = counts.most_common(1)[0]
            if ending != suffix and hits >= SUFFIX_MIN_SHARE * counts.total() and counts.total() >= SUFFIX_MIN_WORDS:
                out.append((suffix, ending))
    out.sort()
    return out
