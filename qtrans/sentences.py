"""Find the sentence around a span of text, so the LLM can reason about what the whole sentence means.

Pure Python (no GTK). Heuristic, not a full sentence splitter: abbreviations, initials and Czech ordinals
("5. května") do not end a sentence, a lowercase start after a full stop does (people type casually).
"""
import re

# Punctuation that may end a sentence, with closing quotes/brackets, before whitespace or the end of text.
_END = re.compile(r"[.!?…]+[\"'”“»)\]]*(?=\s|$)")
_WS = re.compile(r"\s*")
_WORD_BEFORE = re.compile(r"([^\W_]+)\.$")
_ABBREVIATIONS = frozenset(
    "např tzv atd apod resp tj tzn mj cca popř str čes ing mgr bc dr doc prof pí sv st ul tel hod min "
    "mr mrs ms dr prof st vs etc approx fig jr sr".split())


def sentence_around(text: str, start: int, end: int) -> tuple[int, int]:
    """(s_start, s_end) of the sentence(s) of text that contain [start, end), surrounding whitespace excluded."""
    s_start, s_end = 0, len(text)
    for e, n in _boundaries(text):
        if n <= start:
            s_start = n
        elif e >= end:
            s_end = e
            break
    while s_start < s_end and text[s_start].isspace():
        s_start += 1
    while s_end > s_start and text[s_end - 1].isspace():
        s_end -= 1
    return s_start, s_end


def _boundaries(text: str):
    """(end of sentence, start of next one) pairs in text order; a newline always ends a sentence."""
    out = [(m.start(), m.end()) for m in re.finditer(r"\n+", text)]
    for m in _END.finditer(text):
        if not _is_abbreviation(text, m):
            out.append((m.end(), _WS.match(text, m.end()).end()))
    return sorted(out)


def _is_abbreviation(text: str, m: re.Match) -> bool:
    if m.group() != ".":
        return False
    w = _WORD_BEFORE.search(text, 0, m.end())
    if not w:
        return False
    word = w.group(1)
    if word.isdigit():
        # "5. května" is an ordinal; "in 2020. Then" still ends the sentence.
        nxt = text[_WS.match(text, m.end()).end():][:1]
        return nxt.islower()
    return len(word) == 1 and word.isalpha() or word.lower() in _ABBREVIATIONS
