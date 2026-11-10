"""Build data/dict.sqlite from the raw sources in data/raw/ (offline). Usage: python -m dictbuild.build_db"""
import datetime
import json
import os
import sqlite3
import sys
import re
import time
from collections import defaultdict
from pathlib import Path

from dictbuild import parse_cswikt, parse_freq, parse_gnu, parse_kaikki, parse_wordnet
from dictbuild.model import (CS, EN, F_INFLECTED, F_VULGAR, K_CORE, K_FORM, K_VARIANT, P_INFLECTED, P_VULGAR,
                             Collection)
from qtrans.normalize import FUNCTION_WORDS, NORM_VERSION, core_tokens, deletions, norm
from qtrans.paths import DB, RAW

SCHEMA = """
PRAGMA page_size=4096;
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID;
CREATE TABLE term(id INTEGER PRIMARY KEY, lang INTEGER NOT NULL, text TEXT NOT NULL, key TEXT NOT NULL,
  nwords INTEGER NOT NULL, zipf REAL NOT NULL, flags INTEGER NOT NULL DEFAULT 0, pos TEXT);
CREATE TABLE pair(cs_id INTEGER NOT NULL, en_id INTEGER NOT NULL, weight REAL NOT NULL, srcmask INTEGER NOT NULL,
  pos TEXT, note TEXT, flags INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(cs_id, en_id)) WITHOUT ROWID;  -- flags: 1 inflected, 2 vulgar
CREATE TABLE form(fkey TEXT NOT NULL, lemma_id INTEGER NOT NULL, kind INTEGER NOT NULL, form TEXT, tag TEXT,
  zipf REAL NOT NULL DEFAULT 0, PRIMARY KEY(fkey, lemma_id)) WITHOUT ROWID;
CREATE TABLE aspect(a_id INTEGER NOT NULL, b_id INTEGER NOT NULL, PRIMARY KEY(a_id, b_id)) WITHOUT ROWID;
CREATE VIRTUAL TABLE phrase_fts USING fts5(lem, tokenize='unicode61', prefix='2 3');
CREATE TABLE del1(dkey TEXT NOT NULL, term_id INTEGER NOT NULL, PRIMARY KEY(dkey, term_id)) WITHOUT ROWID;
CREATE VIRTUAL TABLE def_en USING fts5(heads, gloss, tokenize='porter unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE def_cs USING fts5(heads, gloss, lem, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE def_target(src INTEGER NOT NULL, def_id INTEGER NOT NULL, ord INTEGER NOT NULL, term_id INTEGER NOT NULL,
  PRIMARY KEY(src, def_id, ord)) WITHOUT ROWID;
"""
INDEXES = """
CREATE INDEX term_key ON term(key, lang);
CREATE INDEX pair_en ON pair(en_id, cs_id);
"""
UNKNOWN_ZIPF = 1.5


def log(msg, t0=[time.perf_counter()]):
    print(f"[{time.perf_counter() - t0[0]:6.1f}s] {msg}", flush=True)


def add_english_forms(col: Collection) -> int:
    """Regular English inflections (forgets, dogs, bigger) from lemminflect for single-word EN lemmas."""
    try:
        from lemminflect import getAllInflections, getAllLemmas
    except ImportError:
        log("lemminflect missing: no regular English forms")
        return 0
    upos_for = {"v": ["VERB"], "n": ["NOUN"], "adj": ["ADJ"], "adv": ["ADV"]}
    n = 0
    for (lang, _ident), t in list(col.terms.items()):
        w = t["text"]
        if lang != EN or not w.isalpha() or not w.islower() or len(w) < 2:
            continue
        poses = [p for pos in t["pos"] for p in upos_for.get(pos, [])] or ["VERB", "NOUN", "ADJ"]
        for upos in set(poses):
            lemmas = getAllLemmas(w, upos).get(upos, ())
            if w not in lemmas:
                continue
            for forms in getAllInflections(w, upos).values():
                for f in forms:
                    if f != w:
                        col.form(EN, f, w, K_FORM, upos.lower())
                        n += 1
    return n


def add_extra(path, col: Collection) -> int:
    """Hand-picked collocations (dictbuild/extra.tsv)."""
    from dictbuild.model import SRC_EXTRA
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) >= 2:
            col.pair(cols[0].strip(), cols[1].strip(), SRC_EXTRA, note=(cols[2].strip() if len(cols) > 2 else None))
            n += 1
    return n


def term_zipf(lang, text, cs_lemma, cs_word, en_freq):
    key = norm(text)
    toks = key.split()
    low = text.lower().rstrip("!?.")
    if len(toks) <= 1:
        if lang == CS:
            return max(cs_lemma.get(low, 0), cs_word.get(low, 0)) or UNKNOWN_ZIPF
        return en_freq.get(low) or en_freq.get(key) or UNKNOWN_ZIPF
    words = [w.strip(",;:!?.") for w in low.split()]
    core = core_tokens(toks)
    vals = []
    for w in words:
        if norm(w) not in core:
            continue
        if lang == CS:
            vals.append(cs_word.get(w) or cs_lemma.get(w) or UNKNOWN_ZIPF)
        else:
            vals.append(en_freq.get(w) or UNKNOWN_ZIPF)
    if not vals:
        return UNKNOWN_ZIPF
    return min(vals) - 0.5 * (len(vals) - 1)


def main() -> int:
    raw = {p.name: p for p in RAW.iterdir()} if RAW.exists() else {}
    need = ["gnu-en-cs.txt", "kaikki-czech.jsonl.gz", "syn2015_lemma_utf8.zip", "syn2015_word_utf8.zip", "en_full.txt"]
    missing = [n for n in need if n not in raw]
    if missing:
        print("missing raw files (run python -m dictbuild.fetch):", ", ".join(missing), file=sys.stderr)
        return 1

    col = Collection()
    log(f"GNU/FDL: {parse_gnu.parse(raw['gnu-en-cs.txt'], col)}")
    log(f"kaikki Czech: {parse_kaikki.parse(raw['kaikki-czech.jsonl.gz'], col)}")
    if "kaikki-cs-extract.jsonl.gz" in raw:
        log(f"Czech Wiktionary: {parse_cswikt.parse(raw['kaikki-cs-extract.jsonl.gz'], col)}")
    if "english-wordnet-2025.xml.gz" in raw:
        col.defs_wn = parse_wordnet.parse(raw["english-wordnet-2025.xml.gz"])
        log(f"WordNet: {len(col.defs_wn)} synsets")
    log(f"extra collocations: {add_extra(Path(__file__).with_name('extra.tsv'), col)}")
    log(f"English forms (lemminflect): {add_english_forms(col)}")
    cs_lemma, cs_word = parse_freq.czech(raw["syn2015_lemma_utf8.zip"], raw["syn2015_word_utf8.zip"])
    en_freq = parse_freq.english(raw["en_full.txt"])
    log(f"frequencies: cs lemmas {len(cs_lemma)}, cs words {len(cs_word)}, en {len(en_freq)}")

    # --- forms: keep only those whose lemma is a dictionary term ------------------------------
    def lemma_targets(lang, lemma):
        """Term keys a form belongs to: the lemma itself and, in Czech, its reflexive 'X se' / 'X si' entries."""
        out = []
        for suffix in ("", " se", " si") if lang == CS else ("",):
            k = (lang, Collection.ident(lemma + suffix))
            if k in col.terms:
                out.append(k)
        return out

    form_of = defaultdict(set)  # (lang, ident(form)) -> {(lang, ident(lemma))}
    en_form_tags = defaultdict(set)
    for lang, f, lemma, kind, tag in col.forms:
        if kind == K_FORM:
            for lk in lemma_targets(lang, lemma):
                form_of[(lang, Collection.ident(f))].add(lk)
            if lang == EN and tag:
                en_form_tags[(EN, Collection.ident(f))].add(tag)
    lemma_cs = col.lemmas_cs

    def is_inflected(k):
        """A term that is a form of another term and not a lemma of its own."""
        others = form_of.get(k, set()) - {k}
        if not others:
            return False
        if k[0] == CS:
            return k[1] not in lemma_cs
        return True

    # --- pairs ----------------------------------------------------------------------------------
    # inflected -> inflected verbs (went -> šel) are dropped; plural nouns (children -> děti) are kept, marked.
    # A half-inflected pair survives only if the lemma of its inflected side has the same translation
    # (mice -> myši yes; mouth -> sta, a truncated 'ústa', no).
    known = set(col.pairs)
    dropped = 0
    for (ck, ek), p in list(col.pairs.items()):
        en_t = col.terms[ek]["text"]
        cs_t = col.terms[ck]["text"]
        vulgar = p["flags"] & P_VULGAR
        en_inf = " " not in en_t and is_inflected(ek) and p["pos"] not in ("n", "adj", "adv")
        cs_inf = " " not in cs_t and is_inflected(ck)
        keep, infl = True, False
        if en_inf and cs_inf:
            tags = en_form_tags.get(ek, set())
            keep = not (p["pos"] == "v" or (tags & {"past", "verb"} and "noun" not in tags))
            infl = True
        elif cs_inf:
            keep = any((lk, ek) in known for lk in form_of[ck] - {ck}) or any(
                (lk, le) in known for lk in form_of[ck] - {ck} for le in form_of.get(ek, ()))
            infl = True
        elif en_inf:
            keep = any((ck, le) in known for le in form_of[ek] - {ek})
            infl = True
        if not keep:
            del col.pairs[(ck, ek)]
            dropped += 1
        else:
            p["flags"] = vulgar | (P_INFLECTED if infl else 0)
    log(f"pairs: {len(col.pairs)} (dropped {dropped} inflected/junk)")

    # Terms that occur only through inflected pairs get F_INFLECTED (ranked below the lemma's FORM hit);
    # terms whose every pair is vulgar get F_VULGAR.
    has_clean, has_decent, used = set(), set(), set()
    for (ck, ek), p in col.pairs.items():
        used.update((ck, ek))
        if not p["flags"] & P_INFLECTED:
            has_clean.update((ck, ek))
        if not p["flags"] & P_VULGAR:
            has_decent.update((ck, ek))
    for k in used - has_clean:
        if is_inflected(k):
            col.terms[k]["flags"] |= F_INFLECTED
    for k in used - has_decent:
        col.terms[k]["flags"] |= F_VULGAR

    # --- assign ids in rank order (fewest words first, then most frequent) --------------------
    rows = []
    for k in used:
        t = col.terms[k]
        key = norm(t["text"])
        if not key:
            continue
        z = term_zipf(k[0], t["text"], cs_lemma, cs_word, en_freq)
        pos = max(t["pos"], key=t["pos"].get) if t["pos"] else None
        rows.append((len(key.split()), -z, t["text"], k, key, z, pos))
    rows.sort()
    ids = {}
    term_rows = []
    for i, (nw, _nz, text, k, key, z, pos) in enumerate(rows, start=1):
        ids[k] = i
        flags = col.terms[k]["flags"]
        if col.terms[k].get("lower"):
            flags &= ~3  # F_PROPER | F_ACRONYM
        term_rows.append((i, k[0], text, key, nw, round(z, 3), flags, pos))
    key_of = {r[0]: r[3] for r in term_rows}
    by_key = defaultdict(list)  # (lang, key) -> [id]
    for r in term_rows:
        by_key[(r[1], r[3])].append(r[0])
    log(f"terms: {len(term_rows)}")

    # --- form table ---------------------------------------------------------------------------
    form_rows = {}
    for lang, f, lemma, kind, tag in col.forms:
        fkey = norm(f)
        if not fkey:
            continue
        z = (cs_word.get(f.lower()) if lang == CS else en_freq.get(f.lower())) or UNKNOWN_ZIPF
        for lk in lemma_targets(lang, lemma):
            lid = ids.get(lk)
            if lid is None or fkey == key_of[lid]:
                continue
            prev = form_rows.get((fkey, lid))
            if prev is None or kind < prev[2]:
                form_rows[(fkey, lid)] = (fkey, lid, kind, f, tag, round(z, 3))
    # Aliases: phrase without se/si and placeholders ('dát pozor' for 'dát si pozor'), joined hyphen words.
    n_alias = 0
    for r in term_rows:
        tid, lang, text, key, nw = r[:5]
        if nw > 1:
            core = " ".join(core_tokens(key.split()))
            # no alias 'to' for 'to someone' when 'to' is a word of its own
            if core and core != key and (core, tid) not in form_rows and not (" " not in core and by_key.get((lang, core))):
                form_rows[(core, tid)] = (core, tid, K_CORE, None, "core", 0)
                n_alias += 1
        if "-" in text:
            joined = key.replace(" ", "")
            if joined != key and (joined, tid) not in form_rows:
                form_rows[(joined, tid)] = (joined, tid, K_VARIANT, None, "joined", 0)
                n_alias += 1
    log(f"forms: {len(form_rows)} ({n_alias} aliases)")

    # --- phrase index: tokens plus lemmas of tokens ---------------------------------------------
    # Lemmas are looked up by the spelling with diacritics ('koně' -> kůň, 'vedoucí' -> vést, not vědět),
    # never for function words or abbreviations ('dám.'), and a reflexive lemma counts by its verb ('zeptat').
    surf_lemmas = defaultdict(set)  # (lang, surface lowercase) -> {lemma key}
    for fkey, lid, kind, form, *_ in form_rows.values():
        if kind == K_FORM and form:
            lk = key_of[lid]
            if lk.endswith((" se", " si")) and lk.count(" ") == 1:
                lk = lk.rsplit(" ", 1)[0]
            if " " not in lk:
                surf_lemmas[(term_rows[lid - 1][1], form.lower())].add(lk)

    def lemmatize(lang, text):
        abbrev = {w.lower() for w in re.findall(r"(\w+)\.", text)}
        surface = [w for w in re.split(r"[\W_]+", text.casefold().replace("'", "").replace("\u2019", "")) if w]
        out = []
        for w in surface:
            k = norm(w)
            if not k:
                continue
            lems = set() if (k in FUNCTION_WORDS or w in abbrev) else surf_lemmas.get((lang, w), set())
            out.append((k, lems))
        return out

    fts_rows = []
    infl_phrases = 0
    flags_update = []
    for r in term_rows:
        tid, lang, text, key, nw, z, flags, pos = r
        if nw < 2:
            continue
        toks = lemmatize(lang, text)
        lem = list(dict.fromkeys([k for k, _ in toks] + [lk for _, ls in toks for lk in sorted(ls)]))
        fts_rows.append((tid, " ".join(lem) or key))
        if lang == CS:
            # 'dávejte pozor' is an inflected copy of 'dávat pozor': rank it lower.
            lk = " ".join(next(iter(ls)) if len(ls) == 1 and not by_key.get((CS, k)) else k for k, ls in toks)
            if lk != key and by_key.get((lang, lk)):
                flags_update.append((flags | F_INFLECTED, tid))
                infl_phrases += 1
    log(f"phrase index rows: {len(fts_rows)} ({infl_phrases} inflected phrases)")

    # --- definitions for the describe-a-word mode ---------------------------------------------
    def_en_rows, def_cs_rows, target_rows = [], [], []
    for lemmas, definition in col.defs_wn:
        tids = [ids[k] for k in ((EN, Collection.ident(x.replace("_", " "))) for x in lemmas) if k in ids]
        if tids:
            did = len(def_en_rows) + 1
            def_en_rows.append((did, ", ".join(lemmas), definition))
            target_rows += [(0, did, i, t) for i, t in enumerate(tids[:6])]
    for lang, text, definition in col.defs:  # long English glosses of Czech words (en.wiktionary)
        tid = ids.get((lang, Collection.ident(text)))
        if tid:
            did = len(def_en_rows) + 1
            def_en_rows.append((did, text, definition))
            target_rows.append((0, did, 0, tid))
    for word, definition in col.defs_cs:
        tid = ids.get((CS, Collection.ident(word)))
        if not tid:
            continue
        toks = lemmatize(CS, definition)
        lem = list(dict.fromkeys([k for k, _ in toks] + [lk for _, ls in toks for lk in sorted(ls)]))
        did = len(def_cs_rows) + 1
        def_cs_rows.append((did, word, definition, " ".join(lem)))
        target_rows.append((1, did, 0, tid))
    log(f"definitions: {len(def_en_rows)} English, {len(def_cs_rows)} Czech")

    # --- typo index -----------------------------------------------------------------------------
    del_rows = set()
    for r in term_rows:
        tid, key, nw = r[0], r[3], r[4]
        if nw == 1 and 4 <= len(key) <= 20:
            del_rows.add((key, tid))
            for d in deletions(key):
                del_rows.add((d, tid))
    log(f"typo index rows: {len(del_rows)}")

    # --- aspect partners (Czech) ----------------------------------------------------------------
    aspect_rows = set()
    for a, b in col.aspects:
        for suffix in ("", " se", " si"):  # zeptat se <-> ptát se
            ia, ib = ids.get((CS, Collection.ident(a + suffix))), ids.get((CS, Collection.ident(b + suffix)))
            if ia and ib and ia != ib:
                aspect_rows.add((ia, ib))
                aspect_rows.add((ib, ia))

    # --- write -----------------------------------------------------------------------------------
    DB.parent.mkdir(parents=True, exist_ok=True)
    work = DB.with_name("dict.sqlite.build")
    tmp = DB.with_name("dict.sqlite.tmp")
    for p in (work, tmp):
        p.unlink(missing_ok=True)
    con = sqlite3.connect(work)
    con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
    con.executemany("INSERT INTO term VALUES (?,?,?,?,?,?,?,?)", term_rows)
    con.executemany("UPDATE term SET flags=? WHERE id=?", flags_update)
    con.executemany("INSERT INTO pair VALUES (?,?,?,?,?,?,?)", sorted(
        (ids[ck], ids[ek], round(p["weight"], 2), p["srcmask"], p["pos"], p["note"], p["flags"])
        for (ck, ek), p in col.pairs.items() if ck in ids and ek in ids))
    con.executemany("INSERT INTO form VALUES (?,?,?,?,?,?)", sorted(form_rows.values()))
    con.executemany("INSERT INTO aspect VALUES (?,?)", sorted(aspect_rows))
    con.executemany("INSERT INTO phrase_fts(rowid, lem) VALUES (?,?)", fts_rows)
    con.execute("INSERT INTO phrase_fts(phrase_fts) VALUES('optimize')")
    con.executemany("INSERT INTO del1 VALUES (?,?)", sorted(del_rows))
    con.executemany("INSERT INTO def_en(rowid, heads, gloss) VALUES (?,?,?)", def_en_rows)
    con.executemany("INSERT INTO def_cs(rowid, heads, gloss, lem) VALUES (?,?,?,?)", def_cs_rows)
    con.executemany("INSERT INTO def_target VALUES (?,?,?,?)", sorted(set(target_rows)))
    con.execute("INSERT INTO def_en(def_en) VALUES('optimize')")
    con.execute("INSERT INTO def_cs(def_cs) VALUES('optimize')")
    con.executescript(INDEXES)
    manifest = json.loads((RAW / "manifest.json").read_text()) if (RAW / "manifest.json").exists() else {}
    meta = {
        "norm_version": str(NORM_VERSION),
        "build_date": datetime.datetime.now().isoformat(timespec="seconds"),
        "sources": json.dumps({k: {"sha256": v.get("sha256"), "license": v.get("license")} for k, v in manifest.items()}),
    }
    con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    con.commit()
    con.execute("ANALYZE")
    con.execute("PRAGMA optimize")
    con.execute("VACUUM INTO ?", (str(tmp),))
    con.close()
    work.unlink()
    os.replace(tmp, DB)
    log(f"wrote {DB} ({DB.stat().st_size / 1e6:.1f} MB): terms {len(term_rows)}, pairs {len(col.pairs)}, "
        f"forms {len(form_rows)}, phrases {len(fts_rows)}, typo keys {len(del_rows)}, aspect {len(aspect_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
