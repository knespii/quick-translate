"""Local LLM (ollama): proposes words for a description (describe-a-word mode) and translates paragraphs.

Every Czech word it returns is checked against the dictionary by the caller; the LLM never has the last word.
"""
import json
import re
import urllib.error
import urllib.request

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {"en": {"type": "string"}, "cs": {"type": "string"}, "why": {"type": "string"}},
                "required": ["en", "cs"],
            },
        }
    },
    "required": ["candidates"],
}

SYSTEM = (
    "You are a bilingual Czech-English lexicographer. A Czech speaker has a word on the tip of their tongue and "
    "describes it, in Czech or in English. Name the word they most likely mean. Prefer common, everyday words. "
    "Give up to 5 candidates, best first. For each: the English word, the Czech word (dictionary form), and a very "
    "short reason in Czech (at most 8 words). Dictionary candidates may be given as hints; use them only if they fit.")


TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {"translation": {"type": "string"}},
    "required": ["translation"],
}

LANG_NAMES = {"cs": "Czech", "en": "English"}

TRANSLATE_SYSTEM = (
    "You are a professional {src}-{tgt} translator. Translate the user's text from {src} into {tgt}. "
    "Be faithful: keep every piece of information, do not add, explain or summarize. Make it sound natural, as a "
    "native {tgt} speaker would write it, not word for word. Keep names, numbers, keyboard shortcuts and code "
    "unchanged. Keep the paragraph breaks (blank lines) exactly as in the original. Return only the translation.")


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, url: str, model: str, keep_alive: str = "10m", timeout: float = 60):
        self.url = url.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive
        self.timeout = timeout
        self._caps = None

    def _post(self, path: str, body: dict, timeout: float) -> dict:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise LLMError(f"model {self.model} není stažený: ollama pull {self.model}") from e
            raise LLMError(f"ollama HTTP {e.code}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise LLMError("ollama neběží (spusť ollama serve)") from e

    def capabilities(self) -> list[str]:
        if self._caps is None:
            self._caps = self._post("/api/show", {"model": self.model}, 5).get("capabilities") or []
        return self._caps

    def prewarm(self) -> None:
        """Load the model into memory so the first real question is fast."""
        self._post("/api/generate", {"model": self.model, "keep_alive": self.keep_alive}, self.timeout)

    def suggest(self, description: str, hints: list[tuple[str, str]] = ()) -> list[dict]:
        user = f"Description: {description}"
        if hints:
            user += "\nDictionary candidates (word: translations): " + "; ".join(f"{a}: {b}" for a, b in hints[:15])
        body = {
            "model": self.model, "stream": False, "format": SCHEMA, "keep_alive": self.keep_alive,
            "options": {"temperature": 0, "num_predict": 700},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        }
        if "thinking" in self.capabilities():
            body["think"] = False
        res = self._post("/api/chat", body, self.timeout)
        content = (res.get("message") or {}).get("content") or ""
        try:
            cands = json.loads(content).get("candidates", [])
        except json.JSONDecodeError:
            cands = _salvage(content)  # cut off at the token limit: keep the complete candidates
            if not cands:
                raise LLMError("nečitelná odpověď modelu")
        return [c for c in cands if isinstance(c, dict) and (c.get("en") or c.get("cs"))][:5]

    def translate(self, text: str, src: str, tgt: str) -> str:
        """Natural, faithful translation of a paragraph; paragraph breaks are kept."""
        names = {"src": LANG_NAMES.get(src, src), "tgt": LANG_NAMES.get(tgt, tgt)}
        body = {
            "model": self.model, "stream": False, "format": TRANSLATE_SCHEMA, "keep_alive": self.keep_alive,
            # JSON escaping and Czech diacritics cost tokens: budget above the input length, capped for safety.
            "options": {"temperature": 0.2, "num_predict": min(3 * len(text) // 2 + 200, 4096)},
            "messages": [{"role": "system", "content": TRANSLATE_SYSTEM.format(**names)},
                         {"role": "user", "content": text}],
        }
        if "thinking" in self.capabilities():
            body["think"] = False
        res = self._post("/api/chat", body, self.timeout)
        content = (res.get("message") or {}).get("content") or ""
        try:
            out = json.loads(content).get("translation")
        except (json.JSONDecodeError, AttributeError):
            out = None
        if not isinstance(out, str) or not out.strip():
            raise LLMError("nečitelná odpověď modelu")
        return out.strip()


def _salvage(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r"\{[^{}]*\}", text):
        try:
            out.append(json.loads(m.group(0)))
        except json.JSONDecodeError:
            pass
    return out
