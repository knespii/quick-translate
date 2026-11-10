"""Headless tests of the generation/debounce logic with a fake MT engine."""
import time

from gi.repository import GLib

from qtrans.controller import Controller, MTWorker


class FakeEngine:
    tr = {"cs-en": None, "en-cs": None}

    def translate(self, text, pair):
        time.sleep(0.01)
        return f"{pair}:{text}"


def spin(seconds):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.002)


def make():
    w = MTWorker(FakeEngine)
    w.start()
    w.loaded.wait(2)
    return Controller(w, debounce_ms=30)


def test_only_latest_generation_is_delivered():
    c = make()
    got = []
    for q in ["d", "da", "dat", "dat si", "dat si pozor"]:
        c.bump()
        c.schedule_mt(q, ["cs-en"], got.append)
        spin(0.005)
    spin(0.3)
    assert got == [[("cs-en", "cs-en:dat si pozor")]]


def test_nothing_delivered_after_hide():
    c = make()
    got = []
    c.bump()
    c.schedule_mt("pes", ["cs-en"], got.append)
    c.bump()  # window hidden
    spin(0.3)
    assert got == []


def test_io_result_and_stale_drop():
    c = make()
    got = []
    c.bump()
    c.run_io(lambda: (time.sleep(0.05), "old")[1], got.append)
    c.bump()
    c.run_io(lambda: "new", got.append)
    spin(0.3)
    assert got == ["new"]


def test_missing_engine_disables_mt():
    def broken():
        raise FileNotFoundError("no models")

    w = MTWorker(broken)
    w.start()
    w.loaded.wait(2)
    c = Controller(w)
    assert c.schedule_mt("x", ["cs-en"], print) is False
