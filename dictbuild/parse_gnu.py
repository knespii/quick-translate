"""GNU/FDL English-Czech dictionary (en-cs.txt): EN \t CS \t class+tags \t note \t author."""
import re

from dictbuild.model import EN, F_ACRONYM, F_PROPER, P_VULGAR, SRC_GNU, Collection

_TAG = re.compile(r"\[([^\]]+)\]")
_PAREN = re.compile(r"\s*\(([^)]*)\)\s*")
NAME_FILLER = ("ženské křestní jméno", "mužské křestní jméno", "okres v USA", "příjmení", "křestní jméno")
POS = {"n": "n", "v": "v", "adj": "adj", "adv": "adv", "prep": "prep", "conj": "conj", "pron": "pron",
       "interj": "intj", "num": "num"}


def _case_flags(text: str) -> int:
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 2 and all(c.isupper() for c in letters):
        return F_ACRONYM
    if text[:1].isupper():
        return F_PROPER
    return 0


def _cs_variants(cs: str) -> tuple[list[str], str | None]:
    """'vzdát (se)' -> ['vzdát', 'vzdát se']; other parentheticals go to the note."""
    note = None
    m = _PAREN.search(cs)
    if m:
        inner = m.group(1).strip()
        base = _PAREN.sub(" ", cs).strip()
        if inner in ("se", "si"):
            return [base, f"{base} {inner}"], None
        if re.search(r"\w\(", cs):  # zout(vat): keep the short form
            return [re.sub(r"\([^)]*\)", "", cs).strip()], None
        note = inner
        cs = base
    if re.search(r"[,/]", cs):
        parts = [p.strip() for p in re.split(r"\s*[,/]\s*", cs) if len(p.strip()) >= 2]
        # Split lists of synonyms ('vyhodit, vyhazovat'), never descriptions ('opilec, který toho moc nesnese').
        if parts and all(len(p.split()) <= 2 for p in parts):
            return parts, note
    return [cs], note


def parse(path, col: Collection) -> dict:
    stats = {"rows": 0, "pairs": 0, "irregular": 0, "skipped_empty": 0, "skipped_abbr": 0}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 5:
                continue
            en, cs, cls, note, author = (c.strip() for c in cols[:5])
            stats["rows"] += 1
            if " = " in en:  # '10-4 = ten-four', 'teh = teh': keep the headword only
                en = en.split(" = ")[0].strip()
            if not cs or not en:
                stats["skipped_empty"] += 1
                continue
            tags = _TAG.findall(cls)
            pos = POS.get(cls.split(":")[0].strip()) if ":" in cls else None
            if "neprav." in tags:
                # 'went -> go/went/gone': the first item is the lemma, the rest are its forms.
                items = [x.strip() for x in cs.split("/") if x.strip()]
                if items:
                    for x in items[1:]:
                        col.form(EN, x, items[0], tag="past")
                    stats["irregular"] += 1
                continue
            if "zkr." in tags and cs.isascii() and len(cs.split()) > 1:
                stats["skipped_abbr"] += 1  # English expansion of an English abbreviation
                continue
            machine = "automatický překlad" in author
            if machine and "jmén." in tags and (cs == en or cs == "USA" or any(cs.startswith(x) for x in NAME_FILLER)):
                continue  # machine-translated place-name descriptions
            weight = 0.5 if machine else None
            domain = ", ".join(t for t in tags if t not in ("neprav.",))
            en_flags = _case_flags(en)
            if any(cs.startswith(x) for x in NAME_FILLER) or cs.startswith("město -"):
                en_flags |= F_PROPER
            variants, pnote = _cs_variants(cs)
            full_note = "; ".join(x for x in (domain, note, pnote) if x) or None
            vulgar = P_VULGAR if "vulg." in tags else 0
            for v in variants:
                col.pair(v, en, SRC_GNU, pos=pos, note=full_note[:80] if full_note else None, weight=weight,
                         cs_flags=_case_flags(v), en_flags=en_flags, flags=vulgar)
                stats["pairs"] += 1
    return stats
