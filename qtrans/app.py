"""Quick Translate: a resident Spotlight-like popup for Czech <-> English lookups.

Run: bin/quick-translate [--gapplication-replace]   (the GNOME shortcut runs: gapplication action cz.que.QuickTranslate toggle)

The App owns the lifecycle, the D-Bus actions, showing and hiding, the selection prefill and routes each query to a
mode: wordmode.py (dictionary rows), paragraph_mode.py (a whole paragraph), describe.py ('?popis'). The window and
its widgets are in popup.py.
"""
import os
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from qtrans import config as config_mod  # noqa: E402
from qtrans import log as log_mod  # noqa: E402
from qtrans import paragraph  # noqa: E402
from qtrans.controller import Controller, MTWorker  # noqa: E402
from qtrans.describe import DescribeMode  # noqa: E402
from qtrans.history import History  # noqa: E402
from qtrans.items import Item, history_item  # noqa: E402
from qtrans.paragraph_mode import ParagraphMode  # noqa: E402
from qtrans.rewrite_mode import RewriteMode  # noqa: E402
from qtrans.paths import DB, REPO  # noqa: E402
from qtrans.popup import CLOSE_MS, Popup  # noqa: E402
from qtrans.wordmode import WordMode  # noqa: E402

APP_ID = "cz.que.QuickTranslate"
# Test-only D-Bus actions (query, snapshot, ...) exist only with QT_DEBUG=1 in the app's environment
# (install/install.sh --debug puts it into the D-Bus service).
DEBUG = os.environ.get("QT_DEBUG") == "1"

log = log_mod.setup()


class App(WordMode, ParagraphMode, RewriteMode, DescribeMode, Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.ALLOW_REPLACEMENT)
        self.cfg = config_mod.load()
        self.win = None
        self.searcher = None
        self._db_ino = None
        self._llm_obj = None
        self._last_toggle = 0.0
        self._t0 = 0.0
        self._was_active = False
        self._shell_prefill = False
        self._copy_when_ready = False
        self._closing = False
        self.ctl = None
        self._diacritizer = None
        self.hist = History(enabled=self.cfg["history"]["enabled"])
        self._init_words()
        self._init_paragraph()
        self._init_describe()
        self._init_rewrite()

    # --- lifecycle -------------------------------------------------------------------------
    def do_startup(self):
        Adw.Application.do_startup(self)
        self.hold()  # resident: never exit when the window is hidden
        Gtk.Settings.get_default().set_property("gtk-entry-select-on-focus", False)
        self._add_actions()
        if DEBUG:
            self._add_debug_actions()
        css = Gtk.CssProvider()
        css.load_from_path(str(REPO / "qtrans" / "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        log.info("GNOME extension %s", "active" if _extension_active(Gio.bus_get_sync(Gio.BusType.SESSION)) else "off")
        self.win = Popup(self, self.cfg)
        self.win.connect("notify::is-active", self._on_active)
        self.win.on_changed = self._on_changed
        mt = None
        if self.cfg["mt"]["enabled"]:
            from qtrans.mt import MTEngine
            mt = MTWorker(MTEngine)
        # 3 IO workers: a grammar fix (~1 s) need not wait behind the LLM's options and the automatic LLM pass.
        self.ctl = Controller(mt, debounce_ms=self.cfg["mt"]["debounce_ms"], io_workers=3)
        # Heavy loading after startup returns, so a D-Bus-activated toggle is handled quickly.
        GLib.idle_add(self._load_backends)
        log.info("startup done (pid %d%s)", os.getpid(), ", debug actions" if DEBUG else "")

    def _add_action(self, name: str, cb, ptype: str | None = None):
        a = Gio.SimpleAction.new(name, GLib.VariantType.new(ptype) if ptype else None)
        a.connect("activate", cb)
        self.add_action(a)

    def _add_actions(self):
        """Always there: the Super+T shortcut and the GNOME extension use them."""
        self._add_action("toggle", self.toggle)
        self._add_action("show", lambda *_: self.show_popup())
        self._add_action("hide", lambda *_: self.hide_popup())
        self._add_action("quit", lambda *_: self.quit())
        # The GNOME extension starts the app with it after login: startup readies everything (_load_backends).
        self._add_action("preload", lambda *_: None)
        # Super+R: rewrite the selected text in another tone.
        self._add_action("rewrite", lambda *_: self.start_rewrite())
        # The GNOME extension lays Liquid Glass under the window: then the app draws no background.
        self._add_action("glass", lambda _a, p: self.win.set_glass(p.get_boolean()), "b")
        # The GNOME extension hands over the text selected since the popup was last shown ('' for none).
        self._add_action("prefill", lambda _a, p: self._prefill_from_shell(p.get_string()), "s")

    def _add_debug_actions(self):
        """For tests and development only (QT_DEBUG=1): drive the popup from a script, e.g.
        gapplication action cz.que.QuickTranslate query "'zapomnel'" / snapshot "'/tmp/x.png'" /
        pickword 12 (a click on character 12 of the shown text) / pickalt 0 (the first alternative)."""
        self._add_action("online", lambda *_: self.online_lookup())
        self._add_action("llm", lambda *_: self.ask_llm())
        self._add_action("copy", lambda *_: self.copy_current())
        self._add_action("query", lambda _a, p: self._debug_query(p.get_string()), "s")
        self._add_action("snapshot", lambda _a, p: self.win.save_png(p.get_string()), "s")
        self._add_action("pickword", lambda _a, p: self.win.click_para(*self.win.char_point(p.get_int32())), "i")
        self._add_action("pickalt", lambda _a, p: self._debug_pickalt(p.get_int32()), "i")
        self._add_action("pickctx", lambda _a, p: self._debug_pickctx(p.get_int32()), "i")
        # type "'jak se mas '": as if typed at the cursor, key by key; key "'alt+Left'", "'Tab'", "'ctrl+z'"...
        self._add_action("type", lambda _a, p: self._debug_type(p.get_string()), "s")
        self._add_action("key", lambda _a, p: self._debug_key(p.get_string()), "s")

    def _load_backends(self):
        if self.ctl.mt is not None:
            self.ctl.mt.start()
        self._open_db()
        GLib.idle_add(self._warm_up, priority=GLib.PRIORITY_LOW)
        return GLib.SOURCE_REMOVE

    def _warm_up(self):
        try:
            self.win.warm_up()
        except Exception:
            log.exception("warm-up failed")
        return GLib.SOURCE_REMOVE

    def _open_db(self):
        try:
            ino = os.stat(DB).st_ino
        except FileNotFoundError:
            self.searcher, self._db_ino = None, None
            self._open_diacritics()
            return
        if self.searcher is not None and ino == self._db_ino:
            return
        try:
            from qtrans.search import Searcher
            t = time.perf_counter()
            self.searcher = Searcher(DB)
            self._db_ino = ino
            log.info("dictionary opened in %.0f ms", (time.perf_counter() - t) * 1000)
        except Exception:
            log.exception("cannot open dictionary")
            self.searcher = None
        self._open_diacritics()

    def _open_diacritics(self):
        """A Diacritizer for the open dictionary (a rebuilt one needs a new one), and the input's automatic
        diacritics on top of it."""
        from qtrans.autodiac import AutoDiacritics
        from qtrans.diacritics import Diacritizer
        self._diacritizer = None
        if self.searcher is not None and self.cfg["diacritics"]["enabled"]:
            try:
                self._diacritizer = Diacritizer(self.searcher.con)
            except Exception:
                log.exception("diacritics off")
        if self._diacritizer is not None and not self._diacritizer.ok:
            log.info("diacritics off: the dictionary has no undiac table (rebuild it)")
            self._diacritizer = None
        self.win.diacritics = AutoDiacritics(self._diacritizer) if self._diacritizer is not None else None

    def _restored(self, text: str) -> str:
        """text with its Czech diacritics back for the translators, unless it is English or the user writes with
        diacritics (then their ASCII words are meant)."""
        d = self._diacritizer
        if d is None or self.win.user_diacritics or not text:
            return text
        return d.restore(text)

    def _llm(self):
        from qtrans.llm import LLM
        c = self.cfg["llm"]
        if self._llm_obj is None:
            # (The rewrite's context for every request: ollama reloads the model when it changes.)
            rw = self.cfg["rewrite"]
            same = rw["enabled"] and rw["model"] in ("", c["model"])
            self._llm_obj = LLM(c["url"], c["model"], c["keep_alive"], c["timeout"],
                                num_ctx=rw["num_ctx"] if same else None)
        return self._llm_obj

    def do_activate(self):
        self.show_popup()

    # --- show / hide -----------------------------------------------------------------------
    def toggle(self, *_):
        now = time.monotonic()
        if now - self._last_toggle < 0.3:  # key autorepeat
            return
        self._last_toggle = now
        w = self.win
        if self._closing:  # pressed again while the close animation runs: show it again
            self._finish_hide()
            self.show_popup()
            return
        if w.get_visible():
            if w.is_active():
                self.hide_popup()
            else:
                # A mapped window cannot be raised without an activation token; re-map it instead.
                w.set_visible(False)
                GLib.idle_add(self.show_popup)
            return
        self.show_popup()

    def show_popup(self):
        if self._closing:
            self._finish_hide()
        self._t0 = time.monotonic()
        self._was_active = False
        if not self.win.get_visible():
            self.win._anim_value = 0.0  # the first frame is transparent; the map handler animates it in
        self._open_db()
        self._close_overview()
        self.win.present()
        self.win.input.grab_focus()  # not a selectable paragraph label clicked last time
        log.info("present")
        return GLib.SOURCE_REMOVE

    def _close_overview(self):
        try:
            bus = self.get_dbus_connection()
            res = bus.call_sync("org.gnome.Shell", "/org/gnome/Shell", "org.freedesktop.DBus.Properties", "Get",
                                GLib.Variant("(ss)", ("org.gnome.Shell", "OverviewActive")),
                                GLib.VariantType("(v)"), Gio.DBusCallFlags.NO_AUTO_START, 200, None)
            if res.unpack()[0]:
                bus.call_sync("org.gnome.Shell", "/org/gnome/Shell", "org.freedesktop.DBus.Properties", "Set",
                              GLib.Variant("(ssv)", ("org.gnome.Shell", "OverviewActive", GLib.Variant("b", False))),
                              None, Gio.DBusCallFlags.NO_AUTO_START, 200, None)
                log.info("overview closed")
        except GLib.Error as e:
            log.debug("overview check failed: %s", e.message)

    def hide_popup(self):
        if self._closing or not self.win.get_visible():
            return
        self._was_active = False  # the is-active=False that hiding causes must not hide again
        self._remember_on_close()
        self.ctl.bump()
        self._copy_when_ready = False
        self.win.hide_word_pop()
        self.win.clear_selection()  # do not keep owning PRIMARY with our selection
        self._closing = True
        self._emit_glass_animate(False, CLOSE_MS)
        self.win.animate(False, self._finish_hide)

    def _emit_glass_animate(self, show: bool, duration: int):
        """Tell the GNOME extension, so its glass fades with the content."""
        try:
            self.get_dbus_connection().emit_signal(None, "/cz/que/QuickTranslate", "cz.que.QuickTranslate.Glass",
                                                   "Animate", GLib.Variant("(bu)", (show, duration)))
        except GLib.Error as e:
            log.debug("glass signal: %s", e.message)

    def _finish_hide(self):
        if not self._closing:
            return
        self._closing = False
        self.win.set_visible(False)
        self._end_rewrite()
        # Every opening starts empty (or with the selected text): nothing left from last time.
        self.win.set_text("")
        log.info("hidden")

    def _on_active(self, w, _pspec):
        if w.is_active():
            if not self._was_active:
                self._was_active = True
                log.info("is-active +%.0f ms", (time.monotonic() - self._t0) * 1000)
                self._prefill()
        elif self._was_active:
            self._was_active = False
            self.hide_popup()

    # --- PRIMARY selection prefill ---------------------------------------------------------
    def _prefill_from_shell(self, text: str):
        log.debug("shell prefill %d chars (visible %s)", len(text), self.win.get_visible())
        self._shell_prefill = True
        if not (self.rewriting or self.cfg["prefill"]["enabled"]) or not self.win.get_visible():
            return
        t = self._prefill_text(text)
        if t and not self._typed_since_open():
            self.win.set_text(t, restore=True)
            self._t0 = time.monotonic()  # (our own change is not typing)
            log.info("prefilled %d chars from selection (shell)", len(t))
        self.win.select_all()

    def _prefill(self):
        win = self.win
        if not (self.rewriting or self.cfg["prefill"]["enabled"]) or self._shell_prefill:
            win.select_all()  # the extension sends the selection itself (_prefill_from_shell)
            return
        clip = self.win.get_display().get_primary_clipboard()
        if not clip.is_local() and (clip.get_formats().contain_gtype(GObject.TYPE_STRING)
                                    or clip.get_formats().get_mime_types()):
            clip.read_text_async(None, self._prefill_done)
            return
        # Mutter sends the selection offer only after focus arrives: until then GDK may still think the selection
        # is ours (from the last time) or that there is none. Wait a moment for the new one.
        state = {"id": 0, "timer": 0}

        def changed(c):
            if c.is_local():
                return
            c.disconnect(state["id"])
            GLib.source_remove(state["timer"])
            c.read_text_async(None, self._prefill_done)

        def give_up():
            clip.disconnect(state["id"])
            log.info("no prefill: no selection offer within 300 ms (local %s)", clip.is_local())
            win.select_all()
            return GLib.SOURCE_REMOVE

        state["id"] = clip.connect("changed", changed)
        state["timer"] = GLib.timeout_add(300, give_up)

    def _prefill_done(self, clip, res):
        try:
            text = clip.read_text_finish(res)
        except GLib.Error:
            text = None
        t = self._prefill_text(text)
        # (The field is empty on every opening, so the same selection may be prefilled again; telling a fresh
        # selection from an old one is the GNOME extension's job: _prefill_from_shell.)
        if t and not self._typed_since_open():
            self.win.set_text(t, restore=True)
            log.info("prefilled %d chars from selection", len(t))
        else:
            log.info("no prefill: %s", "typed already" if t else f"selection not usable ({len(text or '')} chars)")
        self.win.select_all()

    def _prefill_text(self, text) -> str:
        """Short single-line text as before (a word or phrase); a longer or multi-line selection only when it
        is a paragraph, cleaned (hard-wrapped lines joined) and capped at max_paragraph_chars. To rewrite: any
        text as it is (an e-mail keeps its lines), up to [rewrite] max_chars."""
        if self.rewriting:
            # As it is (a selected line's newline goes back with the rewrite); too long: shown with a note.
            t = text or ""
            return t if t.strip() and len(t) <= 4 * self.cfg["rewrite"]["max_chars"] else ""
        pf = self.cfg["prefill"]
        t = (text or "").strip()
        if not t or ("\n" not in t and len(t) <= pf["max_chars"]):
            return t
        limit = min(pf["max_paragraph_chars"], paragraph.MAX_PARAGRAPH_CHARS)
        if limit <= 0 or len(t) > 3 * limit:  # half a page selected (Ctrl+A): not meant for us
            return ""
        t = paragraph.cap(paragraph.clean_selection(t), limit)
        if paragraph.is_paragraph(t) or ("\n" not in t and len(t) <= pf["max_chars"]):
            return t
        return ""

    def _typed_since_open(self) -> bool:
        return self.win.changed_at > self._t0

    # --- query routing ---------------------------------------------------------------------
    def _on_changed(self, text: str):
        self.run_query(text)

    def run_query(self, raw: str):
        """Every change of the input: pick the mode (paragraph, '?' description, words) and start it."""
        if self.rewriting:  # (no bump: the running rewrite goes on while the text stays the same)
            self.win.set_mode(True)
            self._run_rewrite(raw)
            return
        self.ctl.bump()
        self._copy_when_ready = False
        self._reset_words()
        self.win.hide_history()  # typing leaves the history
        self.win.set_back(False)
        q = raw.strip()
        para = bool(q) and not q.startswith("?") and paragraph.is_paragraph(q)
        self.win.set_mode(para)
        if para:
            self._run_paragraph(self._restored(q))
            return
        self._para = None
        if not q:
            self.win.set_items([])
            return
        if q.startswith("?") and self.searcher is not None:
            self._describe_query(self._restored(q[1:].strip()))
            return
        self._run_words(raw, q)

    # --- actions (keys and D-Bus) ----------------------------------------------------------
    def online_lookup(self):
        if self.win.paragraph:
            self._para_online()
        else:
            self._word_online()

    def ask_llm(self):
        if self.win.paragraph:
            self._para_llm()
        else:
            self._llm_suggest()

    def copy_current(self):
        if self.rewriting:
            self.rewrite_accept(paste=False)
        elif self.win.paragraph and not self.win.history:
            self.copy_paragraph()
        else:
            self.copy_item(self.win.selected_item())

    def go_back(self, step: int) -> bool:
        """Alt+← / Alt+→: another version of the paragraph; in word mode Alt+← drops the rows Alt+L / Alt+E added."""
        if self.win.paragraph:
            return self.cycle_version(step)
        return step < 0 and self.back_words()

    def toggle_history(self):
        """Tab: the words translated last (3 per direction) in place of the results, Tab again goes back."""
        if self.win.history:
            self.win.hide_history()
            return
        self.win.show_history(self._history_items())
        log.info("history shown")

    def _history_items(self):
        if not self.cfg["history"]["enabled"]:
            return [Item("info", "Historie je vypnutá", "<span alpha='60%'>zapni ji v <tt>config.toml</tt> "
                         "([history] enabled)</span>", copy="")]
        now = time.time()
        items = []
        for lang, title in (("cs", "Čeština → angličtina"), ("en", "Angličtina → čeština")):
            recent = self.hist.recent(lang, 3)
            if recent:
                items.append(Item("head", title, copy="", key=f"head:{lang}"))
                items += [history_item(e, lang, now) for e in recent]
        return items or [Item("info", "Zatím žádná přeložená slovíčka",
                              "<span alpha='60%'>objeví se tu slova, která přeložíš (Enter, nebo jen zavřením okna)"
                              "</span>", copy="")]

    def _debug_type(self, text: str):
        b = self.win.buffer
        for ch in text:  # one user action per character, like key presses
            b.begin_user_action()
            b.delete_selection(True, True)
            b.insert_interactive_at_cursor(ch, -1, True)
            b.end_user_action()

    def _debug_query(self, text: str):
        self._open_db()  # (a query right after startup may come before the dictionary is opened)
        self.win.set_text(text, restore=True)

    def _debug_key(self, spec: str):
        *mods, name = spec.split("+")
        state = Gdk.ModifierType(0)
        for m in mods:
            state |= {"ctrl": Gdk.ModifierType.CONTROL_MASK, "shift": Gdk.ModifierType.SHIFT_MASK,
                      "alt": Gdk.ModifierType.ALT_MASK}[m.lower()]
        keyval = Gdk.keyval_from_name(name)
        if not self.win._on_key(None, keyval, 0, state) and name.lower() == "z" and "ctrl" in spec.lower():
            self.win.buffer.undo()  # (what the text view does with an unhandled Ctrl+Z)

    def _copy(self, text: str):
        # Mutter accepts the clipboard only from the focused window: set it before hiding.
        self.win.get_display().get_clipboard().set(text)
        log.info("copied %d chars", len(text))
        self.hide_popup()


def _extension_active(bus) -> bool:
    """Is the quick-translate GNOME Shell extension enabled and running?"""
    try:
        res = bus.call_sync("org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell.Extensions", "GetExtensionInfo",
                            GLib.Variant("(s)", ("quick-translate@que.cz",)), GLib.VariantType("(a{sv})"),
                            Gio.DBusCallFlags.NO_AUTO_START, 500, None)
        return res.unpack()[0].get("state") == 1  # ExtensionState.ACTIVE
    except GLib.Error:
        return False


def main() -> int:
    app = App()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
