"""In-memory collection of terms, pairs and forms that the parsers fill and build_db writes out."""
import unicodedata
from collections import defaultdict

CS, EN = 0, 1
LANGS = {"cs": CS, "en": EN}

# term flags
F_PROPER, F_ACRONYM, F_VULGAR, F_INFLECTED, F_WN_ONLY, F_MACHINE = 1, 2, 4, 8, 16, 32
# pair flags (stored in pair.flags)
P_INFLECTED, P_VULGAR = 1, 2

# source bits and weights
SRC_GNU, SRC_ENWIKT, SRC_CSWIKT, SRC_EXTRA = 1, 2, 4, 8
SRC_WEIGHT = {SRC_GNU: 1.0, SRC_ENWIKT: 1.2, SRC_CSWIKT: 1.0, SRC_EXTRA: 1.5}

# form kinds
K_FORM, K_VARIANT, K_CORE, K_INFL_PHRASE = 0, 2, 3, 4


def clean_text(s: str) -> str:
    s = unicodedata.normalize("NFC", s).strip()
    return " ".join(s.split())


class Collection:
    def __init__(self):
        self.terms = {}            # (lang, ident) -> dict(text, pos=Counter-like dict, flags)
        self.pairs = {}            # (cs_ident, en_ident) -> dict(weight, srcmask, pos, note, flags)
        self.forms = []            # (lang, form_text, lemma_text, kind, tag)
        self.aspects = []          # (lemma_text, partner_text)   Czech only
        self.defs = []             # (lang_of_term, term_text, definition_in_english)
        self.defs_cs = []          # (czech_word, czech definition) from Czech Wiktionary
        self.defs_wn = []          # ([english lemmas], english definition) from WordNet
        self.lemmas_cs = set()     # idents of Czech words that have their own (non form-of) Wiktionary entry

    @staticmethod
    def ident(text: str) -> str:
        t = clean_text(text)
        # A capitalized single word is its own term (Pole/pole, May/may, John/john); otherwise case-insensitive.
        if " " not in t and t[:1].isupper() and not t.isupper() and t[1:2].islower():
            folded = t.rstrip("!?.")
        else:
            folded = t.casefold().rstrip("!?.").rstrip()
        return folded or t.casefold()

    def term(self, lang: int, text: str, pos: str | None = None, flags: int = 0) -> tuple:
        text = clean_text(text)
        k = (lang, self.ident(text))
        t = self.terms.get(k)
        if t is None:
            t = self.terms[k] = {"text": text, "pos": defaultdict(int), "flags": 0}
        elif t["text"] != text and text.islower() and not t["text"].islower():
            t["text"] = text  # prefer the lowercase display form ("dog" over "Dog")
        elif t["text"] != text and t["text"].rstrip("!?.") == text:
            t["text"] = text  # prefer "co" over "co?"
        if pos:
            t["pos"][pos] += 1
        t["flags"] |= flags
        if not text[:1].isupper():
            t["lower"] = True  # seen in lower case somewhere: not a proper noun or acronym
        return k

    def pair(self, cs_text: str, en_text: str, src: int, pos=None, note=None, weight=None, flags=0,
             cs_flags=0, en_flags=0):
        """flags: P_VULGAR if this source marks the sense vulgar (a pair stays vulgar only if all sources agree)."""
        if not cs_text.strip() or not en_text.strip():
            return
        ck = self.term(CS, cs_text, pos, cs_flags)
        ek = self.term(EN, en_text, pos, en_flags)
        p = self.pairs.get((ck, ek))
        w = SRC_WEIGHT[src] if weight is None else weight
        if p is None:
            self.pairs[(ck, ek)] = {"weight": w, "srcmask": src, "pos": pos, "note": note, "flags": flags}
            return
        if not p["srcmask"] & src:
            p["weight"] += w
        else:
            p["weight"] = max(p["weight"], w)
        p["srcmask"] |= src
        p["pos"] = p["pos"] or pos
        p["note"] = p["note"] or note
        p["flags"] &= flags  # a pair is "inflected" only if every source says so

    def form(self, lang: int, form_text: str, lemma_text: str, kind: int = K_FORM, tag: str | None = None):
        if form_text and lemma_text and form_text != lemma_text:
            self.forms.append((lang, clean_text(form_text), clean_text(lemma_text), kind, tag))
