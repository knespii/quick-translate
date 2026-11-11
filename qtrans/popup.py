"""The popup window: the input, the result list (word mode), the paragraph page with its word popover, the footer,
the open/close animation and the debug snapshot. It shows what the App (app.py) tells it and hands keys back to it."""
import logging
import math
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from qtrans.autodiac import has_czech_letter, written_with_diacritics  # noqa: E402
from qtrans.items import Item, esc  # noqa: E402
from qtrans.resultlist import TAU_MOVE, ResultList  # noqa: E402

ROW_H = 54
HEAD_H = 30            # a section header in the history list
ENTRY_H = 58
INPUT_LINES = 6        # the input grows up to this many lines, then scrolls
PARA_SCREEN = 0.6      # paragraph mode: the window grows up to this share of the monitor height
BACK = "<b>Alt+←</b> zpět"
FOOTER = ("<b>↵</b> kopírovat", "<b>⇧↵</b> heslo", "<b>Alt+1…9</b> n-tý", "<b>Alt+L</b> online", "<b>Alt+E</b> LLM",
          "<b>?</b>popis", "<b>Tab</b> historie", "<b>Esc</b>")
FOOTER_PARA = ("<b>↵</b> kopírovat překlad", "<b>⇧↵</b> nový řádek", "<b>⌃↑↓</b> originál", "<b>Alt+L</b> online",
               "<b>Alt+E</b> LLM", "<b>Esc</b>")
FOOTER_HISTORY = ("<b>↑↓</b> výběr", "<b>↵</b> kopírovat", "<b>⇧↵</b> heslo", "<b>Alt+1…9</b> n-tý",
                  "<b>Tab</b> zpět", "<b>Esc</b>")
WARM_W, WARM_H = 320, 200  # one panel of the warm-up drawing (Popup._warm_up_node)
OPEN_MS, CLOSE_MS = 170, 120  # (the GNOME extension's glass uses the same durations)
MODE_MS = 160          # cross-fade between the word list and the paragraph block

log = logging.getLogger("qt")


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
    """app is the App (app.py): row clicks, word clicks and keys go to it."""

    def __init__(self, app, cfg):
        super().__init__(application=app, title="Quick Translate")
        self.app_ref = app
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
        # Automatic diacritics (autodiac.AutoDiacritics, set by the App; None = off). _typed: (start, end) of
        # text the user typed or pasted since the last query; our own edits (_quiet) and undo/redo are not typing.
        self.diacritics = None
        self.user_diacritics = False  # the user writes with diacritics: leave their ASCII words alone
        self._typed = []
        self._quiet = False
        self.buffer.connect_after("insert-text", self._after_insert)
        for sig in ("undo", "redo"):
            self.buffer.connect(sig, lambda _b: setattr(self, "_quiet", True))
            self.buffer.connect_after(sig, lambda _b: setattr(self, "_quiet", False))

        self.list = ResultList(ROW_H, self._make_row, lambda item: self.app_ref.copy_item(item))
        # EXTERNAL: no scrollbar (its minimum height would leave a gap under an empty list); the list still
        # scrolls with the wheel and with ↑/↓.
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           vscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        self.scroller.set_child(self.list)
        self.scroller.set_propagate_natural_height(True)
        self.scroller.set_max_content_height(cfg["ui"]["rows"] * ROW_H)
        # The history (Tab): its own list, so the results underneath stay as they were for the way back.
        self.history = False
        self.hist_list = ResultList(ROW_H, self._make_row, lambda item: self.app_ref.copy_item(item),
                                    height_of=lambda it: HEAD_H if it.kind == "head" else ROW_H,
                                    selectable=lambda it: it.kind not in ("head", "info"))
        self.hist_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                vscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        self.hist_scroller.set_child(self.hist_list)
        self.hist_scroller.set_propagate_natural_height(True)
        self._back = False

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
        self.word_pop_ctx = None  # the "Podle smyslu věty" section (LLM options), filled when they arrive
        self.word_pop_ctx_buttons = []
        self._pop_span = None
        self.para_box = SmoothHeight(page)

        self.stack = Gtk.Stack(vhomogeneous=False, interpolate_size=True, transition_duration=MODE_MS,
                               transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_child(self.scroller)
        self.stack.add_child(self.para_box)
        self.stack.add_child(self.hist_scroller)
        self.stack.connect("notify::transition-running", self._on_mode_switched)
        box.append(self.stack)

        self.footer = Gtk.Label(xalign=0.5, ellipsize=Pango.EllipsizeMode.END)
        self.footer.add_css_class("qt-footer")
        self._update_footer()
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
        self._open_tick = 0
        self.connect("map", self._on_map)

    # --- warm-up ----------------------------------------------------------------------------------
    def warm_up(self):
        """Do the first opening's one-off work now, while nobody waits (the app starts at login): the window's
        surface and GPU renderer, the styles and fonts of the layout, and the renderer's shaders and glyphs for
        the kinds of drawing the popup does. Otherwise the first Super+T stalls for a few hundred ms."""
        if self.get_realized():
            return
        t = time.perf_counter()
        self.realize()
        for orientation in (Gtk.Orientation.HORIZONTAL, Gtk.Orientation.VERTICAL):
            self.measure(orientation, -1)
        renderer = self.get_renderer()
        if renderer is not None:
            # (An explicit, whole-pixel area: GSK aborts the process on an empty texture.)
            renderer.render_texture(self._warm_up_node(), Graphene.Rect().init(0, 0, 3 * WARM_W, WARM_H))
        log.info("warm-up in %.0f ms", (time.perf_counter() - t) * 1000)

    def _warm_up_node(self):
        """A small drawing with what the popup draws: rounded cards, borders, shadows, text in its sizes and
        styles, opacity and the opening's transform; each unclipped, clipped and clipped round (the renderer
        has a shader variant for each)."""
        def rgba(spec):
            c = Gdk.RGBA()
            c.parse(spec)
            return c

        w, h = WARM_W, WARM_H
        snap = Gtk.Snapshot()
        for i, clip in enumerate((None, "rect", "rounded")):
            x = i * w
            rect = Graphene.Rect().init(x, 0, w, h)
            card = Gsk.RoundedRect().init_from_rect(Graphene.Rect().init(x + 8, 8, w - 20, h - 20), 16)
            if clip == "rect":
                snap.push_clip(Graphene.Rect().init(x + 2, 2, w - 4, h - 4))
            elif clip == "rounded":
                snap.push_rounded_clip(Gsk.RoundedRect().init_from_rect(Graphene.Rect().init(x + 2, 2, w - 4, h - 4), 12))
            snap.push_opacity(0.5)
            snap.save()
            snap.translate(Graphene.Point().init(x + w / 2, 0))
            snap.scale(0.98, 0.98)
            snap.translate(Graphene.Point().init(-x - w / 2, -4))
            snap.append_outset_shadow(card, rgba("rgba(0,0,0,0.28)"), 0, 4, 0, 16)
            snap.append_color(rgba("rgba(36,36,40,0.6)"), rect)
            snap.push_rounded_clip(card)
            snap.append_color(rgba("rgba(80,80,90,0.6)"), rect)
            snap.append_inset_shadow(card, rgba("rgba(255,255,255,0.1)"), 0, 1, 0, 2)
            snap.pop()
            snap.append_border(card, [1, 1, 1, 1], [rgba("rgba(255,255,255,0.12)")] * 4)
            y = 12
            for size, style in ((20, ""), (15, "bold"), (13, "italic"), (12, ""), (11, "bold"), (10, "bold"),
                                (16, "")):
                layout = self.create_pango_layout("Přelož… česky nebo anglicky ÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ áčďéěíňóřšťúůýž → · ↵")
                layout.set_font_description(Pango.FontDescription.from_string(f"{style} {size}px"))
                snap.save()
                snap.translate(Graphene.Point().init(x + 16, y))
                snap.append_layout(layout, rgba("white"))
                snap.restore()
                y += size + 6
            snap.restore()
            snap.pop()
            if clip:
                snap.pop()
        return snap.to_node()

    # --- open / close animation ------------------------------------------------------------------
    def _on_map(self, _w):
        # The first frame is drawn transparent; the animation starts with the next one, which the compositor asks
        # for once the window is on screen. (After a slow first map it would be over before anything was seen.)
        self._anim_value = 0.0
        ticks = [0]

        def tick(_w, _clock):
            ticks[0] += 1
            if ticks[0] < 2:
                return GLib.SOURCE_CONTINUE
            self._open_tick = 0
            self.animate(True)
            return GLib.SOURCE_REMOVE
        self._cancel_open_tick()
        self._open_tick = self.add_tick_callback(tick)

    def _cancel_open_tick(self):
        if self._open_tick:
            self.remove_tick_callback(self._open_tick)
            self._open_tick = 0

    def animate(self, show: bool, done=None):
        self._cancel_open_tick()
        if self._anim is not None:
            self._anim.pause()
        target = Adw.CallbackAnimationTarget.new(self._set_anim_value)
        a = Adw.TimedAnimation.new(self, self._anim_value, 1.0 if show else 0.0, OPEN_MS if show else CLOSE_MS, target)
        a.set_easing(Adw.Easing.EASE_OUT_CUBIC if show else Adw.Easing.EASE_IN_CUBIC)
        if done is not None:
            a.connect("done", lambda *_: done())
        self._anim = a
        a.play()
        if show and log.isEnabledFor(logging.DEBUG):
            self._trace_frames()

    def _trace_frames(self):
        """Debug: the app's frame times while it opens (a long frame is a visible stutter)."""
        t0 = time.monotonic()
        gaps, last = [], [t0]

        def tick(_w, _clock):
            now = time.monotonic()
            gaps.append(round((now - last[0]) * 1000))
            last[0] = now
            if now - t0 < 0.6:
                return GLib.SOURCE_CONTINUE
            log.debug("frames: first %d ms, max %d ms, %d frames: %s", gaps[0], max(gaps), len(gaps),
                      " ".join(map(str, gaps)))
            return GLib.SOURCE_REMOVE
        self.add_tick_callback(tick)

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
        empty = self.buffer.get_char_count() == 0
        self.placeholder.set_visible(empty)
        if empty:
            self.user_diacritics = False  # a fresh start: the next text may be typed without diacritics
        # Typing over a selection (or set_text) is a delete and an insert: run the query once, before the redraw.
        if not self._changed_idle:
            self._changed_idle = GLib.idle_add(self._emit_changed, priority=GLib.PRIORITY_HIGH_IDLE)

    def _emit_changed(self):
        # Diacritics first, still inside this idle: their edit does not schedule a second query.
        self._restore_typed()
        self._changed_idle = 0
        if self.on_changed is not None:
            self.on_changed(self.get_text())
        return GLib.SOURCE_REMOVE

    def _after_insert(self, _buf, location, text, _length):
        if self._quiet:
            return
        if has_czech_letter(text):
            self.user_diacritics = True
        end = location.get_offset()  # (after the default handler: the end of the inserted text)
        self._typed.append((end - len(text), end))

    def _restore_typed(self):
        typed, self._typed = self._typed, []
        if self.diacritics is None or self.user_diacritics:
            return
        for start, end in typed:
            text = self.get_text()
            if end - start == 1:
                edits = self.diacritics.typed(text, end)
            else:  # pasted
                edits = self.diacritics.whole(text, start, end)
            self._apply_edits(edits)

    def _apply_edits(self, edits):
        """Replace (start, end, new) spans of the input as one undoable step, keeping the cursor and selection."""
        if not edits:
            return
        b = self.buffer
        marks = [b.get_iter_at_mark(m).get_offset() for m in (b.get_insert(), b.get_selection_bound())]
        self._quiet = True
        b.begin_user_action()
        for start, end, new in sorted(edits, reverse=True):
            b.delete(b.get_iter_at_offset(start), b.get_iter_at_offset(end))
            b.insert(b.get_iter_at_offset(start), new)
            marks = [m + len(new) - (end - start) if m >= end else m for m in marks]
        b.end_user_action()
        self._quiet = False
        b.select_range(b.get_iter_at_offset(marks[0]), b.get_iter_at_offset(marks[1]))
        log.debug("diacritics: %d words restored", len(edits))

    def get_text(self) -> str:
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)

    def set_text(self, text: str, restore: bool = False):
        """restore: a prefilled or scripted text, restored whole (Ctrl+Z gets back the text as it was)."""
        self._quiet = True
        self.buffer.set_text(text)  # (irreversible for the undo history: nothing to undo before it)
        self._quiet = False
        self._typed = []
        self.user_diacritics = written_with_diacritics(text)
        if restore and self.diacritics is not None and not self.user_diacritics:
            self._apply_edits(self.diacritics.whole(text))

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
        self._back = False
        self.stack.set_visible_child(self.para_box if para else self.scroller)
        self._update_footer()
        if para:
            self.sep.set_visible(True)

    def set_back(self, on: bool):
        """Is there something for Alt+← to go back to (a row added by Alt+L/E, another paragraph version)?"""
        if on != self._back:
            self._back = on
            self._update_footer()

    def _update_footer(self):
        if self.history:
            parts = FOOTER_HISTORY
        else:
            parts = FOOTER_PARA if self.paragraph else FOOTER
            if self._back:
                parts = parts[:-1] + (BACK, parts[-1])
        self.footer.set_markup("   ".join(parts))

    # --- history (Tab) -----------------------------------------------------------------------------
    def show_history(self, items):
        self.hide_word_pop()
        self.history = True
        first = next((i for i, it in enumerate(items) if self.hist_list.selectable(it)), -1)
        self.hist_list.set_items(items, first)
        self.hist_scroller.get_vadjustment().set_value(0)
        self.stack.set_visible_child(self.hist_scroller)
        self.sep.set_visible(True)
        self._update_footer()

    def hide_history(self):
        if not self.history:
            return
        self.history = False
        self.stack.set_visible_child(self.para_box if self.paragraph else self.scroller)
        self.sep.set_visible(self.paragraph or bool(self.list.items))
        self._update_footer()

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

    def show_word_pop(self, start: int, end: int, groups, on_pick, context: bool = False):
        """Point a popover at text[start:end] with groups [(head, label, alternatives)]; on_pick(n) gets the
        index of the alternative clicked (counted over all groups). context: a "Podle smyslu věty" section with
        a spinner, filled later by set_word_pop_context."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.word_pop_buttons = []
        self.word_pop_ctx_buttons = []
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
                              ("lepší celý překlad: Alt+L online, Alt+E LLM", "qt-label")):
                lab = Gtk.Label(label=text, xalign=0)
                lab.add_css_class(cls)
                box.append(lab)
        self.word_pop_ctx = None
        if context:
            self.word_pop_ctx = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            top = Gtk.Box(spacing=8)
            top.add_css_class("qt-word-pop-head")
            h = Gtk.Label(xalign=0)
            h.set_markup("<b>Podle smyslu věty</b>")
            h.add_css_class("qt-head")
            top.append(h)
            lab = Gtk.Label(label="LLM", xalign=0)
            lab.add_css_class("qt-label")
            top.append(lab)
            top.append(Gtk.Spinner(spinning=True))
            self.word_pop_ctx.append(top)
            box.append(self.word_pop_ctx)
        self.word_pop.set_child(box)
        self._pop_span = (start, end)
        self._place_word_pop()
        self.word_pop.popup()

    def set_word_pop_context(self, options, on_pick):
        """Fill the "Podle smyslu věty" section: options [(word, note, sentence)], on_pick(n); None or [] drops it."""
        sec = self.word_pop_ctx
        if sec is None or not self.word_pop.get_visible():
            return
        if not options:
            sec.get_parent().remove(sec)
            self.word_pop_ctx = None
        else:
            top = sec.get_first_child()
            top.remove(top.get_last_child())  # the spinner
            for n, (word, note, sentence) in enumerate(options):
                lab = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=56)
                lab.set_markup(f"{esc(word)}" + (f"  <span alpha='55%'>{esc(note)}</span>" if note else ""))
                b = Gtk.Button(child=lab, focus_on_click=False, can_focus=False, tooltip_text=sentence)
                b.add_css_class("flat")
                b.add_css_class("qt-chip")
                b.add_css_class("qt-ctx-option")
                b.connect("clicked", lambda _b, n=n: on_pick(n))
                sec.append(b)
                self.word_pop_ctx_buttons.append(b)
        self._place_word_pop()

    def _place_word_pop(self):
        start, end = self._pop_span
        box = self.word_pop.get_child()
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
        if item.kind == "head":  # a section title of the history
            row.set_valign(Gtk.Align.END)
            row.append(Gtk.Label(label=item.head, xalign=0, css_classes=["qt-section"]))
            return row
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

    def insert_top(self, item):
        self.list.insert(0, item)
        self.sep.set_visible(True)
        self.scroller.get_vadjustment().set_value(0)

    def selected_item(self):
        if self.history:
            item = self.hist_list.selected_item()
            return item if item is not None and self.hist_list.selectable(item) else None
        return None if self.paragraph else self.list.selected_item()

    def move(self, delta: int):
        lst, scroller = (self.hist_list, self.hist_scroller) if self.history else (self.list, self.scroller)
        n = len(lst.items)
        if not n:
            return
        i = max(0, min(n - 1, lst.selected + delta))
        step = 1 if delta > 0 else -1
        while 0 <= i < n and not lst.selectable(lst.items[i]):  # over section headers
            i += step
        if not 0 <= i < n:
            return
        lst.select(i)
        # Keep the selected row visible (with its section header) without moving keyboard focus out of the entry.
        adj = scroller.get_vadjustment()
        top, bottom = lst.row_span(i)
        if i > 0 and not lst.selectable(lst.items[i - 1]):
            top = lst.row_span(i - 1)[0]
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
        left_right = {Gdk.KEY_Left: -1, Gdk.KEY_KP_Left: -1, Gdk.KEY_Right: 1, Gdk.KEY_KP_Right: 1}
        enter = (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter)
        if self.history and alt and (keyval in left_right or keyval in (Gdk.KEY_l, Gdk.KEY_L, Gdk.KEY_e, Gdk.KEY_E)):
            self.hide_history()  # these act on the results underneath
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_KP_Tab) and not (ctrl or alt):
            if not self.word_pop.get_visible():
                app.toggle_history()
        elif self.history and keyval == Gdk.KEY_Escape:
            self.hide_history()
        elif self.history and keyval in enter:
            app.copy_item(self.selected_item(), alt_text=shift)
        elif self.history and keyval in up_down + (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            self.move({Gdk.KEY_Page_Down: 5, Gdk.KEY_Page_Up: -5}.get(keyval, 1 if keyval in up_down[:2] else -1))
        elif self.history and alt and Gdk.KEY_1 <= keyval <= Gdk.KEY_9:
            app.copy_nth(keyval - Gdk.KEY_1)
        elif keyval == Gdk.KEY_Escape:
            if not self.hide_word_pop():
                app.hide_popup()
        elif alt and not ctrl and keyval in left_right:
            return app.go_back(left_right[keyval])  # False: nothing to go back to, the key goes on to the input
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
        elif keyval in enter:
            if para and shift:
                self.insert_newline()
            elif para:
                app.copy_paragraph()
            else:
                app.copy_item(self.selected_item(), alt_text=shift)
        elif alt and not ctrl and keyval in (Gdk.KEY_l, Gdk.KEY_L):
            app.online_lookup()
        elif alt and not ctrl and keyval in (Gdk.KEY_e, Gdk.KEY_E):
            app.ask_llm()
        elif para and ctrl and not shift and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            return app.undo_replacement()  # nothing to undo: the input undoes typing
        elif alt and not para and Gdk.KEY_1 <= keyval <= Gdk.KEY_9:
            app.copy_nth(keyval - Gdk.KEY_1)
        else:
            return False
        return True

    # --- debug snapshot --------------------------------------------------------------------------
    def save_png(self, path: str):
        """Render the popup's content to a PNG (for checking the layout without a screen capture)."""
        content = self.get_content()
        w, h = content.get_width(), content.get_height()
        if not w or not h:
            log.warning("snapshot: window not mapped")
            return
        snap = Gtk.Snapshot()
        bg = Gdk.RGBA()
        bg.parse("#2b2b2e" if Adw.StyleManager.get_default().get_dark() else "#fafafb")
        snap.append_color(bg, Graphene.Rect().init(0, 0, w, h))  # the window background is not part of the content
        Gtk.WidgetPaintable.new(content).snapshot(snap, w, h)
        pop = self.word_pop
        if pop.get_mapped():  # its own surface: drawn where the compositor puts it, relative to the content
            wx, wy = self.get_surface_transform()
            _ok, origin = content.compute_point(self, Graphene.Point().init(0, 0))
            px, py = pop.get_surface_transform()
            surf = pop.get_surface()
            snap.save()
            snap.translate(Graphene.Point().init(surf.get_position_x() + px - wx - origin.x,
                                                 surf.get_position_y() + py - wy - origin.y))
            Gtk.WidgetPaintable.new(pop).snapshot(snap, pop.get_width(), pop.get_height())
            snap.restore()
        node = snap.to_node()
        tex = self.get_native().get_renderer().render_texture(node, None)
        tex.save_to_png(path)
        log.info("snapshot %dx%d -> %s", w, h, path)


def _natural_height(widget) -> int:
    return widget.measure(Gtk.Orientation.VERTICAL, -1)[1]  # unlike get_height(), includes CSS padding
