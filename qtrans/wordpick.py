"""Paragraph mode word picker: the word under a click, dictionary alternatives for it, and its replacement.

Pure Python (no GTK) so it can be tested; the popup turns a click into a character index and back.
"""
import re
import unicodedata

from qtrans.normalize import FUNCTION_WORDS, STOPWORDS, norm
from qtrans.search import WHOLE

_EDGE = "-'’"  # word characters that do not start or end a word ("- dash", "'quoted'")
_TOKEN = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")
MAX_GROUPS = 3
MAX_ALTS = 5


def _is_word_char(c: str) -> bool:
    return c.isalnum() or c in _EDGE or unicodedata.category(c).startswith("M")


def word_at(text: str, i: int) -> tuple[int, int] | None:
    """(start, end) of the word containing character i: a maximal run of letters, digits, '-' and apostrophes,
    without leading or trailing '-' / apostrophes. None between words or for a run without a letter."""
    if not 0 <= i < len(text) or not _is_word_char(text[i]):
        return None
    start, end = i, i + 1
    while start > 0 and _is_word_char(text[start - 1]):
        start -= 1
    while end < len(text) and _is_word_char(text[end]):
        end += 1
    while start < end and text[start] in _EDGE:
        start += 1
    while end > start and text[end - 1] in _EDGE:
        end -= 1
    if not any(c.isalpha() for c in text[start:end]):
        return None
    return start, end


def find_token(text: str, token: str, rel: float) -> tuple[int, int] | None:
    """(start, end) of the whole-word occurrence of token in text (any case) nearest to rel (0 = start, 1 = end)."""
    found = [m.span() for m in re.finditer(r"(?<![\w'’-])" + re.escape(token) + r"(?![\w'’-])", text, re.IGNORECASE)]
    if not found:
        return None
    return min(found, key=lambda s: abs(s[0] / max(1, len(text)) - rel))


def replace_span(text: str, start: int, end: int, new: str) -> str:
    """text with text[start:end] replaced by new; a capitalized word stays capitalized."""
    old = text[start:end]
    if old[:1].isupper() and new[:1].islower():
        new = new[0].upper() + new[1:]
    return text[:start] + new + text[end:]


GRAMMATICAL = FUNCTION_WORDS | STOPWORDS | {"and", "or", "but", "not", "no", "so", "if", "as", "by", "me", "us",
                                             "a", "an", "i", "o", "ze", "jak", "ale", "nebo", "kdyz", "jsem"}


def is_grammatical(word: str) -> bool:
    """Articles, pronouns, prepositions, auxiliaries and other short words: no alternatives are offered."""
    key = norm(word)
    return len(key) <= 2 or key in GRAMMATICAL


def alternatives(searcher, word: str, rel: float, source: str, src: str, tgt: str, cache: dict | None = None):
    """Dictionary alternatives for a word of the translation (language tgt).

    The words of the source text whose translations include the word are the ones it was translated from
    ("brouku" -> brouk: beetle, love, bug, honey, sweetheart); their other translations are the alternatives.
    The closest one by relative position (rel: 0 = start of the text, 1 = end) comes first. When no source word
    explains it, synonyms through a round trip over the other language ("bug" -> chyba -> error, fault).
    Returns [(kind, hit, token, alts)]: kind 'src' (hit is a source word, token its spelling in the source)
    or 'syn' (hit is the word's own entry); alts are strings in tgt. cache keeps source lookups per paragraph."""
    cache = {} if cache is None else cache
    if is_grammatical(word):
        return []  # "the" -> "body builder", "I" -> "ego": nothing useful to offer for these
    forms = {norm(word)}
    own = {}  # the word's own entries ("trips" -> trip, "líbí" -> líbit se), not prefixes or typo guesses
    for h in searcher.search(word, limit=4):
        if h.lang == tgt and h.tier > WHOLE and (h.nwords == 1 or (h.label or "").startswith("tvar")):
            k = norm(h.text)
            if k not in own or (own[k].text != word and h.text in (word, word.lower())):  # frog, not Frog
                own[k] = h
    forms |= set(own)
    own = list(own.values())

    tokens = [(m.group(), m.start()) for m in _TOKEN.finditer(source)]
    found = []
    seen = set()
    for tok, pos in tokens:
        key = tok.casefold()
        if key not in cache:
            cache[key] = [(h, _clean(searcher.translations(h, 12)))
                          for h in searcher.search(tok, limit=4) if h.lang == src]
        for h, trans in cache[key]:
            if h.term_id in seen or not any(norm(t) in forms for t in trans):
                continue
            seen.add(h.term_id)
            alts = _distinct(t for t in trans if norm(t) not in forms)
            if alts:
                found.append((abs(pos / max(1, len(source)) - rel), len(found), ("src", h, tok, alts)))
    if found:
        return [g for _d, _i, g in sorted(found)[:MAX_GROUPS]]

    groups = []
    for h in own[:MAX_GROUPS]:
        alts = []
        for t in _clean(searcher.translations(h, 4)):
            for back in searcher.search(t, limit=2):
                if back.lang == src and norm(back.text) == norm(t):
                    alts += [x for x in _clean(searcher.translations(back, 6)) if norm(x) not in forms]
        alts = _distinct(alts)
        if alts:
            groups.append(("syn", h, word, alts))
    return groups


def _clean(translations) -> list[str]:
    """Translation texts without the vulgar ones (not offered as a replacement)."""
    return [t for t, note in translations if "vulg." not in (note or "")]


def _distinct(words) -> list[str]:
    out, seen = [], set()
    for w in words:
        if norm(w) not in seen:
            seen.add(norm(w))
            out.append(w)
    return out[:MAX_ALTS]
