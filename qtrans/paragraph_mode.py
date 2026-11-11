"""Paragraph mode: a longer text is translated whole. Para holds one query's versions (offline MT, online, LLM and
the user's edit of one of them); ParagraphMode (a mixin of the App) fetches them, renders the shown one, cycles
through them (Alt+←/→) and lets the user fix single words: from the dictionary (then the LLM repairs the grammar
of the sentence) or from the LLM's options that fit what the sentence means."""
import difflib
import logging
import time

from gi.repository import GLib

from qtrans import paragraph, wordpick
from qtrans.items import POS_LABEL
from qtrans.sentences import sentence_around

log = logging.getLogger("qt")

ORDER = ("mt", "online", "llm", "edit")  # the order Alt+←/→ goes through the versions
CTX_OPTIONS = 3  # LLM options per clicked word (each costs ~1.5 s on gemma3:4b)


class Para:
    """State of one paragraph query. versions: key (ORDER) -> (text, source label). 'edit' is the shown version
    with the user's replacements; edit_base is the version it started from and undo the texts before each step."""

    def __init__(self, raw: str):
        self.text = paragraph.clean_selection(raw)  # what is translated (and shown as the original)
        self.pair = paragraph.guess_direction(self.text)
        self.versions = {}
        self.current = None
        self.pending = set()
        self.note = ""
        self.stale = ""  # previous translation, shown dimmed until the new one arrives (no flashing)
        self.show_original = False
        self.born = time.monotonic()  # when the query last changed (debounce for the automatic LLM pass)
        self.auto_llm = False  # the running LLM request was started automatically (fails silently)
        self.edit_base = None
        self.undo = []  # texts of the edit version before each replacement step, for Ctrl+Z
        self.fixing = 0  # running grammar repairs by the LLM after a dictionary replacement
        self.src_hits = {}  # source word -> dictionary hits (cache for the word picker)

    @property
    def src(self):
        return self.pair.split("-")[0]

    @property
    def tgt(self):
        return self.pair.split("-")[1]

    def shown(self) -> str:
        return self.versions[self.current][0] if self.current else ""

    def keys(self) -> list[str]:
        return [k for k in ORDER if k in self.versions]

    def cycle(self, step: int) -> bool:
        """Show the previous (-1) or next (+1) version, round the end. False when there is no other one."""
        keys = self.keys()
        if self.current not in keys or len(keys) < 2:
            return False
        self.current = keys[(keys.index(self.current) + step) % len(keys)]
        self.show_original = False
        return True

    def replace(self, text: str):
        """One replacement step in the shown version: it goes into the edit version (a new one when the shown
        version is not the edit yet, replacing an older edit)."""
        if self.current != "edit":
            self.edit_base, self.undo = self.current, []
            source = self.versions[self.current][1]
        else:
            source = self.versions["edit"][1]
        self.undo.append(self.shown())
        self.versions["edit"] = (text, source)
        self.current = "edit"

    def amend(self, text: str):
        """Change the edit version within the last step (the grammar fix after a replacement)."""
        self.versions["edit"] = (text, self.versions["edit"][1])

    def undo_step(self) -> bool:
        """Ctrl+Z: undo the last replacement step of the shown edit version; the first one drops the edit."""
        if self.current != "edit" or self.show_original or not self.undo:
            return False
        before = self.undo.pop()
        if self.undo:
            self.amend(before)
        else:
            del self.versions["edit"]
            self.current = self.edit_base if self.edit_base in self.versions else (self.keys() or [None])[0]
        return True


class Pick:
    """The word the alternatives popover is open for: in version key, whose text was text."""

    def __init__(self, p: Para, text: str, start: int, end: int, alts: list[str], src_sentence: str):
        self.p, self.key, self.text, self.start, self.end, self.alts = p, p.current, text, start, end, alts
        self.src_sentence = src_sentence  # the source sentence the word was translated from (for the LLM)
        self.options = []  # the LLM's [{"word", "sentence", "why"}] for the sentence around the word
        self.sentence = sentence_around(text, start, end)

    def valid(self, p: Para) -> bool:
        return self.p is p and p.current == self.key and p.shown() == self.text and not p.show_original


class ParagraphMode:
    """Mixin of the App. Uses its win, ctl, cfg, searcher, _llm(), _copy() and _copy_when_ready."""

    def _init_paragraph(self):
        self._para = None
        self._llm_used_at = 0.0
        self._auto_llm_failed = set()  # paragraph texts the automatic LLM pass failed on: not retried
        self._pick = None  # Pick: the word the alternatives popover is open for
        self._ctx_busy = False  # an LLM options request runs (ollama answers one at a time)
        self._ctx_next = None  # (pick, word) clicked meanwhile: asked for when the running one ends

    def _run_paragraph(self, q: str):
        old = self._para
        p = self._para = Para(q)
        self._pick = None
        self.win.hide_word_pop()  # the dimmed stale text may be the same string: show_paragraph would keep it
        if old is not None:
            p.stale = old.shown() or old.stale
        if self.ctl.schedule_mt(p.text, [p.pair], self._para_mt_done):
            p.pending.add("mt")
        else:
            p.note = "offline překlad není k dispozici — Alt+L online, Alt+E LLM"
        self._render_para()

    def _render_para(self):
        p = self._para
        if p is None:
            return
        keys = p.keys()
        if p.show_original:
            text, source, dim = p.text, f"originál · {p.src}", False
        elif p.current:
            (text, source), dim = p.versions[p.current], False
            if p.current == "edit":
                source += " · upraveno"
            if len(keys) > 1:
                source += f" · {keys.index(p.current) + 1}/{len(keys)}"
        elif p.stale and p.pending:
            text, source, dim = p.stale, "", True
        else:
            text, source, dim = ("Překládám…" if p.pending else ""), "", True
        self.win.show_paragraph(text, source, p.note, bool(p.pending or p.fixing), dim, p.show_original)
        self.win.set_back(len(keys) > 1)

    def _para_mt_done(self, res):
        p = self._para
        if p is None:
            return
        if isinstance(res, Exception) or not res or not res[0][1]:
            self._para_failed(p, "mt", f"strojový překlad selhal: {res}" if isinstance(res, Exception)
                              else "strojový překlad nic nevrátil")
            return
        pair, text = res[0]
        self._para_result(p, "mt", text, f"strojový překlad · {_arrow(pair)}")
        self._para_auto_llm(p)

    def _para_auto_llm(self, p: Para):
        """cs->en only: once the offline MT is shown and typing has stopped (~600 ms), refine it with the local LLM.
        Not for en->cs (the small model writes worse Czech than the MT)."""
        if (p is not self._para or p.pair != "cs-en" or not self.cfg["paragraph"]["auto_llm"]
                or "llm" in p.versions or "llm" in p.pending or p.text in self._auto_llm_failed):
            return
        gen = self.ctl.gen  # bumped by a newer query or by hiding the window

        def fire():
            if gen == self.ctl.gen and p is self._para and p.current == "mt":
                self._para_llm(auto=True)
            return False

        delay = max(0, int(600 - (time.monotonic() - p.born) * 1000))
        GLib.timeout_add(delay, fire)

    def _para_result(self, p: Para, key: str, text: str, source: str):
        if p is not self._para:
            return
        p.pending.discard(key)
        p.versions[key] = (text, source)
        # A late offline result must not replace the better online / LLM one, nor the automatic LLM pass
        # a version the user asked for (Alt+L) or a translation the user has fixed words in.
        auto = key == "llm" and p.auto_llm
        if key == "llm":
            p.auto_llm = False
        p.note = ""
        if (key != "mt" and not auto) or p.current in (None, "mt"):
            p.current = key
            p.show_original = False
        elif auto and p.current == "edit":
            p.note = "LLM verze: Alt+E"
        self._render_para()
        if self._copy_when_ready:
            self.copy_paragraph()

    def _para_failed(self, p: Para, key: str, note: str):
        if p is not self._para:
            return
        p.pending.discard(key)
        if key == "llm" and p.auto_llm:  # automatic refinement: keep the MT text, no error, no retry
            self._auto_llm_failed.add(p.text)
            p.auto_llm = False
            if p.note == "zpřesňuji…":
                p.note = ""
            note = ""
        else:
            p.note = note
        if not p.pending:  # Enter pressed while waiting: nothing will come, so a later Alt+L/E must not copy and close
            self._copy_when_ready = False
        log.info("paragraph %s failed: %s", key, note)
        self._render_para()

    def _para_online(self):
        p = self._para
        if p is None or "online" in p.pending:
            return
        if not self.cfg["online"]["enabled"]:
            p.note = "online překlad je vypnutý ([online] enabled v config.toml)"
            self._render_para()
            return
        from qtrans import online
        text, src, tgt = p.text, p.src, p.tgt
        timeout = max(self.cfg["online"]["timeout"], 15)  # a long paragraph takes LINDAT a while
        p.pending.add("online")
        p.note = "ptám se LINDATu…"
        self._render_para()

        def done(res):
            if isinstance(res, Exception) or not res:
                self._para_failed(p, "online", f"online nedostupné: {res}"[:120])
            else:
                self._para_result(p, "online", res, f"online · ÚFAL MFF UK · {_arrow(p.pair)}")

        self.ctl.run_io(lambda: online.translate(text, src, tgt, timeout=timeout), done)
        log.info("paragraph online %s, %d chars", p.pair, len(text))

    def _para_llm(self, auto: bool = False):
        p = self._para
        if p is None:
            return
        if not auto and "llm" in p.versions and p.current != "llm":  # kept back after a word replacement
            p.current, p.show_original, p.note = "llm", False, ""
            self._render_para()
            return
        if "llm" in p.pending:
            if not auto and p.auto_llm:  # Alt+E while the automatic pass runs: it now counts as asked for
                p.auto_llm = False
                p.note = "LLM překládá…"
                self._render_para()
            return
        llm = self._llm()
        text, src, tgt = p.text, p.src, p.tgt
        cold = time.monotonic() - self._llm_used_at > 300  # ollama has probably unloaded the model
        p.pending.add("llm")
        p.auto_llm = auto
        p.note = "zpřesňuji…" if auto else "načítám model…" if cold else "LLM překládá…"
        self._render_para()

        def done(res):
            if isinstance(res, Exception) or not res:
                self._para_failed(p, "llm", f"LLM nedostupné: {res}"[:120] if isinstance(res, Exception)
                                  else "LLM nic nevrátil")
            else:
                self._llm_used_at = time.monotonic()
                self._para_result(p, "llm", res, f"LLM · {self.cfg['llm']['model']}")

        self.ctl.run_io(lambda: llm.translate(text, src, tgt), done)
        log.info("paragraph llm%s %s, %d chars", " (auto)" if auto else "", p.pair, len(text))

    # --- versions (Alt+←/→) --------------------------------------------------------------------------
    def cycle_version(self, step: int) -> bool:
        p = self._para
        if p is None or not p.cycle(step):
            return False
        self._pick = None
        self.win.hide_word_pop()
        self._render_para()
        log.info("version %s (%d/%d)", p.current, p.keys().index(p.current) + 1, len(p.keys()))
        return True

    # --- word picker -----------------------------------------------------------------------------------
    def pick_word(self, i: int):
        """A click on character i of the shown paragraph text: alternatives for the word there (translation only):
        the dictionary's at once, the LLM's (what fits the sentence) when they come."""
        p, win = self._para, self.win
        text = p.shown() if p is not None and not p.show_original else ""
        span = wordpick.word_at(text, i) if text and win.para_text.get_text() == text else None
        if span is None:
            self._pick = None
            win.hide_word_pop()
            return
        start, end = span
        word = text[start:end]
        groups, alts, spans, src_spans = [], [], [], []
        source = win.get_text()
        if self.searcher is not None:
            t = time.perf_counter()
            found = wordpick.alternatives(self.searcher, word, start / len(text), p.text, p.src, p.tgt, p.src_hits)
            for kind, h, token, words in found:
                if kind == "syn":
                    label = "podobná slova"
                elif token.casefold() != h.text.casefold():
                    label = f"z „{token}“"
                else:
                    label = POS_LABEL.get(h.pos, h.pos or "")
                groups.append((h.text, label, words, h.lang.upper()))
                alts += words
                if kind == "src":
                    span = wordpick.find_token(source, token, start / len(text))
                    if span and span not in spans:
                        spans.append(span)
                    span = wordpick.find_token(p.text, token, start / len(text))
                    if span:
                        src_spans.append(span)
            # User text only at DEBUG: the INFO log says how much, not what.
            log.info("pick: %d-char word, %d alternatives in %.0f ms", len(word), len(alts),
                     (time.perf_counter() - t) * 1000)
            log.debug("pick %r: %r", word, alts)
        pick = self._pick = Pick(p, text, start, end, alts, source_sentence(p.text, text, start, src_spans))
        ask = not wordpick.is_grammatical(word)
        win.show_word_pop(start, end, groups, self.pick_alt, context=ask)
        win.highlight_source(spans)
        if ask:
            self._ctx_alternatives(pick, word)

    def _ctx_alternatives(self, pick: Pick, word: str):
        """Ask the LLM for replacements that fit what the sentence means; dropped silently when it cannot.
        One request at a time, the latest click waits: words clicked through quickly do not queue up in ollama."""
        if self._ctx_busy:
            self._ctx_next = (pick, word)
            return
        self._ctx_busy = True
        p = pick.p
        s, e = pick.sentence
        translation = pick.text[s:e]
        llm = self._llm()
        t = time.perf_counter()
        gen = self.ctl.gen

        def finished(res):
            self._ctx_busy = False
            nxt, self._ctx_next = self._ctx_next, None
            if nxt is not None and nxt[0] is self._pick and nxt[0].valid(self._para):  # (not after closing)
                self._ctx_alternatives(*nxt)
            done(res)

        def done(res):
            if self._pick is not pick or gen != self.ctl.gen:  # another word, a replacement or a new text since
                return
            if not isinstance(res, Exception):  # a rewrite of more than the sentence is not an option
                res = [o for o in res if plausible_fix(translation, o["sentence"])]
            if isinstance(res, Exception) or not res:
                log.info("pick: no LLM options (%s)", type(res).__name__ if isinstance(res, Exception) else "none")
                self.win.set_word_pop_context(None, self.pick_ctx)
                return
            pick.options = res
            log.info("pick: %d LLM options in %.1f s", len(res), time.perf_counter() - t)
            self.win.set_word_pop_context([(o["word"], self._option_note(o, p.tgt), o["sentence"]) for o in res],
                                          self.pick_ctx)

        self.ctl.run_io(lambda: llm.alternatives_in_context(pick.src_sentence, translation, word, p.src, p.tgt,
                                                            n=CTX_OPTIONS), finished, always=True)

    def _option_note(self, o: dict, lang: str) -> str:
        # The reason is asked for in Czech, but the small model sometimes writes English: then none.
        why = o["why"] if paragraph.guess_direction(o["why"]) == "cs-en" else ""
        if self._known(o["word"], lang):
            return why
        return f"{why} · neověřeno" if why else "neověřeno"

    def _known(self, phrase: str, lang: str) -> bool:
        """Every longer word of phrase is a word or a word form the dictionary knows (the 4b model invents some)."""
        if self.searcher is None:
            return True
        from qtrans.normalize import norm
        con, lid = self.searcher.con, 0 if lang == "cs" else 1
        for w in norm(phrase).split():
            if len(w) <= 2:
                continue
            if not con.execute("SELECT 1 FROM term WHERE key = ? AND lang = ? UNION ALL SELECT 1 FROM form f "
                               "JOIN term t ON t.id = f.lemma_id WHERE f.fkey = ? AND t.lang = ? LIMIT 1",
                               (w, lid, w, lid)).fetchone():
                return False
        return True

    def pick_alt(self, n: int):
        """Replace the picked word with dictionary alternative n; then the LLM repairs the sentence's grammar."""
        pick, self._pick = self._pick, None
        self.win.hide_word_pop()
        p = self._para
        if pick is None or n >= len(pick.alts) or not pick.valid(p):
            return
        new = pick.alts[n]
        text = wordpick.replace_span(pick.text, pick.start, pick.end, new)
        p.replace(text)
        log.info("replaced a %d-char word with alternative %d (%d chars)", pick.end - pick.start, n, len(new))
        log.debug("replaced %r with %r", pick.text[pick.start:pick.end], new)
        self._render_para()
        self._fix_grammar(p, text, pick.start, pick.start + len(new), pick.src_sentence)

    def pick_ctx(self, n: int):
        """Replace the whole sentence around the picked word with the LLM's option n."""
        pick, self._pick = self._pick, None
        self.win.hide_word_pop()
        p = self._para
        if pick is None or n >= len(pick.options) or not pick.valid(p):
            return
        s, e = pick.sentence
        p.replace(pick.text[:s] + pick.options[n]["sentence"] + pick.text[e:])
        log.info("replaced a %d-char sentence with LLM option %d", e - s, n)
        self._render_para()

    def _fix_grammar(self, p: Para, text: str, start: int, end: int, src_sentence: str):
        """After a dictionary word went in (a base form), let the LLM fix the inflection around it, within the same
        undo step; only if the text is still as it was then."""
        s, e = sentence_around(text, start, end)
        sentence, word = text[s:e], text[start:end]
        step = len(p.undo)
        llm = self._llm()
        p.fixing += 1
        self._render_para()

        def done(res):
            p.fixing -= 1
            if p is not self._para:
                return
            if (not isinstance(res, Exception) and p.current == "edit" and p.shown() == text
                    and len(p.undo) == step and plausible_fix(sentence, res)):
                p.amend(text[:s] + res + text[e:])
                self._pick = None
                log.info("grammar fixed (%+d chars)", len(res) - len(sentence))
            else:
                log.info("grammar fix dropped: %s", type(res).__name__ if isinstance(res, Exception)
                         else "changed meanwhile" if p.shown() != text else "no light edit")
            self._render_para()

        self.ctl.run_io(lambda: llm.fix_sentence(src_sentence, sentence, word, p.src, p.tgt), done)

    def _debug_pickalt(self, n: int):
        if self._pick is not None and n < len(self.win.word_pop_buttons):
            self.win.word_pop_buttons[n].emit("clicked")

    def _debug_pickctx(self, n: int):
        if self._pick is not None and n < len(self.win.word_pop_ctx_buttons):
            self.win.word_pop_ctx_buttons[n].emit("clicked")

    def undo_replacement(self) -> bool:
        """Ctrl+Z: undo the last replacement step in the shown translation. False when there is none."""
        p = self._para
        if p is None or not p.undo_step():
            return False
        self._pick = None
        self._render_para()
        return True

    def toggle_original(self):
        p = self._para
        if p is not None:
            p.show_original = not p.show_original
            self._render_para()

    def copy_paragraph(self):
        """Copy the translation shown (also while the original is shown); wait for the first one if none yet."""
        p = self._para
        if p is None:
            return
        if p.current:
            self._copy(p.shown())
        elif p.pending:
            self._copy_when_ready = True


def plausible_fix(old: str, new) -> bool:
    """Is the LLM's repaired sentence a light edit of old (not a rewrite, an answer or an empty string)?"""
    if not isinstance(new, str) or not new.strip() or new == old:
        return False
    if len(new) > 1.5 * len(old) + 12:
        return False
    return difflib.SequenceMatcher(None, old, new).ratio() >= 0.6


def source_sentence(source: str, translation: str, start: int, spans: list) -> str:
    """The sentence of source that the translation's word at start comes from: around its source word (spans,
    found by the dictionary), else the sentence with the same number when both have as many, else the one at the
    same relative position."""
    if spans:
        s, e = sentence_around(source, *spans[0])
        return source[s:e]
    src, tgt = sentence_spans(source), sentence_spans(translation)
    k = next((k for k, (s, e) in enumerate(tgt) if s <= start < e), None)
    if k is not None and len(src) == len(tgt):
        s, e = src[k]
        return source[s:e]
    i = min(len(source) - 1, max(0, int(start / max(1, len(translation)) * len(source))))
    s, e = sentence_around(source, i, i + 1)
    return source[s:e] or source


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of every sentence of text, in order."""
    out, i = [], 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        s, e = sentence_around(text, i, i + 1)
        out.append((s, e))
        i = max(e, i + 1)
    return out


def _arrow(pair: str) -> str:
    return pair.replace("-", " → ")

