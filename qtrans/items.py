"""The rows of the result list (Item) and their formatting. No widgets here: the window draws them (popup.py)."""
from gi.repository import GLib, GObject

POS_LABEL = {"n": "podst. jm.", "v": "sloveso", "adj": "příd. jm.", "adv": "přísl.", "prep": "předložka",
             "conj": "spojka", "pron": "zájmeno", "intj": "citosl.", "num": "číslovka", "jm.": "vlastní jméno",
             "fráz.": "fráze", "přísl.": "přísloví", "part.": "částice", "zkr.": "zkratka"}


class Item(GObject.Object):
    """One row in the result list. kind: dict | mt | online | llm | info | head (a section header, not selectable).
    key identifies the row across updates, so the list can animate it to its new place. lang ('cs' | 'en') is the
    language of a dictionary headword (for the history)."""

    def __init__(self, kind, head, sub="", badge="", label="", copy=None, copy_alt=None, pending=False, alts=None,
                 key=None, lang=None):
        super().__init__()
        self.kind = kind
        self.head = head
        self.sub = sub
        self.badge = badge
        self.label = label
        self.copy = copy if copy is not None else head
        self.copy_alt = copy_alt
        self.pending = pending
        self.alts = alts or []  # individual translations, for Alt+1..9
        self.key = key or (kind if kind in ("mt", "online") else f"{kind}:{head}")
        self.lang = lang


def esc(s: str) -> str:
    return GLib.markup_escape_text(s or "")


def hit_item(h, label=None) -> Item:
    """A dictionary hit (search.Hit) as a row: headword, its translations, the first one's note."""
    trans = [tx for tx, _note in h.translations]
    sub = esc(", ".join(trans))
    notes = [n for _tx, n in h.translations[:1] if n]
    if notes:
        sub += f"  <span alpha='60%'>({esc(notes[0])})</span>"
    return Item("dict", h.text, sub, badge=h.lang.upper(), label=label or h.label or POS_LABEL.get(h.pos, h.pos or ""),
                copy=trans[0] if trans else h.text, copy_alt=h.text, alts=trans, key=f"d:{h.term_id}", lang=h.lang)


def history_item(entry: dict, lang: str, now: float) -> Item:
    """A remembered word (history.History.recent) as a dictionary-like row, labelled with how long ago."""
    trans = entry["translations"]
    return Item("dict", entry["text"], esc(", ".join(trans)), badge=lang.upper(), label=ago(entry["at"], now),
                copy=trans[0], copy_alt=entry["text"], alts=trans, key=f"h:{lang}:{entry['text']}", lang=lang)


def ago(at: float, now: float) -> str:
    """'právě teď', 'před 5 min', 'před 3 h', 'včera', 'před 4 dny' (rough, for a label)."""
    s = max(0.0, now - at)
    if s < 60:
        return "právě teď"
    if s < 3600:
        return f"před {int(s // 60)} min"
    if s < 86400:
        return f"před {int(s // 3600)} h"
    days = int(s // 86400)
    if days == 1:
        return "včera"
    return f"před {days} dny"  # (instrumental: 'před 2 dny', 'před 5 dny')
