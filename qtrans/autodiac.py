"""Automatic diacritics in the input: a word the user finishes (a space or punctuation typed after it) gets its
Czech diacritics back ('jak se mas ' -> 'jak se máš '); a prefilled or pasted text is restored whole.

Pure Python (no GTK): it returns edits, (start, end, new) character spans of the input, and the popup applies
them. Text that does not look Czech, words typed with diacritics and text written with diacritics stay as they are.
"""
import re
import unicodedata

from qtrans.normalize import CZECH_CHARS

_WORD = re.compile(r"[^\W\d_]+")  # the same tokens as qtrans.diacritics
FINISHERS = frozenset(" \t\n.,;:!?…)]}\"'»”“")  # typed after a word, they finish it
_CONTEXT = 160  # characters before the word the context rules may look at (the sentence so far)
_NEXT_DEPENDENT = frozenset({"ze", "te"})  # spelled by the word after them: 'ze školy' / 'že se', 'té doby' / 'tě'
_WRITTEN_SHARE = 0.01  # Czech prose has 5-10 % letters with diacritics, a text typed without them none


def written_with_diacritics(text: str) -> bool:
    """Was this text written with Czech diacritics (then its ASCII words are meant: 'byt' is not 'být')?"""
    letters = [c for c in text.lower() if c.isalpha()]
    return bool(letters) and sum(c in CZECH_CHARS for c in letters) / len(letters) >= _WRITTEN_SHARE


def strip_diacritics(text: str) -> str:
    """'Dál si pozor!' -> 'Dal si pozor!' (case, spaces and punctuation kept)."""
    return unicodedata.normalize("NFC", "".join(c for c in unicodedata.normalize("NFD", text)
                                                if not unicodedata.combining(c)))


def has_czech_letter(text: str) -> bool:
    return any(c in CZECH_CHARS for c in text.lower())


def token_edits(old: str, new: str, lo: int = 0, hi: int | None = None) -> list[tuple[int, int, str]]:
    """The words that differ between old and new (a restored copy: same text between words), as edits of old;
    only words inside [lo, hi]."""
    hi = len(old) if hi is None else hi
    a, b = list(_WORD.finditer(old)), list(_WORD.finditer(new))
    if len(a) != len(b):
        return []
    return [(x.start(), x.end(), y.group()) for x, y in zip(a, b)
            if x.group() != y.group() and lo <= x.start() and x.end() <= hi]


class AutoDiacritics:
    """d is a qtrans.diacritics.Diacritizer."""

    def __init__(self, d):
        self.d = d
        self._last = None  # (start, typed, shown) of the last 'ze'/'te' we spelled: the next word may respell it

    def typed(self, text: str, offset: int) -> list[tuple[int, int, str]]:
        """Edits after the user typed the character just before offset (a finisher completes the word before it)."""
        if not self.d.ok or not 2 <= offset <= len(text) or text[offset - 1] not in FINISHERS:
            return []
        end = offset - 1
        start = end
        while start > 0 and text[start - 1].isalpha():
            start -= 1
        word = text[start:end]
        if not word or not word.isascii() or (start > 0 and _glued(text[start - 1])):
            return []
        if not self.d.looks_czech(text):
            return []
        ctx_start = _context_start(text, start)
        if ctx_start is None:
            return []
        m = re.match(r"\s*\S*", text[offset:])  # the next word, if the user edits inside the text
        ctx_end = offset + m.end()
        ctx = text[ctx_start:ctx_end]
        lo = start
        last, self._last = self._last, None
        if last is not None:
            p_start, p_typed, p_shown = last
            prev = _token_before(text, start)
            if prev == (p_start, p_start + len(p_shown)) and text[prev[0]:prev[1]] == p_shown \
                    and len(p_typed) == len(p_shown) and p_start >= ctx_start:
                # Spell the ze/te again, now that the next word is known: as typed, so the rules see it.
                i = p_start - ctx_start
                ctx = ctx[:i] + p_typed + ctx[i + len(p_typed):]
                lo = p_start
        restored = self.d.restore(ctx, check=False)
        # Against the real text, not ctx: there the ze/te is still spelled as shown.
        edits = [(s + ctx_start, e + ctx_start, new)
                 for s, e, new in token_edits(text[ctx_start:ctx_end], restored, lo - ctx_start, end - ctx_start)]
        if word.lower() in _NEXT_DEPENDENT:
            shown = next((new for s, _e, new in edits if s == start), word)
            self._last = (start, word, shown)
        return edits

    def whole(self, text: str, lo: int = 0, hi: int | None = None) -> list[tuple[int, int, str]]:
        """Edits restoring a prefilled or pasted text (only the words inside [lo, hi]), if it is Czech typed
        without diacritics."""
        self._last = None
        if not self.d.ok or not text.strip() or written_with_diacritics(text):
            return []
        return token_edits(text, self.d.restore(text), lo, hi)


def _glued(c: str) -> bool:
    """A character that makes the word part of a code-like token ('file_name', 'x2y')."""
    return c.isdigit() or c in "_/\\@#=<>{}|~"


def _context_start(text: str, start: int) -> int | None:
    """Where the context of the word at start begins: at a word boundary up to _CONTEXT characters back."""
    lo = max(0, start - _CONTEXT)
    if lo == 0:
        return 0
    m = re.search(r"\s", text[lo:start])
    return lo + m.end() if m else None  # (no space in 160 characters: not prose)


def _token_before(text: str, start: int) -> tuple[int, int] | None:
    """The span of the word before the one at start, if only spaces or punctuation lie between them."""
    found = None
    for m in _WORD.finditer(text, max(0, start - _CONTEXT), start):
        found = m.span()
    return found
