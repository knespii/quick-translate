"""Restore Czech diacritics in text typed without them: 'jak se mas brouku' -> 'jak se máš brouku'.

Each ASCII word takes its most frequent Czech spelling from the undiac table (SYN2015 word forms plus the
dictionary, see dictbuild/undiac.py); a few cheap context rules fix the common ties (že/ze, vocative -u/-ů).
Words already written with diacritics, unknown words and English words stay as typed.
"""
import re
import sqlite3

from qtrans.normalize import FUNCTION_WORDS, norm
from qtrans.paragraph import guess_direction

_TOKEN = re.compile(r"[^\W\d_]+")
# Whitespace-separated chunks that are code, not prose: URLs, e-mails, paths, identifiers, 'mp3'.
_CODE = re.compile(r"://|@|www\.|[/\\_=<>{}|#~]|\d[^\W\d_]|[^\W\d_]\d")
_VOTE, _STRONG_VOTE = 1.0, 2.0  # zipf difference for a half / a full vote ('pet' is half Czech, 'mas' fully)
_ENGLISH_ALONE = 0.5  # restore_word: an English word this close to its Czech spelling stays ('take')
_ENGLISH_IN_CS = 1.5  # restore: in Czech text only a clearly English word stays ('sale' yes, 'cast' no)
_CACHE_MAX = 50_000

# 'ze' is the preposition before s/z/š/ž words and a few others ('ze školy', 'ze mě'), otherwise 'že'.
_ZE_BEFORE = frozenset("me mne dne dvou tri ctyr vsech vseho vsi vsemi".split())
# ...but these start a clause ('že zase', 'že ses'), and after these comes the conjunction ('a že sníh').
_ZE_CLAUSE = frozenset("ses sis sem snad stejne zase zitra zrovna zatim spis skutecne sice sam sama sami".split())
_ZE_AFTER = frozenset("a ale i nebo tak jen asi mozna".split())
# Genitive plural triggers: after them '-ů' stays ('pět dolarů'), elsewhere a tie means vocative ('brouku').
_GEN_PL = frozenset("nekolik mnoho hodne malo par spousta spoustu rada radu vetsina vetsinu kolik tolik vice vic mene "
                    "min dvaceti tisice tisicu stovky desitky miliony pet sest sedm osm devet deset jedenact dvanact "
                    "dvacet tricet ctyricet padesat sto tisic milion".split())
_KEEP_U_RING = frozenset({"domů"})  # 'jdu domů' is the adverb, not a genitive plural
_VOC_MARGIN = 1.0
_VOC_BEFORE = frozenset("pane pani slecno mily mila mili drahy draha milacku".split())  # 'pane Nováku'
_SENTENCE_END = (".", "!", "?", "…")
_GUESS_MIN = 5  # shorter unknown words are names, abbreviations or typos more often than Czech
_SUFFIX_LENGTHS = (5, 4, 3)  # longest ending first, as built by dictbuild/undiac.py


def _match_case(typed: str, word: str) -> str:
    """Copy the case of each typed letter onto the restored word (same length): 'AHOJ', 'Novaku' -> 'Nováku'."""
    return "".join(c.upper() if t.isupper() else c for t, c in zip(typed, word))


# Careful restoring keeps a word as typed when that exact spelling is a common Czech word itself (corpus zipf at
# least CAREFUL_PLAIN: 'byt', 'dal', 'domu') and the spelling with diacritics is less than CAREFUL_GAP more
# frequent — 'mas', 'sel', 'klice' are rare as typed and still become 'máš', 'šel', 'klíče' — or when the two
# best spellings are within CAREFUL_MARGIN of each other ('drahy': dráhy/drahý).
CAREFUL_PLAIN = 4.0
CAREFUL_GAP = 2.0
CAREFUL_MARGIN = 0.5

class Diacritizer:
    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.ok = bool(con.execute("SELECT 1 FROM sqlite_master WHERE name = 'undiac_suffix'").fetchone())
        self._suffixes = dict(con.execute("SELECT suffix, ending FROM undiac_suffix")) if self.ok else {}
        self._rows, self._cs, self._en, self._guesses = {}, {}, {}, {}

    # --- lookups ---------------------------------------------------------------------------------
    def _cached(self, cache: dict, key: str, compute):
        if key not in cache:
            if len(cache) > _CACHE_MAX:
                cache.clear()
            cache[key] = compute(key)
        return cache[key]

    def _row(self, key: str):
        """(word, zipf, margin, alt, plain) for a folded word whose best Czech spelling has diacritics, else None."""
        if not self.ok:
            return None
        return self._cached(self._rows, key, lambda k: self.con.execute(
            "SELECT word, zipf, margin, alt, plain FROM undiac WHERE key = ?", (k,)).fetchone())

    def _lang_zipf(self, key: str, lang: int) -> float:
        """Frequency of the most frequent single-word term or form of `lang` that folds to `key`."""
        row = self.con.execute(
            "SELECT max(z) FROM (SELECT max(zipf) z FROM term WHERE key = ?1 AND lang = ?2 AND nwords = 1 "
            "UNION ALL SELECT max(f.zipf) FROM form f JOIN term t ON t.id = f.lemma_id "
            "WHERE f.fkey = ?1 AND f.kind = 0 AND t.lang = ?2)", (key, lang)).fetchone()
        return row[0] or 0.0

    def _cs_zipf(self, key: str) -> float:
        def compute(k):
            row = self._row(k)
            return max(self._lang_zipf(k, 0), row[1] if row else 0.0)
        return self._cached(self._cs, key, compute)

    def _en_zipf(self, key: str) -> float:
        return self._cached(self._en, key, lambda k: self._lang_zipf(k, 1))

    def _guess(self, key: str) -> str:
        """A word neither dictionary nor corpus knows: 'ne' + a known word ('neprepise' -> 'nepřepíše'),
        else a regular ending ('kachnovitych' -> 'kachnovitých'); the key itself when nothing fits."""
        def compute(k):
            if len(k) < _GUESS_MIN or self._cs_zipf(k) or self._en_zipf(k):
                return k
            for prefix in ("nej", "ne"):
                row = self._row(k[len(prefix):]) if k.startswith(prefix) else None
                if row:
                    return prefix + row[0]
            for n in _SUFFIX_LENGTHS:
                ending = self._suffixes.get(k[-n:])
                if ending:
                    return k[:-n] + ending
            return k
        return self._cached(self._guesses, key, compute)

    # --- public API ------------------------------------------------------------------------------
    def restore_word(self, word: str) -> str:
        """One word with its diacritics back ('tobe' -> 'tobě'); as typed if it has any already, is unknown
        or looks English ('take' alone stays, 'také' needs a Czech sentence around it)."""
        if not word.isascii() or not word.isalpha():
            return word
        key = word.lower()
        row = self._row(key)
        if not row or self._en_zipf(key) >= row[1] - _ENGLISH_ALONE:
            return word
        return _match_case(word, row[0])

    def restore(self, text: str, check: bool = True, careful: bool = True) -> str:
        """The whole text with diacritics restored word by word; everything between words stays exactly.
        With check, text that does not look Czech comes back unchanged. Careful (the default, used by the app)
        leaves a word as typed when that spelling is a common word itself ('byt', 'dal', 'ze') or two spellings
        with diacritics are about as likely ('drahy': drahý/dráhy): a wrong guess is worse than none."""
        if not self.ok or (check and not self.looks_czech(text)):
            return text
        toks = self._tokens(text)
        out, pos = [], 0
        for i, (start, end, word, code) in enumerate(toks):
            out.append(text[pos:start])
            out.append(word if code else self._restore_in_context(text, toks, i, careful))
            pos = end
        out.append(text[pos:])
        return "".join(out)

    def looks_czech(self, text: str) -> bool:
        """Is this (ASCII or mixed) text Czech? Function words and diacritics (guess_direction) plus a vote of
        the words: Czech-only spellings vs common English words. One ambiguous word ('take', 'list') is not."""
        score = 0.5 if guess_direction(text) == "cs-en" else -0.5
        for _s, _e, word, code in self._tokens(text):
            if code:
                continue
            if not word.isascii():
                score += 1 if norm(word) != word.lower() else 0  # typed with Czech diacritics
                continue
            key = word.lower()
            diff = self._cs_zipf(key) - self._en_zipf(key)
            score += ((abs(diff) >= _VOTE) + (abs(diff) >= _STRONG_VOTE)) / 2 * (1 if diff > 0 else -1)
        return score > 0

    # --- internals -------------------------------------------------------------------------------
    @staticmethod
    def _tokens(text: str) -> list[tuple[int, int, str, bool]]:
        """(start, end, word, is_code) for every run of letters; is_code marks words inside URLs, paths etc."""
        out = []
        for chunk in re.finditer(r"\S+", text):
            code = bool(_CODE.search(chunk.group()))
            out += [(chunk.start() + m.start(), chunk.start() + m.end(), m.group(), code)
                    for m in _TOKEN.finditer(chunk.group())]
        return out

    def _restore_in_context(self, text: str, toks: list, i: int, careful: bool = False) -> str:
        start, end, word, _code = toks[i]
        if not word.isascii():
            return word
        key = word.lower()
        row = self._row(key)
        if not row:
            return _match_case(word, self._guess(key))
        best, zipf, margin, alt, plain = row
        if self._en_zipf(key) - self._cs_zipf(key) >= _ENGLISH_IN_CS:
            return word
        if careful and key not in ("ze", "te"):  # (those two have context rules below)
            if (plain >= CAREFUL_PLAIN and zipf - plain < CAREFUL_GAP) or (alt and alt != key and margin < CAREFUL_MARGIN):
                return word
        prev = norm(toks[i - 1][2]) if i > 0 else ""
        nxt = norm(toks[i + 1][2]) if i + 1 < len(toks) else ""
        before = text[:start].rstrip()
        if key == "ze":
            best = "ze" if self._is_preposition_ze(before, prev, nxt) else "že"
        elif key == "te":
            joined = i + 1 < len(toks) and not text[end:toks[i + 1][0]].strip()
            best = "té" if joined and self._feminine_oblique(nxt) else "tě"
        elif best.endswith("ů") and alt == best[:-1] + "u" and best not in _KEEP_U_RING:
            name = bool(before) and word[0].isupper() and not before.endswith(_SENTENCE_END)
            addressed = text[end:end + 1] in (",", "!")  # 'Nováku, pojď sem'
            if self._vocative(prev, margin, name or addressed):
                best = alt
        return _match_case(word, best)

    @staticmethod
    def _vocative(prev: str, margin: float, name: bool) -> bool:
        """A close '-ů'/'-u' tie, a name or a form of address, and nothing that wants a genitive plural before."""
        if prev in _GEN_PL or prev.endswith(("ych", "ich", "ech")):
            return False
        return margin < _VOC_MARGIN or prev in _VOC_BEFORE or name

    @staticmethod
    def _feminine_oblique(nxt: str) -> bool:
        """'té doby', 'té ženě', 'té nové': a noun or adjective in -y/-e/-i follows; 'miluju tě', 'tě rád' not."""
        return (len(nxt) > 2 and nxt.endswith(("y", "e", "i")) and not nxt.startswith("ne")  # 'tě nebaví'
                and nxt not in FUNCTION_WORDS and nxt not in _ZE_CLAUSE)

    @staticmethod
    def _is_preposition_ze(before: str, prev: str, nxt: str) -> bool:
        """'ze školy', 'ze mě' vs 'že se', ', že zítra' (a comma always comes before the conjunction)."""
        if before.endswith(",") or nxt in _ZE_CLAUSE:
            return False
        if nxt in _ZE_BEFORE or (len(nxt) > 2 and nxt[0] in "sz"):  # 'a ze zahrady': the preposition wins
            return True
        return False
