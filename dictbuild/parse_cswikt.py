"""Czech Wiktionary (kaikki cs-extract): Czech entries with English translations and Czech definitions,
plus English entries whose Czech glosses are translations."""
import gzip
import json
import re

from dictbuild.model import SRC_CSWIKT, Collection

_PAREN = re.compile(r"\s*\([^()]*\)\s*")
# Czech meta-glosses of English words: 'préteritum slovesa steal', 'množné číslo od …', 'zkratka …'.
_META_CS = re.compile(r"^(préteritum|příčestí|tvar|množné číslo|jednotné číslo|zkratka|\d\. osoba|osoba|"
                      r"minulý čas|přítomné příčestí|stupňování|zdrobnělina|ženské jméno|mužské jméno|příjmení|"
                      r"město|stát|řeka|ostrov|obec|okres|jméno)\b", re.IGNORECASE)


def _strip_parens(s: str) -> str:
    while True:
        s2 = _PAREN.sub(" ", s)
        if s2 == s:
            return s.strip()
        s = s2
SKIP_POS = {"suffix", "prefix", "character", "interfix", "symbol", "abbrev"}


def _short_glosses(gloss: str) -> list[str]:
    g = _strip_parens(gloss).rstrip(".")
    if not g or ":" in g or "(" in g or ")" in g or _META_CS.search(g):
        return []
    out = []
    for p in re.split(r"\s*[;,]\s*", g):
        p = p.strip()
        if p and len(p.split()) <= 3:
            out.append(p)
    return out


def parse(path, col: Collection) -> dict:
    st = {"cs_entries": 0, "pairs_cs": 0, "pairs_en": 0, "defs": 0}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            lang, word = d.get("lang_code"), (d.get("word") or "").strip()
            if not word or d.get("pos") in SKIP_POS or word.startswith("-") or word.endswith("-"):
                continue
            if lang == "cs":
                st["cs_entries"] += 1
                for t in d.get("translations", []):
                    if t.get("lang_code") == "en" and t.get("word"):
                        en = _strip_parens(t["word"])
                        if en and len(en.split()) <= 6 and "(" not in en and ")" not in en:
                            col.pair(word, en, SRC_CSWIKT, note=(t.get("sense") or None))
                            st["pairs_cs"] += 1
                for s in d.get("senses", []):
                    if "form-of" in (s.get("tags") or []):
                        continue
                    for g in s.get("glosses") or []:
                        if len(g) > 6:
                            col.defs_cs.append((word, g[:300]))
                            st["defs"] += 1
            elif lang == "en":
                proper = word[:1].isupper()
                for s in d.get("senses", []):
                    if "form-of" in (s.get("tags") or []):
                        continue
                    for g in s.get("glosses") or []:
                        for cs in _short_glosses(g):
                            if proper and not cs[:1].isupper():
                                continue  # 'London' -> 'Londýn', not 'město v Kanadě'
                            col.pair(cs, word, SRC_CSWIKT)
                            st["pairs_en"] += 1
    return st
