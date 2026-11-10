"""Async plumbing between the GTK main loop and background work (MT, online, LLM). Depends on GLib only.

Every query bumps a generation counter. Results from background work are delivered on the main loop
only if their generation is still current, so stale answers never reach the window.
"""
import concurrent.futures
import logging
import threading

from gi.repository import GLib

log = logging.getLogger("qt")


class MTWorker(threading.Thread):
    """One daemon thread that owns the MT engine and translates only the latest request."""

    def __init__(self, engine_factory):
        super().__init__(name="mt", daemon=True)
        self._factory = engine_factory
        self._cond = threading.Condition()
        self._pending = None
        self.engine = None
        self.error = None
        self.loaded = threading.Event()

    def run(self):
        try:
            self.engine = self._factory()
        except Exception as e:  # missing models or fxtranslate not installed
            self.error = e
            log.warning("MT disabled: %s", e)
        finally:
            self.loaded.set()
        while True:
            with self._cond:
                while self._pending is None:
                    self._cond.wait()
                job, self._pending = self._pending, None
            text, pairs, done = job
            if self.engine is None:
                done(None)
                continue
            try:
                out = [(p, self.engine.translate(text, p)) for p in pairs if p in self.engine.tr]
            except Exception as e:
                log.exception("MT failed")
                out = e
            done(out)

    @property
    def available(self) -> bool:
        return self.engine is not None

    def submit(self, text: str, pairs: list[str], done) -> None:
        """done(result) is called from the worker thread."""
        with self._cond:
            self._pending = (text, pairs, done)
            self._cond.notify()


class Controller:
    def __init__(self, mt: MTWorker | None = None, debounce_ms: int = 120, io_workers: int = 2):
        self.gen = 0
        self.mt = mt
        self.debounce_ms = debounce_ms
        self._timer = 0
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=io_workers, thread_name_prefix="io")

    def bump(self) -> int:
        """Start a new generation: cancels the pending MT debounce and orphans running work."""
        self.gen += 1
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        return self.gen

    def _deliver(self, gen, callback, payload):
        if gen == self.gen:
            callback(payload)
        return GLib.SOURCE_REMOVE

    def schedule_mt(self, text: str, pairs: list[str], callback) -> bool:
        """Translate after the debounce; callback(result) runs on the main loop if still current."""
        if self.mt is None or (self.mt.loaded.is_set() and not self.mt.available):
            return False
        gen = self.gen
        if self._timer:
            GLib.source_remove(self._timer)

        def fire():
            self._timer = 0
            self.mt.submit(text, pairs, lambda res: GLib.idle_add(self._deliver, gen, callback, res))
            return GLib.SOURCE_REMOVE

        self._timer = GLib.timeout_add(self.debounce_ms, fire)
        return True

    def run_io(self, fn, callback) -> None:
        """Run fn() in the IO pool; callback(result_or_exception) on the main loop if still current."""
        gen = self.gen

        def done(fut):
            try:
                res = fut.result()
            except Exception as e:
                res = e
            GLib.idle_add(self._deliver, gen, callback, res)

        self._pool.submit(fn).add_done_callback(done)
