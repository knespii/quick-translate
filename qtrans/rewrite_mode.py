"""Rewrite mode (Super+R): the selected text rewritten in another tone by the local LLM, streamed as it comes; Enter
puts it in place of the selection (the GNOME extension pastes it into the window the text came from).

A mixin of the App, as paragraph_mode.py: the state of one text (Rewrite) and the actions on it.
"""
import logging
import threading
import time

from gi.repository import GLib

from qtrans import paragraph, rewrite

log = logging.getLogger("qt")

DEBOUNCE_MS = 700  # typing in the input: rewrite once it pauses
EMPTY_HINT = "Označ text v jakékoli aplikaci a stiskni Super+R, nebo ho sem napiš."


class Rewrite:
    """One text: its versions per tone (finished, cleaned), what is being written now and where it stands."""

    def __init__(self, text: str, raw: str = ""):
        self.text = text
        self.raw = raw or text  # as selected: its leading and trailing whitespace goes back with the rewrite
        self.lang = paragraph.guess_direction(text).split("-")[0]
        self.versions = {}  # tone -> (text, note)
        self.partial = {}   # tone -> text so far (while streaming)
        self.pending = None  # tone being written
        self.cancel = None  # threading.Event of the running request
        self.error = ""
        self.note = ""
        self.show_original = False


class RewriteMode:
    def _init_rewrite(self):
        self.rewriting = False  # the popup is in rewrite mode (Super+R) until it hides
        self._rw = None
        self._rw_tone = rewrite.load_last_tone()
        self._rw_timer = 0
        self._rw_accept = None  # Enter pressed before the rewrite was finished: (paste,)

    # --- opening ------------------------------------------------------------------------------------------
    def start_rewrite(self):
        """Super+R: open the popup in rewrite mode (the selected text comes as the prefill); pressed again
        while it is open in rewrite mode, close it."""
        if not self.cfg["rewrite"]["enabled"]:
            log.info("rewrite: off in the config")
            return
        w = self.win
        if w.get_visible() and w.is_active() and self.rewriting and not self._closing:
            self.hide_popup()
            return
        if self._closing:
            self._finish_hide()  # (now: it resets the mode, so after setting it would undo it)
        if not self.rewriting:  # from translating: its pending results must not land in the rewrite
            self.ctl.bump()
            self._copy_when_ready = False
            self._para = None
            self._reset_words()
        self.rewriting = True
        w.set_rewrite(True, self._rw_tone)
        log.info("rewrite mode")
        if w.get_visible():
            self.run_query(w.get_text())  # (already open for translating: rewrite what is there)
            if not w.is_active():
                self.toggle()
            return
        self._last_toggle = time.monotonic()  # (as toggle does: a key repeat must not close it again)
        self.show_popup()
        self.run_query(w.get_text())

    def _end_rewrite(self):
        """The popup hid: the next opening translates again."""
        self._rw_cancel()
        self._rw = None
        self._rw_accept = None
        if self.rewriting:
            self.rewriting = False
            self.win.set_rewrite(False, self._rw_tone)

    # --- the text ---------------------------------------------------------------------------------------------
    def _run_rewrite(self, raw: str):
        """A new text in the input: rewrite it once typing pauses (a prefilled text at once)."""
        text = raw.strip()
        if self._rw is not None and self._rw.text == text:
            self._render_rewrite()
            return
        self._rw_accept = None  # an Enter given for the old text must not paste a new one unseen
        self._rw_cancel()
        self._rw = Rewrite(text, raw) if text else None
        self._render_rewrite()
        if self._rw is None:
            return
        limit = self.cfg["rewrite"]["max_chars"]
        if len(text) > limit:
            self._rw.error = f"Text je moc dlouhý na přepsání ({len(text)} znaků, nejvýš {limit})."
            self._render_rewrite()
            return
        delay = 0 if not self._typed_since_open() else DEBOUNCE_MS
        self._rw_timer = GLib.timeout_add(delay, self._rw_fire)

    def _rw_fire(self):
        self._rw_timer = 0
        self._rw_generate()
        return GLib.SOURCE_REMOVE

    def _rw_cancel(self):
        if self._rw_timer:
            GLib.source_remove(self._rw_timer)
            self._rw_timer = 0
        r = self._rw
        if r is not None and r.cancel is not None:
            r.cancel.set()
            r.cancel = None
            r.partial.pop(r.pending, None)
            r.pending = None

    def _rw_generate(self):
        r, tone = self._rw, self._rw_tone
        if r is None or r.error or tone in r.versions or r.pending == tone:
            self._render_rewrite()
            return
        self._rw_cancel()
        llm = self._rewrite_llm()
        cancel = threading.Event()
        r.pending, r.cancel = tone, cancel
        r.partial[tone] = ""
        gen = self.ctl.gen
        cold = time.monotonic() - self._llm_used_at > 300
        self._llm_used_at = time.monotonic()
        r.note = "načítám model…" if cold else ""
        text, lang = r.text, r.lang

        def cancelled():
            return cancel.is_set() or gen != self.ctl.gen

        latest = [None]  # the newest text from the stream; one update of the view at a time

        def on_text(so_far):
            first = latest[0] is None
            latest[0] = so_far
            if first:
                GLib.idle_add(self._rw_partial, r, tone, cancel, latest)

        def work():
            return llm.rewrite_stream(rewrite.messages(text, tone, lang), rewrite.max_tokens(text), on_text,
                                      cancelled, temperature=rewrite.temperature(tone))

        def done(res):
            self._rw_done(r, tone, cancel, res)

        log.info("rewrite %s %s, %d chars", tone, lang, len(text))
        self.ctl.run_io(work, done)
        self._render_rewrite()

    def _rw_partial(self, r, tone, cancel, latest):
        so_far, latest[0] = latest[0], None
        if r is self._rw and r.cancel is cancel and not cancel.is_set() and so_far is not None:
            r.partial[tone] = so_far
            if r.note == "načítám model…":
                r.note = ""
            if tone == self._rw_tone:
                self._render_rewrite()
        return GLib.SOURCE_REMOVE

    def _rw_done(self, r, tone, cancel, res):
        if r is not self._rw or cancel.is_set():
            return
        r.pending, r.cancel = None, None
        r.partial.pop(tone, None)
        if isinstance(res, Exception) or not (res or "").strip():
            r.error = f"Přepis se nepovedl: {res}" if isinstance(res, Exception) else "Model nic nevrátil."
            self._rw_accept = None
            log.info("rewrite %s failed: %s", tone, res)
            self._render_rewrite()
            return
        out = rewrite.clean_output(res, r.text)
        notes = []
        if tone == "grammar" and self._diacritizer is not None:
            out, kept = rewrite.guard_corrections(r.text, out, lambda w: self._diacritizer.is_word(w, r.lang))
            if kept:
                notes.append(f"vráceno {kept} zbytečných změn")
        lost = rewrite.changed_facts(r.text, out)
        if lost:
            notes.append("⚠ zkontroluj: " + ", ".join(lost[:4]))
        r.versions[tone] = (out, " · ".join(notes))
        r.error = ""
        log.info("rewrite %s done, %d chars%s", tone, len(out), f" ({notes})" if notes else "")
        self._render_rewrite()
        if self._rw_accept is not None and tone == self._rw_tone:
            paste = self._rw_accept
            self._rw_accept = None
            self._rw_replace(out, paste)

    def _rewrite_llm(self):
        llm = self._llm()
        model = self.cfg["rewrite"]["model"]
        if model and model != llm.model:
            from qtrans.llm import LLM
            c = self.cfg["llm"]
            if getattr(self, "_rw_llm", None) is None or self._rw_llm.model != model:
                self._rw_llm = LLM(c["url"], model, keep_alive=c["keep_alive"], timeout=c["timeout"],
                                   num_ctx=self.cfg["rewrite"]["num_ctx"])
            return self._rw_llm
        return llm

    # --- tones ------------------------------------------------------------------------------------------------
    def set_rewrite_tone(self, key: str):
        if key not in rewrite.TONE_KEYS or not self.rewriting:
            return
        if key != self._rw_tone:
            self._rw_tone = key
            rewrite.save_last_tone(key)
            self._rw_accept = None
        self.win.set_tone(key)
        r = self._rw
        if r is not None:
            r.show_original = False
            if r.error and key not in r.versions and not r.error.startswith("Text je moc"):
                r.error = ""
            if self._rw_timer:  # (still waiting for typing to pause)
                self._render_rewrite()
                return
        self._rw_generate()

    def step_rewrite_tone(self, step: int):
        keys = rewrite.TONE_KEYS
        self.set_rewrite_tone(keys[(keys.index(self._rw_tone) + step) % len(keys)])

    def toggle_rewrite_original(self):
        if self._rw is not None:
            self._rw.show_original = not self._rw.show_original
            self._render_rewrite()

    # --- the result ---------------------------------------------------------------------------------------------
    def _render_rewrite(self):
        r, tone = self._rw, rewrite.tone(self._rw_tone)
        model = self._rewrite_model_name()
        if r is None:
            self.win.show_paragraph(EMPTY_HINT, "", "", False, True, False)
            return
        busy = r.pending is not None
        if r.show_original:
            self.win.show_paragraph(r.text, "originál", "", busy, False, True)
        elif tone.key in r.versions:
            text, note = r.versions[tone.key]
            self.win.show_paragraph(text, f"{tone.label} · {model}", note, busy, False, False)
        elif r.error:
            self.win.show_paragraph(r.text, f"{tone.label} · {model}", r.error, False, True, False)
        elif r.partial.get(tone.key):
            self.win.show_paragraph(r.partial[tone.key], f"{tone.label} · {model}", r.note, True, False, False)
        else:
            self.win.show_paragraph("Přepisuji…", f"{tone.label} · {model}", r.note, True, True, False)

    def _rewrite_model_name(self) -> str:
        return self.cfg["rewrite"]["model"] or self.cfg["llm"]["model"]

    # --- Enter -----------------------------------------------------------------------------------------------
    def rewrite_accept(self, paste: bool = True):
        """Enter: the rewrite in place of the selection (paste); Alt+Enter: only copied. Not finished yet: as
        soon as it is."""
        r = self._rw
        if r is None:
            return
        if self._rw_tone in r.versions:
            self._rw_replace(r.versions[self._rw_tone][0], paste)
        elif r.pending == self._rw_tone or self._rw_timer:
            self._rw_accept = paste
            r.note = "vložím, až bude hotovo…" if paste else "zkopíruji, až bude hotovo…"
            self._render_rewrite()

    def _rw_replace(self, text: str, paste: bool):
        text = rewrite.keep_outer_space(self._rw.raw if self._rw else "", text)
        self.win.get_display().get_clipboard().set(text)  # (while focused: Mutter takes it only then)
        log.info("rewrite %s %d chars", "pasted" if paste else "copied", len(text))
        if paste:
            try:
                self.get_dbus_connection().emit_signal(None, "/cz/que/QuickTranslate", "cz.que.QuickTranslate.Glass",
                                                       "Paste", None)
            except GLib.Error as e:
                log.debug("paste signal: %s", e.message)
        self.hide_popup()
