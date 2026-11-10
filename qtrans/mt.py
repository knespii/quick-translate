"""Offline phrase translation with the Firefox (Bergamot) models through fxtranslate.

A Translator is bound to the thread that created it, so MTEngine must be created and used in one thread.
Models are read from data/models only; Translator.load() is never called (it goes to the network).
"""
import logging
import time
from pathlib import Path

from qtrans.paths import MODELS

log = logging.getLogger("qt")


def _pair_files(d: Path) -> dict[str, Path]:
    files = {}
    for p in d.iterdir():
        if p.name.startswith(".") or p.is_dir():
            continue
        files[p.name.split(".")[0]] = p  # model / vocab / lex
    return files


class MTEngine:
    def __init__(self, models_dir: Path = MODELS):
        from fxtranslate import Translator  # imported here so the app runs without fxtranslate

        self.tr = {}
        for pair in ("cs-en", "en-cs"):
            d = models_dir / pair
            if not d.is_dir():
                continue
            f = _pair_files(d)
            t0 = time.perf_counter()
            vocab = f["vocab"].read_bytes()
            self.tr[pair] = Translator(f["model"].read_bytes(), vocab, vocab, f["lex"].read_bytes() if "lex" in f else None)
            log.info("MT %s loaded in %.0f ms (backend %s)", pair, (time.perf_counter() - t0) * 1000, self.tr[pair].backend())
        if not self.tr:
            raise FileNotFoundError(f"no translation models in {models_dir}")

    def translate(self, text: str, pair: str) -> str:
        tr = self.tr[pair]
        if "\n" in text or len(text) > 200:
            return tr.translate_long(text).strip()
        return tr.translate(text).strip()
