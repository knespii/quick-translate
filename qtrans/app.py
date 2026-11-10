"""Quick Translate: a resident Spotlight-like popup for Czech <-> English lookups.

Run: bin/quick-translate [--gapplication-replace]   (the GNOME shortcut runs: gapplication action cz.que.QuickTranslate toggle)
"""
import math
import os
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango  # noqa: E402

from qtrans import config as config_mod  # noqa: E402
from qtrans import log as log_mod  # noqa: E402
from qtrans import paragraph  # noqa: E402
from qtrans import wordpick  # noqa: E402
from qtrans.controller import Controller, MTWorker  # noqa: E402
from qtrans.normalize import has_czech_chars  # noqa: E402
from qtrans.paths import DB, REPO  # noqa: E402
from qtrans.resultlist import TAU_MOVE, ResultList  # noqa: E402

APP_ID = "cz.que.QuickTranslate"
POS_LABEL = {"n": "podst. jm.", "v": "sloveso", "adj": "příd. jm.", "adv": "přísl.", "prep": "předložka",
             "conj": "spojka", "pron": "zájmeno", "intj": "citosl.", "num": "číslovka", "jm.": "vlastní jméno",
             "fráz.": "fráze", "přísl.": "přísloví", "part.": "částice", "zkr.": "zkratka"}
ROW_H = 54
ENTRY_H = 58
INPUT_LINES = 6        # the input grows up to this many lines, then scrolls
PARA_SCREEN = 0.6      # paragraph mode: the window grows up to this share of the monitor height

log = log_mod.setup()


class Item(GObject.Object):
    """One row in the result list. kind: dict | mt | online | llm | info.
    key identifies the row across updates, so the list can animate it to its new place."""

    def __init__(self, kind, head, sub="", badge="", label="", copy=None, copy_alt=None, pending=False, alts=None,
                 key=None):
        super().__init__()
        self.kind = kind
        self.head = head
        self.sub = sub
        self.badge = badge
        self.label = label
        self.copy = copy if copy is not None else head
        self.copy_alt = copy_alt
        self.pending = pending
        self.alts = alts or []  # individual translations, for Alt+1..9
        self.key = key or (kind if kind in ("mt", "online") else f"{kind}:{head}")


def esc(s: str) -> str:
    return GLib.markup_escape_text(s or "")


FOOTER = ("<b>↵</b> kopírovat   <b>⇧↵</b> heslo   <b>Alt+1…9</b> n-tý   <b>⌃L</b> online   "
          "<b>?</b>popis   <b>⌃E</b> LLM   <b>Esc</b>")
FOOTER_PARA = ("<b>↵</b> kopírovat překlad   <b>⇧↵</b> nový řádek   <b>⌃↑↓</b> originál   <b>⌃L</b> online   "
               "<b>⌃E</b> LLM   <b>Esc</b>")
OPEN_MS, CLOSE_MS = 170, 120
MODE_MS = 160          # cross-fade between the word list and the paragraph block


class Para:
    """State of one paragraph query. versions: key (mt | online | llm) -> (text, source label)."""

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
        self.undo = []  # word replacements: (version key, text before), for Ctrl+Z
        self.src_hits = {}  # source word -> dictionary hits (cache for the word picker)

    @property
    def src(self):
        return self.pair.split("-")[0]

    @property
    def tgt(self):
        return self.pair.split("-")[1]

    def shown(self) -> str:
        return self.versions[self.current][0] if self.current else ""

    def edited(self, key: str | None) -> bool:
        return any(k == key for k, _text in self.undo)


class SmoothHeight(Gtk.Widget):
    """A bin whose height follows its child's natural height with the same exponential smoothing as the
    result list, so the window grows and shrinks softly when the paragraph text changes."""
    __gtype_name__ = "QtSmoothHeight"

    def __init__(self, child):
        super().__init__()
        self.child = child
        child.set_parent(self)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self._h = None
        self._th = 0
        self._tick_id = 0
        self._last = None

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        # Minimum height 0: the stack measures with interpolated sizes while it cross-fades.
        if orientation == Gtk.Orientation.HORIZONTAL:
            return self.child.measure(orientation, -1)
        if for_size < 0 and self._h is not None:  # a wrapped child's height means nothing without a width
            return 0, math.ceil(self._h), -1, -1
        nat = self.child.measure(orientation, for_size)[1]
        self._th = nat
        animate = Gtk.Settings.get_default().get_property("gtk-enable-animations") and self.get_mapped()
        if self._h is None or not animate:
            self._h = nat
        elif abs(self._h - nat) >= 0.5 and not self._tick_id:
            self._last = None
            self._tick_id = self.add_tick_callback(self._tick)
        return 0, math.ceil(self._h), -1, -1

    def _tick(self, _widget, clock):
        now = clock.get_frame_time() / 1e6
        dt = 1 / 60 if self._last is None else min(0.05, max(0.0, now - self._last))
        self._last = now
        self._h += (self._th - self._h) * (1 - math.exp(-dt / TAU_MOVE))
        self.queue_resize()
        if abs(self._th - self._h) < 0.5:
            self._h = self._th
            self._tick_id = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def do_size_allocate(self, width, height, baseline):
        # The child gets its full target height; this widget clips it while growing.
        self.child.allocate(width, max(height, self._th), -1, None)

    def do_unmap(self):
        Gtk.Widget.do_unmap(self)
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        self._h = None  # the next showing starts at the right height (the stack interpolates the switch)

    def do_dispose(self):
        if self.child is not None:
            self.child.unparent()
            self.child = None
        Gtk.Widget.do_dispose(self)


class Popup(Adw.ApplicationWindow):
    def __init__(self, app, cfg):
        super().__init__(application=app, title="Quick Translate")
        self.cfg = cfg
        width = cfg["ui"]["width"]
        self.set_resizable(False)
        self.set_hide_on_close(True)
        self.set_size_request(width, -1)  # drop libadwaita's 360x200 minimum: no empty space under the entry
        self.add_css_class("qt-popup")
        self.set_default_size(width, -1)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_size_request(width, -1)
        box.set_overflow(Gtk.Overflow.HIDDEN)

        # Multi-line input that looks like a one-line entry for short text; grows up to INPUT_LINES, then scrolls.
        self.input = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        self.input.add_css_class("qt-entry")
        self.buffer = self.input.get_buffer()
        # The word of the original a picked translation word comes from (paragraph mode alternatives).
        self.source_tag = self.buffer.create_tag("qt-source-word", background="#f6d32d", foreground="#241f31")
        self.input_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                 vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.input_scroller.set_propagate_natural_height(True)
        self.input_scroller.set_child(self.input)
        self.placeholder = Gtk.Label(label="Přelož… česky nebo anglicky", halign=Gtk.Align.START,
                                     valign=Gtk.Align.START, can_target=False)
        self.placeholder.add_css_class("qt-placeholder")
        overlay = Gtk.Overlay(child=self.input_scroller)
        overlay.add_overlay(self.placeholder)
        box.append(overlay)
        self.sep = Gtk.Separator(visible=False)
        box.append(self.sep)
        self.on_changed = None
        self.changed_at = 0.0
        self._changed_idle = 0
        self.buffer.connect("changed", self._buffer_changed)

        self.list = ResultList(ROW_H, self._make_row, lambda item: self.app_ref.copy_item(item))
        # EXTERNAL: no scrollbar (its minimum height would leave a gap under an empty list); the list still
        # scrolls with the wheel and with ↑/↓.
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           vscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        self.scroller.set_child(self.list)
        self.scroller.set_propagate_natural_height(True)
        self.scroller.set_max_content_height(cfg["ui"]["rows"] * ROW_H)

        # Paragraph mode: one wrapped, selectable block with a small header (source, spinner, hint).
        self.paragraph = False
        self.para_source = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.para_source.add_css_class("qt-label")
        self.para_spinner = Gtk.Spinner(opacity=0.0)
        self.para_note = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.para_note.add_css_class("qt-label")
        hint = Gtk.Label(label="klik na slovo = alternativy · Ctrl+↑/↓ originál")
        hint.add_css_class("qt-para-hint")
        self.para_head = Gtk.Box(spacing=8)
        self.para_head.add_css_class("qt-para-head")
        for w in (self.para_source, self.para_spinner, self.para_note, hint):
            self.para_head.append(w)
        # max_width_chars=1: a wrapped label must not ask for the width of its whole text (the window would widen).
        self.para_text = Gtk.Label(xalign=0, yalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                   selectable=True, focus_on_click=False, max_width_chars=1, hexpand=True)
        self.para_text.add_css_class("qt-para-text")
        self.para_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.para_scroller.set_propagate_natural_height(True)
        self.para_scroller.set_child(self.para_text)
        # A click (not a drag) on a word of the translation shows its dictionary alternatives. A raw event
        # controller in the capture phase: the label's own selection gestures may claim the click.
        clicks = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        clicks.connect("event", self._on_para_event)
        self.para_text.add_controller(clicks)
        self._press = None
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(self.para_head)
        page.append(self.para_scroller)
        # Not autohide: the keyboard stays in the input (Esc, Enter, Ctrl+Z keep working); the buttons do not
        # take focus. Parented to the box: its layout manager presents the popover on every allocation (a
        # label does not, so the popover would not follow the text as it reflows).
        self.word_pop = Gtk.Popover(autohide=False, position=Gtk.PositionType.BOTTOM, has_arrow=False)
        self.word_pop.set_offset(0, 6)
        self.word_pop.add_css_class("qt-word-pop")
        self.word_pop.set_parent(page)
        self.word_pop.connect("closed", lambda _p: self.clear_source_highlight())
        self.word_pop_buttons = []
        self.para_box = SmoothHeight(page)

        self.stack = Gtk.Stack(vhomogeneous=False, interpolate_size=True, transition_duration=MODE_MS,
                               transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_child(self.scroller)
        self.stack.add_child(self.para_box)
        self.stack.connect("notify::transition-running", self._on_mode_switched)
        box.append(self.stack)

        self.footer = Gtk.Label(xalign=0.5, ellipsize=Pango.EllipsizeMode.END)
        self.footer.set_markup(FOOTER)
        self.footer.add_css_class("qt-footer")
        box.append(self.footer)
        self.set_content(box)
        self._fit_input()

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        # Open/close animation: the whole window (shadow included) fades and grows from 97 %.
        self._anim_value = 1.0
        self._anim = None
        self.connect("map", lambda *_: self.animate(True))

    # --- open / close animation ------------------------------------------------------------------
    def animate(self, show: bool, done=None):
        if self._anim is not None:
            self._anim.pause()
        target = Adw.CallbackAnimationTarget.new(self._set_anim_value)
        a = Adw.TimedAnimation.new(self, self._anim_value, 1.0 if show else 0.0, OPEN_MS if show else CLOSE_MS, target)
        a.set_easing(Adw.Easing.EASE_OUT_CUBIC if show else Adw.Easing.EASE_IN_CUBIC)
        if done is not None:
            a.connect("done", lambda *_: done())
        self._anim = a
        a.play()

    def _set_anim_value(self, value):
        self._anim_value = value
        self.queue_draw()

    def do_snapshot(self, snapshot):
        v = self._anim_value
        if v >= 0.999:
            Adw.ApplicationWindow.do_snapshot(self, snapshot)
            return
        w = self.get_width()
        k = 0.97 + 0.03 * v
        snapshot.push_opacity(max(0.0, v))
        snapshot.save()
        snapshot.translate(Graphene.Point().init(w / 2, 0))
        snapshot.scale(k, k)
        snapshot.translate(Graphene.Point().init(-w / 2, -8 * (1 - v)))
        Adw.ApplicationWindow.do_snapshot(self, snapshot)
        snapshot.restore()
        snapshot.pop()

    # --- input -----------------------------------------------------------------------------------
    def _fit_input(self):
        """Centre one line of text in ENTRY_H, like the old Gtk.Entry; the placeholder sits on that line."""
        lh = self.input.create_pango_layout("Ág").get_pixel_size()[1]
        top = max(4, (ENTRY_H - lh) // 2)
        self.input.set_top_margin(top)
        self.input.set_bottom_margin(max(4, ENTRY_H - lh - top))
        self.placeholder.set_margin_top(top)
        self.input_scroller.set_max_content_height(lh * INPUT_LINES + ENTRY_H - lh)

    def _buffer_changed(self, _buf):
        self.changed_at = time.monotonic()
        self.placeholder.set_visible(self.buffer.get_char_count() == 0)
        # Typing over a selection (or set_text) is a delete and an insert: run the query once, before the redraw.
        if not self._changed_idle:
            self._changed_idle = GLib.idle_add(self._emit_changed, priority=GLib.PRIORITY_HIGH_IDLE)

    def _emit_changed(self):
        self._changed_idle = 0
        if self.on_changed is not None:
            self.on_changed(self.get_text())
        return GLib.SOURCE_REMOVE

    def get_text(self) -> str:
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)

    def set_text(self, text: str):
        self.buffer.set_text(text)

    def select_all(self):
        # The cursor goes to the start, so a long prefilled paragraph is shown from its beginning.
        self.buffer.select_range(self.buffer.get_start_iter(), self.buffer.get_end_iter())
        self.input.scroll_mark_onscreen(self.buffer.get_insert())

    def clear_selection(self):
        self.buffer.place_cursor(self.buffer.get_iter_at_mark(self.buffer.get_insert()))

    def insert_newline(self):
        b = self.buffer
        b.begin_user_action()
        b.delete_selection(True, True)
        b.insert_interactive_at_cursor("\n", -1, True)
        b.end_user_action()
        self.input.scroll_mark_onscreen(b.get_insert())

    # --- word / paragraph mode ---------------------------------------------------------------------
    def set_mode(self, para: bool):
        if para == self.paragraph:
            return
        self.paragraph = para
        self.hide_word_pop()
        self.stack.set_visible_child(self.para_box if para else self.scroller)
        self.footer.set_markup(FOOTER_PARA if para else FOOTER)
        if para:
            self.sep.set_visible(True)

    def _on_mode_switched(self, stack, _pspec):
        # The hidden word list keeps its rows during the cross-fade; empty it afterwards, so coming back
        # to word mode starts fresh instead of gliding from rows of some earlier query.
        if not stack.get_transition_running() and self.paragraph:
            self.list.set_items([])

    def show_paragraph(self, text: str, source: str, note: str, busy: bool, dim: bool, original: bool):
        self.para_scroller.set_max_content_height(self._para_max_height())
        if self.para_text.get_text() != text:
            self.hide_word_pop()
            self.para_text.set_text(text)
            self.para_scroller.get_vadjustment().set_value(0)
        for cls, on in (("qt-dim", dim), ("qt-original", original)):
            (self.para_text.add_css_class if on else self.para_text.remove_css_class)(cls)
        self.para_source.set_text(source)
        self.para_note.set_text(note)
        self.para_spinner.set_opacity(1.0 if busy else 0.0)  # always allocated: the header never changes height
        self.para_spinner.set_spinning(busy)

    def _para_max_height(self) -> int:
        cap = 600
        surface = self.get_surface()
        mon = self.get_display().get_monitor_at_surface(surface) if surface is not None else None
        if mon is not None:
            cap = int(mon.get_geometry().height * PARA_SCREEN)
        # Count the input at its full height: the text view lays out lazily, so its current size is stale here,
        # and only a long text (which fills the input anyway) reaches the cap.
        input_h = self.input_scroller.get_max_content_height()
        used = input_h + _natural_height(self.para_head) + _natural_height(self.footer) + 1
        return max(3 * ROW_H, cap - used)

    def scroll_para(self, pages: int):
        adj = self.para_scroller.get_vadjustment()
        adj.set_value(adj.get_value() + pages * adj.get_page_size() * 0.9)

    # --- word picker (paragraph mode) --------------------------------------------------------------
    def _on_para_event(self, ctl, _event):
        event = ctl.get_current_event()  # PyGObject passes the signal's GdkEvent as None
        if event is None:
            return False
        kind = event.get_event_type()
        if kind not in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE) or event.get_button() != 1:
            return False
        x, y = self._event_point(event)
        if kind == Gdk.EventType.BUTTON_PRESS:
            self._press = (x, y)
            return False
        press, self._press = self._press, None
        if press is not None and abs(x - press[0]) <= 4 and abs(y - press[1]) <= 4:
            # After the label has handled the release (a click clears its selection, a drag keeps it).
            GLib.idle_add(self._click_idle, x, y)
        return False

    def _click_idle(self, x, y):
        self.click_para(x, y)
        return GLib.SOURCE_REMOVE

    def _event_point(self, event):
        native = self.para_text.get_native()
        _ok, sx, sy = event.get_position()
        nx, ny = native.get_surface_transform()
        _ok, p = native.compute_point(self.para_text, Graphene.Point().init(sx - nx, sy - ny))
        return p.x, p.y

    def click_para(self, x: float, y: float):
        """A click at (x, y) in the paragraph text: pick the word there, unless text is selected."""
        if self.para_text.get_selection_bounds()[0]:
            return
        layout = self.para_text.get_layout()
        ox, oy = self.para_text.get_layout_offsets()
        inside, index, _trailing = layout.xy_to_index(int((x - ox) * Pango.SCALE), int((y - oy) * Pango.SCALE))
        text = layout.get_text()
        i = len(text.encode()[:index].decode(errors="ignore")) if inside else -1
        self.app_ref.pick_word(i)

    def char_point(self, i: int):
        """The centre of character i of the paragraph text, in the label's coordinates (for 'pickword')."""
        r = self._char_rect(i)
        return r.x + r.width / 2, r.y + r.height / 2

    def _char_rect(self, i: int) -> Gdk.Rectangle:
        layout = self.para_text.get_layout()
        ox, oy = self.para_text.get_layout_offsets()
        pos = layout.index_to_pos(len(layout.get_text()[:i].encode()))
        r = Gdk.Rectangle()
        r.x, r.y = ox + pos.x // Pango.SCALE, oy + pos.y // Pango.SCALE
        r.width, r.height = max(1, abs(pos.width) // Pango.SCALE), max(1, pos.height // Pango.SCALE)
        return r

    def show_word_pop(self, start: int, end: int, groups, on_pick):
        """Point a popover at text[start:end] with groups [(head, label, alternatives)]; on_pick(n) gets the
        index of the alternative clicked (counted over all groups)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.word_pop_buttons = []
        for head, label, alts, *badge in groups:
            # Looks like a result row: badge, bold headword, dim label; alternatives as light chips.
            top = Gtk.Box(spacing=8)
            top.add_css_class("qt-word-pop-head")
            if badge and badge[0]:
                bl = Gtk.Label(label=badge[0])
                bl.add_css_class("qt-badge")
                bl.add_css_class(f"qt-badge-{badge[0].lower()}")
                top.append(bl)
            h = Gtk.Label(xalign=0)
            h.set_markup(f"<b>{esc(head)}</b>")
            h.add_css_class("qt-head")
            top.append(h)
            if label:
                lab = Gtk.Label(label=label, xalign=0, ellipsize=Pango.EllipsizeMode.END)
                lab.add_css_class("qt-label")
                top.append(lab)
            box.append(top)
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6,
                               column_spacing=2, row_spacing=2, homogeneous=False)
            for alt in alts:
                n = len(self.word_pop_buttons)
                b = Gtk.Button(label=alt, focus_on_click=False, can_focus=False)
                b.add_css_class("flat")
                b.add_css_class("qt-chip")
                b.connect("clicked", lambda _b, n=n: on_pick(n))
                flow.append(b)
                b.get_parent().set_can_focus(False)
                self.word_pop_buttons.append(b)
            box.append(flow)
        if not groups:
            for text, cls in (("Ve slovníku nic", "qt-word-pop-empty"),
                              ("lepší celý překlad: Ctrl+L online, Ctrl+E LLM", "qt-label")):
                lab = Gtk.Label(label=text, xalign=0)
                lab.add_css_class(cls)
                box.append(lab)
        self.word_pop.set_child(box)
        a, b = self._char_rect(start), self._char_rect(end - 1)
        if b.y == a.y:  # a word wrapped over two lines (WORD_CHAR) points at its first letter only
            a.width = max(1, b.x + b.width - a.x)
        _ok, p = self.para_text.compute_point(self.word_pop.get_parent(), Graphene.Point().init(a.x, a.y))
        a.x, a.y = int(p.x), int(p.y)
        # A popover is centred under what it points at; start it at the word instead, inside the window.
        pop_w = box.measure(Gtk.Orientation.HORIZONTAL, -1)[1] + 14  # (+ its padding and border, style.css)
        page_w = self.word_pop.get_parent().get_width()
        left = max(8, min(a.x - 12, page_w - pop_w - 8))
        self.word_pop.set_offset(int(left + pop_w / 2 - (a.x + a.width / 2)), 6)
        self.word_pop.set_pointing_to(a)
        self.word_pop.popup()

    def highlight_source(self, spans):
        """Yellow background on these (start, end) character spans of the input, the first one scrolled into view."""
        self.clear_source_highlight()
        b = self.buffer
        for s, e in spans:
            b.apply_tag(self.source_tag, b.get_iter_at_offset(s), b.get_iter_at_offset(e))
        if spans:
            self.input.scroll_to_iter(b.get_iter_at_offset(spans[0][0]), 0.1, False, 0, 0)

    def clear_source_highlight(self):
        self.buffer.remove_tag(self.source_tag, self.buffer.get_start_iter(), self.buffer.get_end_iter())

    def hide_word_pop(self) -> bool:
        if not self.word_pop.get_visible():
            return False
        self.word_pop.popdown()
        return True

    # --- rows ------------------------------------------------------------------------------------
    def _make_row(self, item: Item):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, valign=Gtk.Align.CENTER)
        row.add_css_class("qt-row")
        row.add_css_class(f"qt-row-{item.kind}")
        top = Gtk.Box(spacing=8)
        if item.badge:
            b = Gtk.Label(label=item.badge)
            b.add_css_class("qt-badge")
            b.add_css_class(f"qt-badge-{item.badge.lower()}")
            top.append(b)
        head = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
        head.set_markup(f"<b>{esc(item.head)}</b>")
        head.add_css_class("qt-head")
        top.append(head)
        if item.label:
            lab = Gtk.Label(label=item.label, xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
            lab.add_css_class("qt-label")
            top.append(lab)
        if item.pending:
            top.append(Gtk.Spinner(spinning=True))
        row.append(top)
        if item.sub:
            sub = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
            sub.set_markup(item.sub)
            sub.add_css_class("qt-sub")
            row.append(sub)
        return row

    def set_glass(self, on: bool):
        if on != self.has_css_class("qt-glass"):
            (self.add_css_class if on else self.remove_css_class)("qt-glass")
            log.info("liquid glass %s", "on" if on else "off")

    def set_items(self, items, select: int = 0):
        self.list.set_items(items, select)
        self.sep.set_visible(bool(items))
        self.scroller.get_vadjustment().set_value(0)

    def selected_item(self):
        return None if self.paragraph else self.list.selected_item()

    def move(self, delta: int):
        n = len(self.list.items)
        if not n:
            return
        i = max(0, min(n - 1, self.list.selected + delta))
        self.list.select(i)
        # Keep the selected row visible without moving keyboard focus out of the entry.
        adj = self.scroller.get_vadjustment()
        top, bottom = i * ROW_H, (i + 1) * ROW_H
        if top < adj.get_value():
            adj.set_value(top)
        elif bottom > adj.get_value() + adj.get_page_size():
            adj.set_value(bottom - adj.get_page_size())

    def _on_key(self, _ctl, keyval, _code, state):
        app = self.app_ref
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        para = self.paragraph
        up_down = (Gdk.KEY_Down, Gdk.KEY_KP_Down, Gdk.KEY_Up, Gdk.KEY_KP_Up)
        if keyval == Gdk.KEY_Escape:
            if not self.hide_word_pop():
                app.hide_popup()
        elif para and ctrl and keyval in up_down:
            app.toggle_original()
        elif para and keyval in up_down:
            return False  # the cursor moves inside the multi-line text
        elif keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self.move(1)
        elif keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self.move(-1)
        elif keyval in (Gdk.KEY_Page_Down,):
            self.scroll_para(1) if para else self.move(5)
        elif keyval in (Gdk.KEY_Page_Up,):
            self.scroll_para(-1) if para else self.move(-5)
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            if para and shift:
                self.insert_newline()
            elif para:
                app.copy_paragraph()
            else:
                app.copy_item(self.selected_item(), alt_text=shift)
        elif ctrl and keyval in (Gdk.KEY_l, Gdk.KEY_L):
            app.online_lookup()
        elif ctrl and keyval in (Gdk.KEY_e, Gdk.KEY_E):
            app.ask_llm()
        elif para and ctrl and not shift and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            return app.undo_replacement()  # nothing to undo: the input undoes typing
        elif alt and not para and Gdk.KEY_1 <= keyval <= Gdk.KEY_9:
            app.copy_nth(keyval - Gdk.KEY_1)
        else:
            return False
        return True


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.ALLOW_REPLACEMENT)
        self.cfg = config_mod.load()
        self.win = None
        self.searcher = None
        self._db_ino = None
        self._last_toggle = 0.0
        self._t0 = 0.0
        self._was_active = False
        self._last_prefill = None
        self._copy_when_ready = False
        self._mt_item = None
        self._online_item = None
        self._last_hits = []
        self._closing = False
        self._para = None
        self._llm_used_at = 0.0
        self._auto_llm_failed = set()  # paragraph texts the automatic LLM pass failed on: not retried
        self._pick = None  # the word the alternatives popover is open for
        self.ctl = None

    # --- lifecycle -------------------------------------------------------------------------
    def do_startup(self):
        Adw.Application.do_startup(self)
        self.hold()  # resident: never exit when the window is hidden
        Gtk.Settings.get_default().set_property("gtk-entry-select-on-focus", False)
        for name, cb in (("toggle", self.toggle), ("show", lambda *_: self.show_popup()),
                         ("hide", lambda *_: self.hide_popup()), ("quit", lambda *_: self.quit()),
                         ("online", lambda *_: self.online_lookup()),
                         ("llm", lambda *_: self.ask_llm()),
                         ("copy", lambda *_: self.copy_current())):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
        # The GNOME extension lays Liquid Glass under the window: then the app draws no background.
        glass = Gio.SimpleAction.new("glass", GLib.VariantType.new("b"))
        glass.connect("activate", lambda _a, p: self.win.set_glass(p.get_boolean()))
        self.add_action(glass)
        # The GNOME extension hands over the text selected since the popup was last shown ('' for none).
        prefill = Gio.SimpleAction.new("prefill", GLib.VariantType.new("s"))
        prefill.connect("activate", lambda _a, p: self._prefill_from_shell(p.get_string()))
        self.add_action(prefill)
        # Debug helpers: gapplication action cz.que.QuickTranslate query "'zapomnel'" / snapshot "'/tmp/x.png'"
        for name, cb in (("query", lambda _a, p: self.win.set_text(p.get_string())),
                         ("snapshot", lambda _a, p: self._snapshot(p.get_string()))):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            a.connect("activate", cb)
            self.add_action(a)
        # ... pickword 12 (a click on character 12 of the shown text) / pickalt 0 (the first alternative)
        for name, cb in (("pickword", lambda _a, p: self.win.click_para(*self.win.char_point(p.get_int32()))),
                         ("pickalt", lambda _a, p: self._debug_pickalt(p.get_int32()))):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("i"))
            a.connect("activate", cb)
            self.add_action(a)
        css = Gtk.CssProvider()
        css.load_from_path(str(REPO / "qtrans" / "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        log.info("GNOME extension %s", "active" if _extension_active(Gio.bus_get_sync(Gio.BusType.SESSION)) else "off")
        self.win = Popup(self, self.cfg)
        self.win.app_ref = self
        self.win.connect("notify::is-active", self._on_active)
        self.win.on_changed = self._on_changed
        mt = None
        if self.cfg["mt"]["enabled"]:
            from qtrans.mt import MTEngine
            mt = MTWorker(MTEngine)
        self.ctl = Controller(mt, debounce_ms=self.cfg["mt"]["debounce_ms"])
        # Heavy loading after startup returns, so a D-Bus-activated toggle is handled quickly.
        GLib.idle_add(self._load_backends)
        log.info("startup done (pid %d)", os.getpid())

    def _load_backends(self):
        if self.ctl.mt is not None:
            self.ctl.mt.start()
        self._open_db()
        return GLib.SOURCE_REMOVE

    def _open_db(self):
        try:
            ino = os.stat(DB).st_ino
        except FileNotFoundError:
            self.searcher, self._db_ino = None, None
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
        if not self.cfg["prefill"]["enabled"] or not self.win.get_visible():
            return
        t = self._prefill_text(text)
        if t and not self._typed_since_open():
            self.win.set_text(t)
            self._t0 = time.monotonic()  # (our own change is not typing)
            log.info("prefilled %d chars from selection (shell)", len(t))
        self.win.select_all()

    def _prefill(self):
        win = self.win
        if not self.cfg["prefill"]["enabled"] or getattr(self, "_shell_prefill", False):
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
            self._last_prefill = t
            self.win.set_text(t)
            log.info("prefilled %d chars from selection", len(t))
        else:
            log.info("no prefill: %s", "typed already" if t else f"selection not usable ({len(text or '')} chars)")
        self.win.select_all()

    def _prefill_text(self, text) -> str:
        """Short single-line text as before (a word or phrase); a longer or multi-line selection only when it
        is a paragraph, cleaned (hard-wrapped lines joined) and capped at max_paragraph_chars."""
        pf = self.cfg["prefill"]
        t = (text or "").strip()
        if not t or ("\n" not in t and len(t) <= pf["max_chars"]):
            return t
        limit = min(pf["max_paragraph_chars"], paragraph.MAX_PARAGRAPH_CHARS)
        if limit <= 0 or len(t) > 3 * limit:  # half a page selected (Ctrl+A): not meant for us
            return ""
        t = paragraph.clean_selection(t)
        if len(t) > limit:
            t = t[:limit].rsplit(" ", 1)[0]
        if paragraph.is_paragraph(t) or ("\n" not in t and len(t) <= pf["max_chars"]):
            return t
        return ""

    def _typed_since_open(self) -> bool:
        return self.win.changed_at > self._t0

    # --- query handling --------------------------------------------------------------------
    def _on_changed(self, text: str):
        self.run_query(text)

    def run_query(self, raw: str):
        t = time.perf_counter()
        self.ctl.bump()
        self._copy_when_ready = False
        self._mt_item = None
        self._online_item = None
        self._last_hits = []
        q = raw.strip()
        items = []
        hits = []
        para = bool(q) and not q.startswith("?") and paragraph.is_paragraph(q)
        self.win.set_mode(para)
        if para:
            self._run_paragraph(q)
            return
        self._para = None
        if not q:
            self.win.set_items([])
            return
        if q.startswith("?") and self.searcher is not None:
            self._describe_query(q[1:].strip())
            return
        if self.searcher is None:
            self._open_db()  # a query before the idle load (D-Bus activation) still finds the dictionary
        if self.searcher is None:
            items.append(Item("info", "Slovník chybí", "spusť <tt>python -m dictbuild.build_db</tt>", copy=""))
        else:
            hits = self.searcher.search(raw, limit=self.cfg["ui"]["rows"] * 2)
            self._last_hits = hits
            items += [_hit_item(h) for h in hits]
        pairs = self._mt_pairs(q, hits)
        if pairs and self.ctl.schedule_mt(q, pairs, self._mt_done):
            self._mt_item = Item("mt", "Překládám…", "<span alpha='60%'>strojový překlad</span>", copy="", pending=True)
            pos = 0 if not hits or len(q.split()) >= 3 else min(len(items), 3)
            items.insert(pos, self._mt_item)
        self._set_items(items)
        log.debug("query %r: %d hits in %.1f ms", raw, len(hits), (time.perf_counter() - t) * 1000)

    # --- paragraph mode ----------------------------------------------------------------------------
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
            p.note = "offline překlad není k dispozici — Ctrl+L online, Ctrl+E LLM"
        self._render_para()

    def _render_para(self):
        p = self._para
        if p is None:
            return
        if p.show_original:
            text, source, dim = p.text, f"originál · {p.src}", False
        elif p.current:
            (text, source), dim = p.versions[p.current], False
        elif p.stale and p.pending:
            text, source, dim = p.stale, "", True
        else:
            text, source, dim = ("Překládám…" if p.pending else ""), "", True
        if not p.show_original and p.edited(p.current):
            source += " · upraveno"
        self.win.show_paragraph(text, source, p.note, bool(p.pending), dim, p.show_original)

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
            if gen == self.ctl.gen and p is self._para and p.current == "mt" and not p.edited("mt"):
                self._para_llm(auto=True)
            return False

        delay = max(0, int(600 - (time.monotonic() - p.born) * 1000))
        GLib.timeout_add(delay, fire)

    def _para_result(self, p: Para, key: str, text: str, source: str):
        if p is not self._para:
            return
        p.pending.discard(key)
        p.versions[key] = (text, source)
        p.undo = [u for u in p.undo if u[0] != key]  # a new text of that version: its word edits are gone
        # A late offline result must not replace the better online / LLM one, nor the automatic LLM pass
        # a version the user asked for (Ctrl+L) or a translation the user has fixed words in.
        auto = key == "llm" and p.auto_llm
        if key == "llm":
            p.auto_llm = False
        p.note = ""
        if (key != "mt" and not auto) or (p.current in (None, "mt") and not p.edited(p.current)):
            p.current = key
            p.show_original = False
        elif auto and p.edited(p.current):
            p.note = "LLM verze: Ctrl+E"
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
        if not p.pending:  # Enter pressed while waiting: nothing will come, so a later Ctrl+L/E must not copy and close
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
            if not auto and p.auto_llm:  # Ctrl+E while the automatic pass runs: it now counts as asked for
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

    def pick_word(self, i: int):
        """A click on character i of the shown paragraph text: alternatives for the word there (translation only)."""
        p, win = self._para, self.win
        text = p.shown() if p is not None and not p.show_original else ""
        span = wordpick.word_at(text, i) if text and win.para_text.get_text() == text else None
        if span is None:
            self._pick = None
            win.hide_word_pop()
            return
        start, end = span
        word = text[start:end]
        groups, alts, spans = [], [], []
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
            log.info("pick %r: %d alternatives in %.0f ms", word, len(alts), (time.perf_counter() - t) * 1000)
        self._pick = (p, p.current, text, start, end, alts)
        win.show_word_pop(start, end, groups, self.pick_alt)
        win.highlight_source(spans)

    def pick_alt(self, n: int):
        """Replace the picked word with alternative n in the shown translation."""
        pick, self._pick = self._pick, None
        self.win.hide_word_pop()
        p = self._para
        if pick is None or pick[0] is not p or n >= len(pick[5]):
            return
        _p, key, text, start, end, alts = pick
        if p.current != key or p.shown() != text or p.show_original:
            return
        p.undo.append((key, text))
        p.versions[key] = (wordpick.replace_span(text, start, end, alts[n]), p.versions[key][1])
        log.info("replaced %r with %r", text[start:end], alts[n])
        self._render_para()

    def _debug_pickalt(self, n: int):
        if self._pick is not None and n < len(self.win.word_pop_buttons):
            self.win.word_pop_buttons[n].emit("clicked")

    def undo_replacement(self) -> bool:
        """Ctrl+Z: undo the last word replacement in the shown translation. False when there is none."""
        p = self._para
        if p is None or p.show_original or not p.edited(p.current):
            return False
        i = max(j for j, (k, _t) in enumerate(p.undo) if k == p.current)
        key, text = p.undo.pop(i)
        p.versions[key] = (text, p.versions[key][1])
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

    def copy_current(self):
        if self.win.paragraph:
            self.copy_paragraph()
        else:
            self.copy_item(self.win.selected_item())

    # --- describe a word ("?věc na otevírání konzerv") ---------------------------------------------
    def _describe_query(self, q: str):
        self._describe_text = q
        if not q:
            self._set_items([Item("info", "Popiš slovo, které ti nejde na jazyk",
                                  "<span alpha='60%'>např. <i>?věc na otevírání konzerv</i>  ·  Ctrl+E zeptá se LLM</span>",
                                  copy="")])
            self._llm_prewarm()
            return
        rev = self._reverse()
        czech = has_czech_chars(q) or not _looks_english(q)
        lists = ([rev.search_cs(q)] if czech else []) + [rev.search_en(q)]
        self._describe_lists = lists
        self._set_items(self._describe_items(rev.fuse(lists)))
        if czech:
            self.ctl.schedule_mt(q, ["cs-en"], self._describe_mt_done)

    def _describe_mt_done(self, res):
        if not res or isinstance(res, Exception):
            return
        rev = self._reverse()
        lists = self._describe_lists + [rev.search_en(res[0][1])]
        self._set_items(self._describe_items(rev.fuse(lists)))

    def _describe_items(self, hits):
        items = [_hit_item(h, label=h.label) for h in hits[: self.cfg["ui"]["rows"] * 2]]
        if not items:
            items = [Item("info", "Nic podle popisu", "<span alpha='60%'>zkus jiná slova nebo Ctrl+E (LLM)</span>", copy="")]
        return items

    def _reverse(self):
        from qtrans.reverse import Reverse
        if getattr(self, "_rev", None) is None or self._rev.searcher is not self.searcher:
            self._rev = Reverse(self.searcher.con, self.searcher)
        return self._rev

    def _llm(self):
        from qtrans.llm import LLM
        c = self.cfg["llm"]
        if getattr(self, "_llm_obj", None) is None:
            self._llm_obj = LLM(c["url"], c["model"], c["keep_alive"], c["timeout"])
        return self._llm_obj

    def _llm_prewarm(self):
        if getattr(self, "_prewarmed", False):
            return
        self._prewarmed = True
        llm = self._llm()
        self.ctl._pool.submit(lambda: _quiet(llm.prewarm))

    def ask_llm(self):
        if self.win.paragraph:
            self._para_llm()
            return
        text = self.win.get_text().strip().lstrip("?").strip()
        if not text or self.searcher is None:
            return
        hints = [(i.head, i.alts[0]) for i in self.win.list.items if i.kind == "dict" and i.alts][:15]
        self._llm_item = Item("llm", f"Ptám se modelu {self.cfg['llm']['model']}…",
                              "<span alpha='60%'>první dotaz může trvat i desítky sekund (načítá se model)</span>",
                              copy="", pending=True)
        self._insert_top(self._llm_item)
        llm = self._llm()
        self.ctl.run_io(lambda: llm.suggest(text, hints), self._llm_done)
        log.info("llm question (%d hints)", len(hints))

    def _llm_done(self, res):
        item = getattr(self, "_llm_item", None)
        idx = self.win.list.index_of(item) if item else None
        if idx is None:
            return
        if isinstance(res, Exception):
            self.win.list.replace(idx, [Item("info", "LLM nedostupné", f"<span alpha='60%'>{esc(str(res))}</span>",
                                             copy="", key="llm")], select=idx)
            return
        rows = []
        con = self.searcher.con
        for c in res:
            cs, en, why = (c.get("cs") or "").strip(), (c.get("en") or "").strip(), (c.get("why") or "").strip()
            # The Czech words come from the dictionary (via the English word); the model's own Czech only if
            # the dictionary does not know the English word, and then marked as unverified.
            row = con.execute("SELECT id, lang, text, key, nwords, zipf, flags, pos FROM term WHERE key = ? AND lang = 1 "
                              "ORDER BY zipf DESC LIMIT 1", (_norm(en),)).fetchone() if en else None
            if row:
                from qtrans.search import Hit, LANG
                h = Hit(row[0], LANG[row[1]], *row[2:])
                trans = [t for t, _n in self.searcher.translations(h, 4)]
                sub = esc(", ".join(trans)) + (f"  <span alpha='60%'>({esc(why)})</span>" if why else "")
                rows.append(Item("llm", h.text, sub, badge="EN", label="LLM · slovník",
                                 copy=trans[0] if trans else h.text, copy_alt=h.text, alts=trans))
            else:
                known = con.execute("SELECT 1 FROM term WHERE key = ? AND lang = 0", (_norm(cs),)).fetchone()
                label = "LLM · ve slovníku" if known else "LLM · neověřeno"
                sub = esc(f"{cs} — {why}" if why else cs)
                rows.append(Item("llm", en or cs, sub, badge="EN" if en else "CS", label=label,
                                 copy=cs or en, copy_alt=en or cs, alts=[x for x in (cs, en) if x]))
        if not rows:
            rows = [Item("info", "LLM nic nenavrhl", "", copy="")]
        self.win.list.replace(idx, rows, select=idx)

    def _set_items(self, items):
        self.win.set_items(items)

    def _mt_pairs(self, q: str, hits, force: bool = False) -> list[str]:
        nwords = len(q.split())
        exact = hits and hits[0].tier >= 900
        if not force and nwords < 2 and (exact or len(q) < 3):
            return []
        if has_czech_chars(q):
            return ["cs-en"]
        if hits:
            return ["cs-en"] if hits[0].lang == "cs" else ["en-cs"]
        return ["cs-en", "en-cs"]

    def _mt_done(self, res):
        item = self._mt_item
        if item is None:
            return
        q = self.win.get_text().strip()
        if isinstance(res, Exception) or not res:
            new = None
        else:
            # The wrong direction echoes the input back almost unchanged; keep the one that differs most.
            pair, text = max(res, key=lambda pr: _difference(q, pr[1]))
            new = Item("mt", text, f"<span alpha='60%'>strojový překlad · {pair.replace('-', ' → ')}</span>",
                       badge=pair.split("-")[1].upper(), copy=text, copy_alt=q)
        lst = self.win.list
        idx = lst.index_of(item)
        if idx is None:
            return
        if new is None or _difference(q, new.head) == 0:
            lst.replace(idx, [], select=lst.selected if lst.selected < idx else max(0, lst.selected - 1))
            self._mt_item = None
            return
        lst.replace(idx, [new], select=idx if self._copy_when_ready else None)
        self._mt_item = new
        if self._copy_when_ready:
            self.copy_item(new)

    # --- actions ---------------------------------------------------------------------------
    def copy_item(self, item, alt_text=False):
        if item is None:
            return
        if item.pending:
            self._copy_when_ready = True
            return
        text = item.copy_alt if (alt_text and item.copy_alt) else item.copy
        if not text:
            return
        self._copy(text)

    def copy_nth(self, n: int):
        item = self.win.selected_item()
        if item and n < len(item.alts):
            self._copy(item.alts[n])

    def _copy(self, text: str):
        # Mutter accepts the clipboard only from the focused window: set it before hiding.
        self.win.get_display().get_clipboard().set(text)
        log.info("copied %d chars", len(text))
        self.hide_popup()

    def online_lookup(self):
        if self.win.paragraph:
            self._para_online()
            return
        q = self.win.get_text().strip()
        if not q:
            return
        if not self.cfg["online"]["enabled"]:
            self._insert_top(Item("info", "Online překlad je vypnutý", "zapni ho v <tt>config.toml</tt> ([online] enabled)", copy=""))
            return
        from qtrans import online
        pairs = self._mt_pairs(q, self._last_hits, force=True)
        timeout = self.cfg["online"]["timeout"]
        self._online_item = Item("online", "Ptám se LINDATu…", "<span alpha='60%'>online · ÚFAL MFF UK</span>",
                                 copy="", pending=True)
        self._insert_top(self._online_item)

        def work():
            return [(p, online.translate(q, *p.split("-"), timeout=timeout)) for p in pairs]

        self.ctl.run_io(work, self._online_done)
        log.info("online lookup %s", pairs)

    def _insert_top(self, item):
        self.win.list.insert(0, item)
        self.win.sep.set_visible(True)
        self.win.scroller.get_vadjustment().set_value(0)

    def _online_done(self, res):
        item = getattr(self, "_online_item", None)
        idx = self.win.list.index_of(item) if item else None
        if idx is None:
            return
        q = self.win.get_text().strip()
        if isinstance(res, Exception) or not res:
            log.info("online failed: %s", res)
            new = Item("info", "Online nedostupné", f"<span alpha='60%'>{esc(str(res))[:90]}</span>", copy="",
                       key="online")
        else:
            pair, text = max(res, key=lambda pr: _difference(q, pr[1]))
            new = Item("online", text, f"<span alpha='60%'>online · ÚFAL MFF UK · {pair.replace('-', ' → ')}</span>",
                       badge=pair.split("-")[1].upper(), copy=text, copy_alt=q)
        self.win.list.replace(idx, [new], select=idx)
        self._online_item = new
        if self._copy_when_ready and new.kind == "online":
            self.copy_item(new)

    def _snapshot(self, path: str):
        """Render the popup's content to a PNG (for checking the layout without a screen capture)."""
        content = self.win.get_content()
        w, h = content.get_width(), content.get_height()
        if not w or not h:
            log.warning("snapshot: window not mapped")
            return
        snap = Gtk.Snapshot()
        bg = Gdk.RGBA()
        bg.parse("#2b2b2e" if Adw.StyleManager.get_default().get_dark() else "#fafafb")
        snap.append_color(bg, Graphene.Rect().init(0, 0, w, h))  # the window background is not part of the content
        Gtk.WidgetPaintable.new(content).snapshot(snap, w, h)
        pop = self.win.word_pop
        if pop.get_mapped():  # its own surface: drawn where the compositor puts it, relative to the content
            wx, wy = self.win.get_surface_transform()
            _ok, origin = content.compute_point(self.win, Graphene.Point().init(0, 0))
            px, py = pop.get_surface_transform()
            surf = pop.get_surface()
            snap.save()
            snap.translate(Graphene.Point().init(surf.get_position_x() + px - wx - origin.x,
                                                 surf.get_position_y() + py - wy - origin.y))
            Gtk.WidgetPaintable.new(pop).snapshot(snap, pop.get_width(), pop.get_height())
            snap.restore()
        node = snap.to_node()
        tex = self.win.get_native().get_renderer().render_texture(node, None)
        tex.save_to_png(path)
        log.info("snapshot %dx%d -> %s", w, h, path)


def _hit_item(h, label=None) -> Item:
    trans = [tx for tx, _note in h.translations]
    sub = esc(", ".join(trans))
    notes = [n for _tx, n in h.translations[:1] if n]
    if notes:
        sub += f"  <span alpha='60%'>({esc(notes[0])})</span>"
    return Item("dict", h.text, sub, badge=h.lang.upper(), label=label or h.label or POS_LABEL.get(h.pos, h.pos or ""),
                copy=trans[0] if trans else h.text, copy_alt=h.text, alts=trans, key=f"d:{h.term_id}")



def _natural_height(widget) -> int:
    return widget.measure(Gtk.Orientation.VERTICAL, -1)[1]  # unlike get_height(), includes CSS padding


def _arrow(pair: str) -> str:
    return pair.replace("-", " → ")


def _looks_english(q: str) -> bool:
    from qtrans.search import _cues
    return bool(_cues(_norm(q))[0]) or any(w in q.lower().split() for w in ("the", "a", "for", "of", "someone", "thing"))


def _norm(s: str) -> str:
    from qtrans.normalize import norm
    return norm(s)


def _quiet(fn):
    try:
        fn()
    except Exception as e:  # prewarm is best-effort
        log.info("llm prewarm skipped: %s", e)


def _extension_active(bus) -> bool:
    """Is the quick-translate GNOME Shell extension enabled and running?"""
    try:
        res = bus.call_sync("org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell.Extensions", "GetExtensionInfo",
                            GLib.Variant("(s)", ("quick-translate@que.cz",)), GLib.VariantType("(a{sv})"),
                            Gio.DBusCallFlags.NO_AUTO_START, 500, None)
        return res.unpack()[0].get("state") == 1  # ExtensionState.ACTIVE
    except GLib.Error:
        return False


def _difference(a: str, b: str) -> int:
    from qtrans.normalize import norm
    na, nb = norm(a), norm(b)
    return 0 if na == nb else 1 + abs(len(set(na.split()) ^ set(nb.split())))




def main() -> int:
    app = App()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
