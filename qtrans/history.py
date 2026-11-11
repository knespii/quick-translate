"""Tiny persistent history of translated words, so the UI can offer the last few per language. Stays local."""
import json
import logging
import os
import time
from pathlib import Path

from qtrans.paths import STATE

LANGS = ("cs", "en")
MAX_WORDS = 4  # longer input is a sentence, not a word worth remembering

log = logging.getLogger("qt")


def _key(text: str) -> str:
    # Casefold + whitespace only: keep diacritics, 'být' and 'byt' are different words.
    return " ".join(text.casefold().split())


class History:
    def __init__(self, path: Path = STATE / "history.json", limit: int = 100, enabled: bool = True):
        self.path = Path(path)
        self.limit = limit
        self.enabled = enabled
        self._entries = self._load() if enabled else []

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            log.warning("history %s ignored: %s", self.path, e)
            return []
        if not isinstance(data, list):
            return []
        return [e for e in data if self._valid(e)][: self.limit]

    @staticmethod
    def _valid(e) -> bool:
        # (A hand-edited file must not crash the Tab view: it shows translations[0].)
        tr = e.get("translations") if isinstance(e, dict) else None
        return (isinstance(e, dict) and e.get("lang") in LANGS and isinstance(e.get("text"), str)
                and isinstance(tr, list) and bool(tr) and all(isinstance(t, str) for t in tr)
                and isinstance(e.get("at"), (int, float)))

    def _save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self._entries, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("history %s not saved: %s", self.path, e)

    def record(self, lang: str, text: str, translations: list[str]) -> None:
        text = " ".join(text.split())
        translations = [t for t in translations if t and t.strip()]
        if not self.enabled or lang not in LANGS or not text or not translations:
            return
        if len(text.split()) > MAX_WORDS:
            return
        key = _key(text)
        rest = [e for e in self._entries if not (e["lang"] == lang and _key(e["text"]) == key)]
        entry = {"lang": lang, "text": text, "translations": list(translations), "at": time.time()}
        self._entries = [entry, *rest][: self.limit]
        self._save()

    def recent(self, lang: str, n: int = 3) -> list[dict]:
        out = [e for e in self._entries if e["lang"] == lang][:n]
        return [{"text": e["text"], "translations": list(e["translations"]), "at": e["at"]} for e in out]

    def clear(self) -> None:
        self._entries = []
        if self.enabled:
            self._save()
