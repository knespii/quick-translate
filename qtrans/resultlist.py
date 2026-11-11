"""Animated result list: rows glide to their new place when the order changes, new rows fade in one after
another, removed rows fade out, and the list height follows smoothly (so the window grows and shrinks softly).

Every row has a target position and opacity; a tick callback moves the current values towards the targets
with exponential smoothing each frame, so a new result set can arrive at any time (every keystroke) and the
rows simply retarget from wherever they are.
"""
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import GLib, Graphene, Gsk, Gtk  # noqa: E402

TAU_MOVE = 0.055   # s, time constant of position/height smoothing (~95 % after 0.16 s)
TAU_FADE = 0.045   # s, time constant of fading
STAGGER = 0.022    # s between rows appearing in a fresh list


class _Row:
    __slots__ = ("key", "item", "widget", "y", "ty", "op", "top", "delay", "h")

    def __init__(self, key, item, widget, y, ty, op, top, delay=0.0, h=0):
        self.key, self.item, self.widget = key, item, widget
        self.y, self.ty, self.op, self.top, self.delay, self.h = y, ty, op, top, delay, h


def _signature(item):
    return (item.kind, item.head, item.sub, item.badge, item.label, item.pending)


class ResultList(Gtk.Widget):
    __gtype_name__ = "QtResultList"

    def __init__(self, row_h: int, make_row, on_activate, height_of=None, selectable=None):
        """height_of(item) -> row height (default row_h); selectable(item) -> False for rows a click must not
        select (section headers)."""
        super().__init__()
        self.row_h = row_h
        self._make_row = make_row
        self._on_activate = on_activate
        self._height_of = height_of or (lambda _item: row_h)
        self.selectable = selectable or (lambda _item: True)
        self._tops = []
        self.items = []
        self._keys = []
        self._rows: dict[str, _Row] = {}
        self._dying: list[_Row] = []
        self.selected = -1
        self._h = 0.0
        self._th = 0.0
        self._tick_id = 0
        self._last = None
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("qt-list")

    # --- public API -----------------------------------------------------------------------------
    def set_items(self, items, select: int = 0):
        rows: dict[str, _Row] = {}
        keys = []
        fresh = not self._rows
        appearing = 0
        ty = 0
        tops = []
        for item in items:
            key = item.key
            while key in rows:  # keep keys unique
                key += "+"
            keys.append(key)
            h = self._height_of(item)
            tops.append(ty)
            r = self._rows.get(key) or self._revive(key)
            if r is None:
                # New row: slides up a little into its place; a fresh list cascades in row by row.
                r = _Row(key, item, self._build(key, item), ty + (6 if fresh else h * 0.35), ty,
                         0.0, 1.0, appearing * STAGGER, h)
                appearing += 1
            else:
                if _signature(r.item) != _signature(item):
                    r.widget.unparent()
                    r.widget = self._build(key, item)
                    r.op = min(r.op, 0.35)  # quick cross-fade of the changed content (pending -> result)
                r.item, r.ty, r.top, r.h = item, ty, 1.0, h
            rows[key] = r
            ty += h
        for key, r in self._rows.items():
            if key not in rows:
                r.top, r.delay = 0.0, 0.0
                self._dying.append(r)
        self._rows, self._keys, self.items, self._tops = rows, keys, list(items), tops
        self._th = ty
        self.selected = min(select, len(items) - 1) if items else -1
        self._mark_selected()
        self._kick()

    def index_of(self, item):
        for i, it in enumerate(self.items):
            if it is item:
                return i
        return None

    def replace(self, index: int, new_items: list, select: int | None = None):
        items = self.items[:index] + list(new_items) + self.items[index + 1:]
        keep = self.selected if select is None else select
        self.set_items(items, keep if keep >= 0 else 0)

    def insert(self, index: int, item, select: bool = True):
        items = self.items[:index] + [item] + self.items[index:]
        self.set_items(items, index if select else (self.selected + (1 if self.selected >= index else 0)))

    def row_span(self, index: int) -> tuple[int, int]:
        """(top, bottom) of row index in the list's coordinates (target positions)."""
        top = self._tops[index]
        return top, top + self._height_of(self.items[index])

    def selected_item(self):
        if 0 <= self.selected < len(self.items):
            return self.items[self.selected]
        return self.items[0] if self.items else None

    def select(self, index: int):
        if self.items:
            self.selected = max(0, min(len(self.items) - 1, index))
            self._mark_selected()

    # --- internals ------------------------------------------------------------------------------
    def _revive(self, key):
        for r in self._dying:
            if r.key == key:
                self._dying.remove(r)
                return r
        return None

    def _build(self, key, item):
        w = self._make_row(item)
        w.set_parent(self)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_a, k=key: self._clicked(k))
        w.add_controller(click)
        return w

    def _clicked(self, key):
        if key in self._keys:
            if not self.selectable(self.items[self._keys.index(key)]):
                return
            self.select(self._keys.index(key))
            self._on_activate(self.selected_item())

    def _mark_selected(self):
        for i, key in enumerate(self._keys):
            w = self._rows[key].widget
            if i == self.selected:
                w.add_css_class("selected")
            else:
                w.remove_css_class("selected")

    def _all_rows(self):
        return list(self._rows.values()) + self._dying

    def _kick(self):
        if not Gtk.Settings.get_default().get_property("gtk-enable-animations") or not self.get_mapped():
            self._finish()
            return
        if not self._tick_id:
            self._last = None
            self._tick_id = self.add_tick_callback(self._tick)

    def _finish(self):
        for r in self._all_rows():
            r.y, r.op, r.delay = r.ty, r.top, 0.0
        for r in self._dying:
            r.widget.unparent()
        self._dying.clear()
        self._h = self._th
        self.queue_resize()

    def _tick(self, _widget, clock):
        now = clock.get_frame_time() / 1e6
        dt = 1 / 60 if self._last is None else min(0.05, max(0.0, now - self._last))
        self._last = now
        a_move = 1 - math.exp(-dt / TAU_MOVE)
        a_fade = 1 - math.exp(-dt / TAU_FADE)
        busy = False
        for r in self._all_rows():
            if r.delay > 0:
                r.delay -= dt
                busy = True
                continue
            r.y += (r.ty - r.y) * a_move
            r.op += (r.top - r.op) * a_fade
            if abs(r.ty - r.y) < 0.4:
                r.y = r.ty
            else:
                busy = True
            if abs(r.top - r.op) < 0.01:
                r.op = r.top
            else:
                busy = True
        for r in [r for r in self._dying if r.op <= 0.0]:
            r.widget.unparent()
            self._dying.remove(r)
        old_h = self._h
        self._h += (self._th - self._h) * a_move
        if abs(self._th - self._h) < 0.5:
            self._h = self._th
        else:
            busy = True
        if round(old_h) != round(self._h):
            self.queue_resize()
        else:
            self.queue_allocate()
        self.queue_draw()
        if not busy:
            self._tick_id = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # --- GtkWidget vfuncs -------------------------------------------------------------------------
    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            h = math.ceil(self._h)
            return h, h, -1, -1
        return 0, 0, -1, -1

    def do_size_allocate(self, width, height, baseline):
        for r in self._all_rows():
            t = Gsk.Transform.new().translate(Graphene.Point().init(0, r.y))
            r.widget.allocate(width, r.h, -1, t)

    def do_snapshot(self, snapshot):
        for r in self._all_rows():
            if r.op <= 0.004:
                continue
            if r.op < 0.999:
                snapshot.push_opacity(r.op)
                self.snapshot_child(r.widget, snapshot)
                snapshot.pop()
            else:
                self.snapshot_child(r.widget, snapshot)

    def do_unmap(self):
        Gtk.Widget.do_unmap(self)
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        self._finish()

    def do_dispose(self):
        for r in self._all_rows():
            r.widget.unparent()
        self._rows, self._dying = {}, []
        Gtk.Widget.do_dispose(self)
