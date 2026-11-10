"""Bidirectional Czech <-> English dictionary search over data/dict.sqlite.

Every keystroke runs a few indexed queries (exact, forms, prefix, phrases, typos), merges the candidates by
term and ranks them with: tier + 12 * zipf + bonuses - penalties. See docs/plan.md for the reasoning.

For a word reached through one of its forms ('šel' -> jít), the frequency of the form the user typed counts,
so a frequent form beats a rare exact word (sel = mudflow) and 'je' means být, not 'jé'.
"""
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from qtrans.normalize import (AUXILIARY, CLITICS, FUNCTION_WORDS, NORM_VERSION, PLACEHOLDERS, REFLEXIVE, STOPWORDS,
                              core_tokens, deletions, has_czech_chars, has_non_ascii_letter, norm, osa_distance)

CS, EN = 0, 1
LANG = {CS: "cs", EN: "en"}
F_PROPER, F_ACRONYM, F_VULGAR, F_INFLECTED = 1, 2, 4, 8
P_INFLECTED, P_VULGAR = 1, 2
K_FORM, K_VARIANT, K_CORE = 0, 2, 3

EXACT, ALIAS, NEG, INFLECTED, ASPECT = 1000, 960, 880, 870, 850
WHOLE, PREFIX, PHRASE_PREFIX, FUZZY, WORD_FALLBACK = 740, 650, 520, 400, 300
MAX_ZIPF = 6.5
LEADING = {"the", "a", "an", "to"}  # 'the dog', 'to run'

# Pronouns in a query can stand for the placeholders in dictionary phrases ('přijít na to' ~ 'přijít na něco').
PRONOUN_SLOTS = {
    "to": ("neco",), "ho": ("nekoho", "neco"), "ji": ("nekoho",), "je": ("neco", "nekoho"), "mu": ("nekomu", "necemu"),
    "mi": ("nekomu",), "ti": ("nekomu",), "te": ("nekoho",), "nam": ("nekomu",), "vam": ("nekomu",),
    "nas": ("nekoho",), "vas": ("nekoho",), "mne": ("nekoho", "nekomu"),
    "it": ("sth", "something"), "him": ("sb", "somebody", "someone"), "her": ("sb", "somebody", "someone"),
    "them": ("sb", "sth"), "me": ("sb", "nekoho"), "you": ("sb",), "this": ("sth",), "that": ("sth",),
}


@dataclass
class Hit:
    term_id: int
    lang: str
    text: str
    key: str
    nwords: int
    zipf: float
    flags: int
    pos: str | None
    tier: float = 0
    label: str | None = None
    score: float = 0
    translations: list = field(default_factory=list)  # [(text, note)]


def surface_tokens(raw: str) -> list[str]:
    """The words as typed (lowercase, diacritics kept), aligned with norm(raw).split()."""
    return [w for w in re.split(r"[\W_]+", raw.casefold().replace("'", "").replace("’", "")) if w]


class Searcher:
    def __init__(self, path: Path):
        self.con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        self.con.execute("PRAGMA mmap_size=268435456")
        self.con.execute("PRAGMA query_only=1")
        meta = dict(self.con.execute("SELECT k, v FROM meta"))
        if meta.get("norm_version") != str(NORM_VERSION):
            raise RuntimeError(f"dictionary built with norm_version {meta.get('norm_version')}, "
                               f"app expects {NORM_VERSION}: rebuild with python -m dictbuild.build_db")
        self.meta = meta

    # --- helpers -------------------------------------------------------------------------------
    def _lemma_keys(self, token: str, surface: str | None = None) -> set[str]:
        """Lemma keys of a word ('dal' -> 'dat'); a reflexive lemma counts by its verb ('zeptal' -> 'zeptat').
        With `surface` (the query was typed with diacritics), only forms spelled exactly so count."""
        if token in FUNCTION_WORDS:
            return set()
        strict = surface is not None
        out = set()
        for key, form in self.con.execute(
                "SELECT t.key, f.form FROM form f JOIN term t ON t.id = f.lemma_id WHERE f.fkey = ? AND f.kind = 0",
                (token,)):
            if strict and form and form.lower() != surface:
                continue
            if key.endswith((" se", " si")) and key.count(" ") == 1:
                key = key.rsplit(" ", 1)[0]
            if " " not in key:
                out.add(key)
        return out

    def _key(self, tid: int) -> str:
        return self.con.execute("SELECT key FROM term WHERE id = ?", (tid,)).fetchone()[0]

    def _aspect_keys(self, keys: set[str]) -> set[str]:
        marks = ",".join("?" * len(keys))
        rows = self.con.execute(
            f"SELECT t2.key FROM term t1 JOIN aspect a ON a.a_id = t1.id JOIN term t2 ON t2.id = a.b_id "
            f"WHERE t1.lang = 0 AND t1.key IN ({marks})", list(keys))
        return {k for (k,) in rows} - keys

    def _forms(self, key: str, add, shift: float = 0, label: str | None = None, refl: set | None = None) -> bool:
        """Lemmas reached through an inflected form spelled `key`. Returns whether anything was added."""
        found = False
        for lid, kind, form, tag, fz, lkey, lz in self.con.execute(
                "SELECT f.lemma_id, f.kind, f.form, f.tag, f.zipf, t.key, t.zipf FROM form f "
                "JOIN term t ON t.id = f.lemma_id WHERE f.fkey = ?", (key,)):
            if kind == K_FORM:
                tier = EXACT - 10 + shift
                if tag and ("imperative" in tag or ("genitive" in tag and "plural" in tag)):
                    tier -= 40
                if refl is not None and (REFLEXIVE & set(lkey.split())) != refl:
                    tier -= 40  # 'vrátil jsem se' wants vrátit se more than vrátit
                add(lid, tier, label or (f"tvar „{form}“" if form else "tvar"), form, min(fz, lz + 1.5))
            elif kind == K_CORE:
                add(lid, ALIAS + shift, label or ("bez se/si" if REFLEXIVE & set(lkey.split()) else "zkráceně"))
            else:
                if form and form.endswith(".") and len(key) <= 2:
                    continue  # 'ž.' is not what someone typing 'z' wants
                add(lid, ALIAS - 10 + shift, label or "varianta")
            found = True
        return found

    def _exact(self, key: str, add, shift: float = 0, label: str | None = None) -> bool:
        found = False
        for tid, text, flags, nw, zipf in self.con.execute(
                "SELECT id, text, flags, nwords, zipf FROM term WHERE key = ?", (key,)):
            if flags & F_INFLECTED and nw == 1:
                add(tid, INFLECTED + shift, label or "tvar", text)
            else:
                # A rare exact word should not beat a frequent form of another word (sel vs šel).
                add(tid, EXACT - 40 * max(0.0, 3.0 - zipf) - (60 if flags & F_INFLECTED else 0) + shift, label, text)
            found = True
        return found

    # --- search ----------------------------------------------------------------------------------
    def search(self, raw: str, limit: int = 14) -> list[Hit]:
        q = norm(raw)
        if not q:
            return []
        toks = q.split()
        # Someone who types diacritics anywhere in the query means every word as spelled ('děkuji moc' is not 'moč').
        surf = surface_tokens(raw)
        if len(surf) != len(toks) or not has_non_ascii_letter(raw):
            surf = [None] * len(toks)
        partial = not raw.endswith(" ")
        # term id -> (tier, label, surface, zipf): surface is the spelling that matched; zipf, if set,
        # is the frequency to rank by (that of the typed form)
        cand: dict[int, tuple] = {}

        def add(tid, tier, label=None, surface=None, zipf=None):
            old = cand.get(tid)
            if old is None or tier > old[0]:
                cand[tid] = (tier, label, surface, zipf)

        con = self.con
        # 1.-3. the whole query as a word, a form of a word, or an alias
        self._exact(q, add)
        self._forms(q, add)
        strong = bool(cand)

        # 3a. 'zeptal se', 'vrátil jsem se', 'zapomněl jsem': one content word plus se/si/auxiliary
        if not strong and len(toks) >= 2:
            content = [t for t in toks if t not in REFLEXIVE and t not in AUXILIARY and t not in CLITICS]
            if len(content) == 1:
                q_refl = REFLEXIVE & set(toks)
                t = content[0]
                for refl in sorted(q_refl) or [None]:
                    if refl:
                        self._exact(f"{t} {refl}", add, -10)
                self._exact(t, add, -30 - (40 if q_refl else 0), "bez se/si" if q_refl else None)
                self._forms(t, add, refl=q_refl)
                strong = bool(cand)

        # 3b. negation: 'nesnáším' -> nesnášet (a word of its own) before snášet
        if not strong and len(toks) == 1:
            for pre in ("nej", "ne"):
                rest = q[len(pre):]
                if not (q.startswith(pre) and len(rest) >= 3):
                    continue
                for (tid,) in con.execute("SELECT id FROM term WHERE key = ?", (rest,)):
                    add(tid, NEG, "zápor")
                for lid, lkey in con.execute(
                        "SELECT f.lemma_id, t.key FROM form f JOIN term t ON t.id = f.lemma_id "
                        "WHERE f.fkey = ? AND f.kind = 0", (rest,)):
                    add(lid, NEG - 20, "zápor")
                    for (nid,) in con.execute("SELECT id FROM term WHERE key = ? AND lang = 0", (pre + lkey,)):
                        add(nid, EXACT - 10, f"tvar „{raw.strip()}“", raw.strip())
                if cand:
                    strong = True
                    break

        # 3c. aspect partner of a Czech verb (zapomínat -> zapomenout), never above the hit it came from
        if strong and len(toks) == 1:
            src = {tid: c for tid, c in cand.items() if c[0] >= 900}
            if src:
                marks = ",".join("?" * len(src))
                for aid, bid in con.execute(f"SELECT a_id, b_id FROM aspect WHERE a_id IN ({marks})", list(src)):
                    tier, _l, surface, _z = src[aid]
                    add(bid, min(ASPECT, tier - 30), "vid. protějšek", surface)

        # 3d. 'the dog', 'a dog', 'to run': the word without the article / infinitive 'to'
        if not strong and len(toks) >= 2 and toks[0] in LEADING:
            rest = " ".join(toks[1:])
            self._exact(rest, add, -20, "bez členu" if toks[0] != "to" else None)
            self._forms(rest, add, -20)

        # 4. prefix: the last word is still being typed
        if partial:
            for tid, key in con.execute(
                    "SELECT id, key FROM term WHERE key > ? AND key < ? ORDER BY zipf DESC LIMIT 30",
                    (q, q + "\U0010ffff")):
                add(tid, PREFIX - 4 * min(15, len(key) - len(q)))
            if len(toks) == 1 and len(q) >= 4:  # 'zapomn' -> zapomenout via 'zapomněl'
                for lid, form, fkey, fz, lz in con.execute(
                        "SELECT f.lemma_id, f.form, f.fkey, f.zipf, t.zipf FROM form f JOIN term t ON t.id = f.lemma_id "
                        "WHERE f.fkey > ? AND f.fkey < ? AND f.kind = 0 ORDER BY f.zipf DESC LIMIT 15",
                        (q, q + "\U0010ffff")):
                    add(lid, PREFIX - 4 * min(15, len(fkey) - len(q)), f"tvar „{form}…“", None, min(fz, lz + 1.5))

        # 5. phrases containing the words (with lemma alternatives per word)
        if len(q) >= 2:
            # While a single unknown word is being typed, completions come before phrases containing it.
            cap = PREFIX - 30 if (len(toks) == 1 and partial and not strong) else None
            self._phrases(toks, surf, partial, add, cap=cap)
            # 'figure it out', 'nech to být': the object pronoun is optional
            pron = [i for i, t in enumerate(toks) if i > 0 and t in PRONOUN_SLOTS]
            if pron and not any(c[0] >= WHOLE - 10 for c in cand.values()):
                keep = [i for i in range(len(toks)) if i not in pron]
                if len(core_tokens([toks[i] for i in keep])) >= 2:
                    self._phrases([toks[i] for i in keep], [surf[i] for i in keep], partial, add)

        # 6. one-typo matches for a single word that found nothing better
        if not strong and len(toks) == 1 and 4 <= len(q) <= 20:
            keys = [q, *deletions(q)]
            marks = ",".join("?" * len(keys))
            for tid, key in con.execute(
                    f"SELECT DISTINCT t.id, t.key FROM del1 d JOIN term t ON t.id = d.term_id WHERE d.dkey IN ({marks})",
                    keys):
                if key != q:
                    dist = osa_distance(q, key, 1)
                    if dist <= 1:
                        add(tid, FUZZY - 20 * dist, "překlep?")

        # 7. a sentence: look for phrases inside it ('dal si pozor na psa' -> 'dát si pozor', 'mám tě rád' -> 'mít rád')
        if len(toks) >= 2 and not strong:
            self._windows(toks, surf, add, cand)
        return self._rank(raw, cand, limit)

    def _windows(self, toks, surf, add, cand) -> None:
        keep = [i for i, t in enumerate(toks) if t not in AUXILIARY and t not in CLITICS]
        words, wsurf = [toks[i] for i in keep], [surf[i] for i in keep]
        content = [t for t in words if t not in FUNCTION_WORDS and t not in STOPWORDS]
        covered = set()
        if len(toks) >= 3 and not any(c[0] >= WHOLE - 10 for c in cand.values()):
            for size in range(min(len(words) - (len(words) == len(toks)), 5), 1, -1):
                found = False
                for start in range(0, len(words) - size + 1):
                    window = words[start:start + size]
                    wcontent = [t for t in window if t not in FUNCTION_WORDS]
                    ok = (len(wcontent) >= 2 and window[0] not in FUNCTION_WORDS) or (size == 2 and len(wcontent) == 1)
                    if not ok:
                        continue
                    before = len(cand)
                    # The phrase must have as many words as the window ('tip of' is not 'tip of the iceberg').
                    self._phrases(window, wsurf[start:start + size], False, add,
                                  penalty=60 * (len(words) - size), wanted=WHOLE - 10)
                    if len(cand) > before:
                        found = True
                        covered.update(wcontent)
                if found:
                    break
        # The query's own words: right below exact-length phrases if there is a single one ('s kamarádem'),
        # otherwise at the end, for the words no phrase covered ('jít na nákup' -> nákup)
        best = max((c[0] for c in cand.values()), default=0)
        tier = WHOLE - 20 if (len(content) == 1 and best < WHOLE) else WORD_FALLBACK
        for t, s in zip(words, wsurf):
            if t not in content or t in covered or len(t) < 3:
                continue
            strict = s is not None
            for tid, text, flags in self.con.execute("SELECT id, text, flags FROM term WHERE key = ?", (t,)):
                if flags & F_ACRONYM:
                    continue  # 'psa' in a sentence is the dog, not PSA
                if not strict or text.lower() == s:  # 'moc' typed with care is not 'moč'
                    add(tid, tier, "slovo z dotazu", text)
            for lid, form, tag, fz, lz in self.con.execute(
                    "SELECT f.lemma_id, f.form, f.tag, f.zipf, t.zipf FROM form f JOIN term t ON t.id = f.lemma_id "
                    "WHERE f.fkey = ? AND f.kind = 0", (t,)):
                if strict and form and form.lower() != s:
                    continue
                penalty = 40 if tag and ("imperative" in tag or ("genitive" in tag and "plural" in tag)) else 0
                add(lid, tier - 10 - penalty, "slovo z dotazu", form, min(fz, lz + 1.5))

    def _phrases(self, toks, surf, partial: bool, add, penalty: float = 0, wanted: float = 0, cap=None) -> None:
        """Add phrases containing all words of toks. Hits below the tier `wanted` (before penalty) are skipped."""
        q_refl = REFLEXIVE & set(toks)
        idx = [i for i, t in enumerate(toks) if t not in REFLEXIVE and t not in PLACEHOLDERS] or list(range(len(toks)))
        q_core = [toks[i] for i in idx]
        alts, aspect_only, groups = [], [], []
        for n, i in enumerate(idx):
            t = toks[i]
            last = n == len(idx) - 1
            main = {t} | self._lemma_keys(t, surf[i] if surf else None)
            if len(q_core) >= 2:
                main |= set(PRONOUN_SLOTS.get(t, ()))
            asp = self._aspect_keys(main) if len(t) >= 3 and t not in FUNCTION_WORDS else set()
            a = sorted(main, key=lambda x: (x != t, x))[:8] + sorted(asp)[:4]
            alts.append(set(a))
            aspect_only.append(asp)
            terms = [f'"{x}"' for x in a]
            if last and partial:
                terms[0] = f'"{t}"*'
            groups.append(terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")")
        if not groups:
            return
        rows = self.con.execute(
            "SELECT f.rowid, f.lem, t.key, t.nwords, t.flags FROM phrase_fts f JOIN term t ON t.id = f.rowid "
            "WHERE phrase_fts MATCH ? ORDER BY f.rowid LIMIT 60", (" AND ".join(groups),)).fetchall()
        n_q = len(q_core)
        for tid, lem, key, nw, flags in rows:
            lemset = set(lem.split())
            ttoks = key.split()
            if all(a & lemset for a in alts):
                t_core = [t for t in ttoks if t not in REFLEXIVE and t not in PLACEHOLDERS] or ttoks
                tier = WHOLE - 25 * abs(len(t_core) - n_q)
                t_refl = REFLEXIVE & set(ttoks)
                if q_refl and q_refl == t_refl:
                    tier += 15
                elif t_refl and not q_refl:
                    tier -= 15
                elif q_refl and not t_refl:
                    tier -= 25  # 'co se stalo' is not 'stůj co stůj'
                pos = [next((j for j, tt in enumerate(ttoks) if tt in a), -1) for a in alts]
                if all(p >= 0 for p in pos) and pos == sorted(pos):
                    tier += 10
                for a, asp in zip(alts, aspect_only):
                    if asp and not ((a - asp) & lemset):
                        tier -= 20  # matched only through the other aspect (umývat -> umýt nádobí)
            else:
                tier = PHRASE_PREFIX - 20 * nw
            if flags & F_INFLECTED:
                tier -= 60
            if cap is not None:
                tier = min(tier, cap)
            if tier >= wanted:
                add(tid, tier - penalty, "fráze z dotazu" if penalty else None)

    # --- ranking -------------------------------------------------------------------------------
    def _rank(self, raw: str, cand: dict, limit: int) -> list[Hit]:
        if not cand:
            return []
        ids = list(cand)
        marks = ",".join("?" * len(ids))
        rows = self.con.execute(
            f"SELECT id, lang, text, key, nwords, zipf, flags, pos FROM term WHERE id IN ({marks})", ids).fetchall()
        raw_s = raw.strip()
        raw_l = raw_s.lower()
        czech_input = has_czech_chars(raw_s)
        typed_diacritics = has_non_ascii_letter(raw_s)
        en_cue, cs_cue = _cues(norm(raw_s))
        is_upper = raw_s.isupper() and len(raw_s) > 1
        is_cap = raw_s[:1].isupper()
        hits = []
        for tid, lang, text, key, nw, zipf, flags, pos in rows:
            tier, label, surface, zfreq = cand[tid]
            score = tier + 12 * min(zipf if zfreq is None else zfreq, MAX_ZIPF)
            if surface:
                if not typed_diacritics and has_non_ascii_letter(surface):
                    score -= 20  # 'mice' is the English word before Czech 'míče'
                elif typed_diacritics and _accent_mismatch(raw_l, surface.lower()):
                    score -= 80  # 'drahý' typed with care is not 'dráhy'
            exact_text = text == raw_s if flags & (F_PROPER | F_ACRONYM) else text.lower() == raw_l
            if exact_text or (typed_diacritics and (surface or "").lower() == raw_l):
                score += 40  # spelled exactly as typed: 'ho' (him) before EN 'ho', 'take' before 'také'
            if czech_input:
                score += 150 if lang == CS else -150
            elif not (lang == EN and exact_text):
                score += 30 * ((en_cue if lang == EN else cs_cue) - (cs_cue if lang == EN else en_cue))
            if flags & F_ACRONYM and not is_upper:
                score -= 150
            if flags & F_PROPER and not is_cap:
                score -= 80
            if flags & F_VULGAR:
                score -= 60
            hits.append(Hit(tid, LANG[lang], text, key, nw, zipf, flags, pos, tier, label, score))
        hits.sort(key=lambda h: (-h.score, h.term_id))
        hits = hits[:limit]
        for h in hits:
            h.translations = self.translations(h)
        return [h for h in hits if h.translations]

    def translations(self, h: Hit, n: int = 6) -> list[tuple[str, str | None]]:
        """Best translations first: not vulgar, not via an inflected form, names only for names, then by
        agreement between sources and frequency."""
        mine, other = ("cs_id", "en_id") if h.lang == "cs" else ("en_id", "cs_id")
        plain_head = not h.flags & (F_PROPER | F_ACRONYM)
        rows = self.con.execute(
            f"SELECT t.text, p.note, p.flags FROM pair p JOIN term t ON t.id = p.{other} WHERE p.{mine} = ? "
            f"ORDER BY (p.flags & {P_VULGAR}) != 0, (p.flags & {P_INFLECTED}) != 0, "
            f"(? AND (t.flags & {F_PROPER | F_ACRONYM}) != 0), p.weight + 0.5 * t.zipf DESC LIMIT ?",
            (h.term_id, plain_head, n)).fetchall()
        return [(text, _with_vulgar(note) if pf & P_VULGAR else note) for text, note, pf in rows]


def _with_vulgar(note: str | None) -> str:
    return f"{note}; vulg." if note else "vulg."


def _accent_mismatch(typed: str, surface: str) -> bool:
    """The user typed an accented letter where the candidate has a different one."""
    if len(typed) != len(surface):
        return False
    return any(ord(a) > 127 and a.isalpha() and a != b for a, b in zip(typed, surface))


def _cues(q: str) -> tuple[int, int]:
    """Weak spelling hints for the language of an ASCII query: (english, czech), each 0 or 1."""
    en = any(x in q for x in ("th", "wh", "w", "q", "x")) or q.endswith(("ing", "tion", "ness", "ly"))
    cs = q.endswith(("ovat", "eni", "ych", "ani", "ovy"))
    return int(en), int(cs and not en)
