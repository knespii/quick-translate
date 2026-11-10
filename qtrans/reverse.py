"""Describe-a-word search ("mám to na jazyku"): find words whose dictionary definition matches a description.

Czech descriptions are matched against Czech Wiktionary definitions (lemmatized), English ones (or the MT
translation of a Czech description) against WordNet and English Wiktionary glosses. Results are fused by rank.
"""
import sqlite3

from qtrans.normalize import norm
from qtrans.search import Hit, LANG

STOP_EN = {"the", "a", "an", "of", "to", "for", "and", "or", "that", "which", "who", "is", "are", "be", "used",
           "use", "thing", "things", "something", "someone", "somebody", "with", "in", "on", "by", "it", "its", "from",
           "as", "at", "you", "your", "when", "what", "word", "kind", "type", "sort", "like", "very", "one", "some"}
STOP_CS = {"ta", "ten", "to", "ty", "ti", "vec", "veci", "na", "a", "i", "pro", "s", "se", "si", "z", "ze", "do", "v",
           "ve", "k", "ke", "o", "u", "je", "jsou", "ktery", "ktera", "ktere", "co", "kdo", "neco", "nekdo", "kdyz",
           "jak", "takovy", "takova", "takove", "slovo", "nejaky", "nejaka", "nejake", "jako", "nebo", "kterym",
           "kterou", "cim", "tim", "jsem", "jsi", "jak", "se", "rika", "tomu", "toho"}
K_RRF = 8


class Reverse:
    def __init__(self, con: sqlite3.Connection, searcher):
        self.con = con
        self.searcher = searcher
        self.available = bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE name IN ('def_en', 'def_cs')").fetchone())

    def _lemmas(self, token: str) -> set[str]:
        rows = self.con.execute(
            "SELECT t.key FROM form f JOIN term t ON t.id = f.lemma_id WHERE f.fkey = ? AND f.kind = 0 AND t.lang = 0",
            (token,))
        return {k for (k,) in rows if " " not in k}

    def _ranked(self, src: int, sql: str, expr: str, limit: int, groups: list[set[str]]) -> list[tuple[int, str]]:
        """BM25 candidates re-sorted by how many query words (any of their alternatives) the definition contains."""
        try:
            rows = self.con.execute(sql, (expr, limit * 3)).fetchall()
        except sqlite3.OperationalError:
            return []

        def matched(text: str) -> int:
            words = set(text.split())
            stems = {w[:5] for w in words}
            return sum(1 for g in groups if g & words or any(len(a) >= 5 and a[:5] in stems for a in g))

        rows = sorted(enumerate(rows), key=lambda r: (-matched(r[1][2]), r[0]))
        out = []
        for _rank, (did, gloss, _text) in rows[:limit]:
            for (tid,) in self.con.execute(
                    "SELECT term_id FROM def_target WHERE src = ? AND def_id = ? ORDER BY ord", (src, did)):
                out.append((tid, gloss))
        return out

    def search_cs(self, text: str, limit: int = 30) -> list[tuple[int, str]]:
        toks = [t for t in norm(text).split() if t not in STOP_CS and len(t) > 1]
        if not toks:
            return []
        groups = [set(sorted({t} | self._lemmas(t))[:6]) for t in toks]
        expr = "lem : (" + " OR ".join(f'"{a}"' for g in groups for a in sorted(g)) + ")"
        return self._ranked(1, "SELECT rowid, gloss, lem FROM def_cs WHERE def_cs MATCH ? ORDER BY bm25(def_cs, 0, 0, 1) "
                               "LIMIT ?", expr, limit, groups)

    def search_en(self, text: str, limit: int = 30) -> list[tuple[int, str]]:
        toks = [t for t in norm(text).split() if t not in STOP_EN and len(t) > 1]
        if not toks:
            return []
        expr = "gloss : (" + " OR ".join(f'"{t}"' for t in toks) + ")"
        return self._ranked(0, "SELECT rowid, gloss, lower(gloss) FROM def_en WHERE def_en MATCH ? "
                               "ORDER BY bm25(def_en, 0, 1) LIMIT ?", expr, limit, [{t} for t in toks])

    def fuse(self, lists: list[list[tuple[int, str]]], limit: int = 12) -> list[Hit]:
        """Reciprocal rank fusion of several ranked (term_id, gloss) lists into dictionary hits."""
        score, gloss = {}, {}
        for lst in lists:
            seen = set()
            for rank, (tid, g) in enumerate(lst):
                if tid in seen:
                    continue
                seen.add(tid)
                score[tid] = score.get(tid, 0) + 1 / (K_RRF + rank)
                gloss.setdefault(tid, g)
        best = sorted(score, key=lambda t: -score[t])[:limit * 2]
        if not best:
            return []
        marks = ",".join("?" * len(best))
        rows = {r[0]: r for r in self.con.execute(
            f"SELECT id, lang, text, key, nwords, zipf, flags, pos FROM term WHERE id IN ({marks})", best)}
        hits = []
        for tid in best:
            if tid not in rows:
                continue
            _id, lang, text, key, nw, zipf, flags, pos = rows[tid]
            h = Hit(tid, LANG[lang], text, key, nw, zipf, flags, pos, tier=0, label=gloss[tid][:70],
                    score=score[tid])
            h.translations = self.searcher.translations(h)
            if h.translations:
                hits.append(h)
            if len(hits) >= limit:
                break
        return hits
