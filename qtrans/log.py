"""Logging to stderr and a small rotating file in $XDG_STATE_HOME/quick-translate/qt.log."""
import logging
import logging.handlers
import os

from qtrans.paths import STATE

_done = False


def setup(debug: bool | None = None) -> logging.Logger:
    global _done
    log = logging.getLogger("qt")
    if _done:
        return log
    _done = True
    if debug is None:
        debug = os.environ.get("QT_DEBUG") == "1"
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)s %(threadName)s %(message)s", "%H:%M:%S")
    err = logging.StreamHandler()
    err.setFormatter(fmt)
    log.addHandler(err)
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(STATE / "qt.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError as e:
        log.warning("cannot open log file: %s", e)
    return log
