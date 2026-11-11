"""Paragraph mode helpers: tell a paragraph from a dictionary query, guess its language, tidy a browser selection.

Pure Python (no GTK) so it can be tested and reused by the popup and the prefill.
"""
import re

MAX_PARAGRAPH_CHARS = 2000
MAX_QUERY_WORDS = 8

# A sentence end only counts when more text follows ("Ahoj." alone is still a query).
_SENTENCE_END = re.compile(r"[.!?…]+[\"'”“»)\]]*\s+\S")
_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
_CS_CHARS = set("áčďéěíňóřšťúůýž")
_CS_WORDS = frozenset(
    "a i v ve na se si je jsou byl byla bylo být že ale nebo to ten ta tak jak co kde když protože aby by "
    "jsem jsme jste není nejen ani také už jen pro po při od do za ze z k ke o s u já ty on ona my vy oni "
    "který která které jeho její jejich mezi přes bez proto tedy jako".split())
_EN_WORDS = frozenset(
    "the a an and or but of to in on at for with from by is are was were be been being it this that these "
    "those he she they we you i not no do does did have has had will would can could should which who what "
    "when where why how as if than then so there their its our your my".split())
# Lines that start a list item stay on their own line when joining hard-wrapped text.
_LIST_ITEM = re.compile(r"^\s*(?:[-*•–·]|\d+[.)])\s")


def is_paragraph(text: str) -> bool:
    """More than a dictionary query: many words, several sentences or several lines."""
    text = text.strip()
    if not text:
        return False
    if "\n" in text:
        return True
    if len(text.split()) > MAX_QUERY_WORDS:
        return True
    return bool(_SENTENCE_END.search(text))


def guess_direction(text: str) -> str:
    """'cs-en' for Czech text, 'en-cs' otherwise; works on long text, unclear input defaults to 'en-cs'."""
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return "en-cs"
    letters = [c for c in text.lower() if c.isalpha()]
    diacritics = sum(c in _CS_CHARS for c in letters) / len(letters)
    cs = sum(w in _CS_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    # Czech prose has ~5-10 % letters with diacritics, English none; a few Czech names in English text
    # can exceed that, so function words weigh more than diacritics.
    score = (cs - en) / len(words) + (diacritics - 0.02) * 3
    return "cs-en" if score > 0 else "en-cs"


def clean_selection(text: str) -> str:
    """Tidy a selected or pasted paragraph: one line per paragraph, single spaces, at most MAX_PARAGRAPH_CHARS."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u00ad", "").replace("\u200b", "")
    paragraphs = []
    for block in re.split(r"\n[ \t]*\n\s*", text):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        out = lines[0]
        for ln in lines[1:]:
            # Hard-wrapped lines join with a space; list items keep their own line.
            out += ("\n" if _LIST_ITEM.match(ln) else " ") + ln
        paragraphs.append(re.sub(r"[ \t]+", " ", out))
    return cap("\n\n".join(paragraphs))


def cap(text: str, limit: int = MAX_PARAGRAPH_CHARS) -> str:
    """text cut to at most limit characters, at a sentence end if there is one in the second half."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    # Prefer the last sentence end in the second half, so the cut text still reads as whole sentences.
    ends = [m.end() for m in re.finditer(r"[.!?…]+[\"'”“»)\]]*(?=\s)", head)]
    if ends and ends[-1] >= limit // 2:
        return head[:ends[-1]].rstrip()
    space = max(head.rfind(" "), head.rfind("\n"))
    if space >= limit // 2:
        return head[:space].rstrip()
    return head.rstrip()
