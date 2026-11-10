"""English Wiktionary Czech entries (kaikki.org JSONL): CZ->EN glosses, inflected forms, aspect partners."""
import gzip
import json
import re

from dictbuild.model import CS, F_PROPER, K_FORM, K_VARIANT, P_VULGAR, SRC_ENWIKT, Collection
from qtrans.normalize import norm

SKIP_POS = {"suffix", "prefix", "character", "interfix", "combining_form", "symbol", "infix", "circumfix"}
POS = {"noun": "n", "verb": "v", "adj": "adj", "adv": "adv", "name": "jm.", "phrase": "fráz.", "pron": "pron",
       "num": "num", "prep": "prep", "conj": "conj", "intj": "intj", "particle": "part.", "proverb": "přísl.",
       "det": "pron", "contraction": "zkr.", "prep_phrase": "fráz."}

GRAMMAR = {"singular", "plural", "dual", "nominative", "genitive", "dative", "accusative", "vocative", "locative",
           "instrumental", "masculine", "feminine", "neuter", "animate", "inanimate", "participle", "present", "past",
           "future", "active", "passive", "indicative", "imperative", "conditional", "transgressive", "infinitive",
           "first-person", "second-person", "third-person", "comparative", "superlative", "adjectival", "short-form",
           "negative", "form-of", "possessive"}
CORE = {"singular", "plural", "dual", "nominative", "genitive", "dative", "accusative", "vocative", "locative",
        "instrumental", "participle", "infinitive", "imperative", "first-person", "second-person", "third-person",
        "comparative", "superlative", "transgressive"}
# Lemmas whose forms do not start like the lemma (pes -> psa starts the same, jít -> šel does not).
SUPPLETIVE = {"být", "jít", "člověk", "dobrý", "špatný", "zlý", "malý", "velký", "dítě", "rok", "oko", "ucho", "já", "ty",
              "on", "ona", "ono", "my", "vy", "oni", "se", "jeden", "dva", "mnoho", "málo", "dobře", "špatně", "brzy",
              "hodně", "vzít", "brát", "chtít", "mít", "jíst", "spát", "stát", "dát", "vědět", "moci", "smět", "ten",
              "kdo", "co", "náš", "váš", "svůj", "můj", "tvůj", "jenž", "pan", "bůh"}

_PAREN = re.compile(r"\s*[(\[][^()\[\]]*[)\]]\s*")
# Grammatical meta-glosses that are not translations: 'vocative singular of stůl', 'comparative degree of dobrý'.
_META = re.compile(r"\b(singular|plural|dual|nominative|genitive|dative|accusative|vocative|locative|instrumental|"
                   r"participle|person|degree|short form|form|spelling|synonym|diminutive|augmentative|"
                   r"abbreviation|initialism|alternative|past tense|transgressive|imperative)\s+of\b|"
                   r"\brelating to\b|^of,? (from|or)\b", re.IGNORECASE)
_NOT_A_DEFINITION = re.compile(
    r"^(used other than|alternative|obsolete|archaic|dated|abbreviation|initialism|acronym|clipping|diminutive|"
    r"augmentative|synonym|inflection|nominative|genitive|plural|feminine|masculine)|\bsee\b|\bform of\b",
    re.IGNORECASE)
_DEFN = re.compile(r"^(one who|someone who|a person who|something that|the act of|used to|any of|a type of|a kind of)\b")


def tags_ok(tags: set) -> bool:
    return bool(tags) and tags <= GRAMMAR and bool(tags & CORE)


def stem_ok(form: str, lemma: str) -> bool:
    if lemma in SUPPLETIVE:
        return True
    f, l = norm(form), norm(lemma)
    if not f or not l:
        return False
    for pre in ("nej", "ne", ""):
        if f.startswith(pre) and f[len(pre):len(pre) + 1] == l[:1]:
            return True
    return False


def split_gloss(gloss: str) -> tuple[list[str], str | None, str | None]:
    """Returns (short translations, a note, a long definition)."""
    note_m = re.search(r"\(([^()]*)\)", gloss)
    note = note_m.group(1).strip() if note_m else None
    g = gloss
    while True:  # nested parentheses: strip the innermost first
        g2 = _PAREN.sub(" ", g)
        if g2 == g:
            break
        g = g2
    g = g.strip().rstrip(".")
    if not g or ":" in g or "(" in g or ")" in g or _META.search(g):
        return [], None, None
    out = []
    long_parts = []
    for part in re.split(r"\s*;\s*", g):
        for p in re.split(r"\s*,\s*", part):
            p = p.strip()
            if p.startswith("to "):
                p = p[3:]
            if not p:
                continue
            if len(p.split()) <= 4 and not _DEFN.match(p):
                out.append(p)
            else:
                long_parts.append(p)
    return out, (note[:80] if note else None), ("; ".join(long_parts) or None)


def reflexive_text(word: str, sense: dict) -> str:
    raw = " ".join(sense.get("raw_glosses") or sense.get("glosses") or [])
    m = re.search(r"reflexive with (se|si)\b", raw)
    pron = m.group(1) if m else "se"
    if word.endswith((" se", " si")):
        return word
    return f"{word} {pron}"


def parse(path, col: Collection) -> dict:
    st = {"entries": 0, "pairs": 0, "forms": 0, "forms_dropped": 0, "aspects": 0, "defs": 0}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("lang_code") != "cs":
                continue
            word, kpos = d.get("word", "").strip(), d.get("pos")
            if not word or kpos in SKIP_POS or word.startswith("-") or word.endswith("-"):
                continue
            st["entries"] += 1
            pos = POS.get(kpos)
            flags = F_PROPER if kpos == "name" else 0
            for s in d.get("senses", []):
                tags = set(s.get("tags", []))
                if s.get("form_of"):
                    for fo in s["form_of"]:
                        lemma = fo.get("word", "").strip()
                        t = tags - {"form-of"}
                        if lemma and " " not in word and tags_ok(t) and stem_ok(word, lemma):
                            col.form(CS, word, lemma, K_FORM, ",".join(sorted(t)))
                            st["forms"] += 1
                    continue
                if s.get("alt_of"):
                    for ao in s["alt_of"]:
                        if ao.get("word"):
                            col.form(CS, word, ao["word"].strip(), K_VARIANT, "alt")
                    continue
                glosses = s.get("glosses") or []
                if not glosses:
                    continue
                cs_text = reflexive_text(word, s) if "reflexive" in tags else word
                col.lemmas_cs.add(Collection.ident(word))
                trans, note, defn = split_gloss(glosses[-1])
                vulgar = P_VULGAR if tags & {"vulgar", "offensive"} else 0
                if kpos == "name":
                    trans = [t for t in trans if t[:1].isupper()]  # "Prague", not "a male surname"
                for en in trans:
                    col.pair(cs_text, en, SRC_ENWIKT, pos=pos, note=note, cs_flags=flags, flags=vulgar,
                             en_flags=F_PROPER if (flags and en[:1].isupper()) else 0)
                    st["pairs"] += 1
                definition = defn or (glosses[-1] if not trans and len(glosses[-1]) > 3 else None)
                if definition and not _NOT_A_DEFINITION.search(definition):
                    col.defs.append((CS, cs_text, definition))
                    st["defs"] += 1
            for fm in d.get("forms", []):
                form = (fm.get("form") or "").strip()
                t = set(fm.get("tags", []))
                if not form or form == "-" or form == word or " " in form or "table-tags" in t or "inflection-template" in t:
                    continue
                if t in ({"perfective"}, {"imperfective"}):
                    col.aspects.append((word, form))
                    st["aspects"] += 1
                elif t & {"alternative", "colloquial"} and t <= {"alternative", "colloquial"}:
                    col.form(CS, form, word, K_VARIANT, "alt")
                elif tags_ok(t) and stem_ok(form, word):
                    col.form(CS, form, word, K_FORM, ",".join(sorted(t)))
                    st["forms"] += 1
                else:
                    st["forms_dropped"] += 1
    return st
