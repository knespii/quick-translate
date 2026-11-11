"""Local LLM (ollama): proposes words for a description (describe-a-word mode) and translates paragraphs.

Every Czech word it returns is checked against the dictionary by the caller; the LLM never has the last word.
"""
import http.client
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


ALTERNATIVES_SCHEMA = {
    "type": "object",
    "properties": {
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"word": {"type": "string"}, "sentence": {"type": "string"}, "why": {"type": "string"}},
                "required": ["word", "sentence", "why"],
            },
        }
    },
    "required": ["options"],
}

ALTERNATIVES_SYSTEM = (
    "You are a {src}-{tgt} translation editor. The user gives a {src} original, its {tgt} translation and one "
    "word of the translation they want to change, often because it is too literal. Think about what the original "
    "sentence really means (idioms, figurative use, terms of endearment, register) and offer up to "
    "{n} other {tgt} ways to translate that word in this context, best first. For each give: \"word\": the {tgt} "
    "replacement copied exactly from the new sentence (inflected, with the right article); \"sentence\": the "
    "whole {tgt} translation rewritten with it, grammatical and natural, everything else kept as it was; "
    "\"why\": a very short reason written in Czech (at most 6 words). Never offer the original word or just another "
    "form of it.")

FIX_SCHEMA = {
    "type": "object",
    "properties": {"sentence": {"type": "string"}},
    "required": ["sentence"],
}

FIX_SYSTEM = (
    "You are a careful {tgt} proofreader. In a {tgt} translation of a {src} text the user put a dictionary base "
    "form of a word in place of another word, so the grammar around it may be wrong. Return the sentence with "
    "that word kept (same meaning, in whatever form the grammar needs) and the grammar made correct: inflection, "
    "agreement, articles, singular or plural. Change nothing else.")

# Leading articles do not make a different option ("a beetle" vs "beetle").
_ARTICLES = re.compile(r"^(?:the|an|a)\s+", re.I)


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, url: str, model: str, keep_alive: str = "10m", timeout: float = 60, num_ctx: int | None = None):
        """num_ctx: the model's context (tokens) for every request – one size, as ollama loads the model again
        whenever it changes."""
        self.url = url.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive
        self.timeout = timeout
        self.num_ctx = num_ctx
        self._caps = None

    def _opts(self, **options) -> dict:
        if self.num_ctx:
            options["num_ctx"] = self.num_ctx
        return options

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
        self._post("/api/generate", {"model": self.model, "keep_alive": self.keep_alive, "options": self._opts()},
                   self.timeout)

    def suggest(self, description: str, hints: list[tuple[str, str]] = ()) -> list[dict]:
        user = f"Description: {description}"
        if hints:
            user += "\nDictionary candidates (word: translations): " + "; ".join(f"{a}: {b}" for a, b in hints[:15])
        body = {
            "model": self.model, "stream": False, "format": SCHEMA, "keep_alive": self.keep_alive,
            "options": self._opts(temperature=0, num_predict=700),
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
            "options": self._opts(temperature=0.2, num_predict=min(3 * len(text) // 2 + 200, 4096)),
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

    def alternatives_in_context(self, source: str, translation: str, word: str, src: str, tgt: str,
                                n: int = 4) -> list[dict]:
        """Up to n replacements for word that fit what the source sentence means, each with the rewritten
        translation: [{"word", "sentence", "why"}]; the original wording is never offered."""
        names = {"src": LANG_NAMES.get(src, src), "tgt": LANG_NAMES.get(tgt, tgt)}
        user = (f"Original ({names['src']}): {source}\nTranslation ({names['tgt']}): {translation}\n"
                f"Word to replace: {word}")
        # Every option repeats the whole sentence.
        budget = min(n * (len(translation) + 60) + 100, 2048)
        content = self._chat(ALTERNATIVES_SYSTEM.format(n=n, **names), user, ALTERNATIVES_SCHEMA, 0.3, budget)
        try:
            opts = json.loads(content).get("options", [])
        except (json.JSONDecodeError, AttributeError):
            opts = _salvage(content)  # cut off at the token limit: keep the complete options
            if not opts:
                raise LLMError("nečitelná odpověď modelu")
        if not isinstance(opts, list):
            raise LLMError("nečitelná odpověď modelu")
        return _clean_options(opts, translation, word)[:n]

    def fix_sentence(self, source: str, sentence: str, replaced: str, src: str, tgt: str) -> str:
        """sentence with the user's base form 'replaced' kept but the grammar around it corrected."""
        names = {"src": LANG_NAMES.get(src, src), "tgt": LANG_NAMES.get(tgt, tgt)}
        user = (f"Original ({names['src']}): {source}\nSentence ({names['tgt']}): {sentence}\n"
                f"Word put in by the user: {replaced}")
        content = self._chat(FIX_SYSTEM.format(**names), user, FIX_SCHEMA, 0.1,
                             min(2 * len(sentence) + 100, 2048))
        try:
            out = json.loads(content).get("sentence")
        except (json.JSONDecodeError, AttributeError):
            out = None
        if not isinstance(out, str) or not out.strip():
            raise LLMError("nečitelná odpověď modelu")
        return out.strip()

    def rewrite_stream(self, messages: list[dict], num_predict: int, on_text, cancelled,
                       temperature: float = 0.3) -> str:
        """Stream a free-text answer (/api/chat, stream): on_text(text so far) after every piece; stops (and so
        stops the model) as soon as cancelled() is true. Returns the whole text ('' when cancelled)."""
        body = {
            "model": self.model, "stream": True, "keep_alive": self.keep_alive, "messages": messages,
            "options": self._opts(temperature=temperature, num_predict=num_predict),
        }
        if "thinking" in self.capabilities():
            body["think"] = False
        parts = []
        with self._open("/api/chat", body, self.timeout) as r:
            try:
                return self._read_stream(r, parts, on_text, cancelled)
            except (OSError, ValueError, http.client.HTTPException) as e:  # (a timeout or a dropped connection)
                raise LLMError("ollama přestala odpovídat") from e

    @staticmethod
    def _read_stream(r, parts, on_text, cancelled) -> str:
        for line in r:
            if cancelled():
                return ""
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                raise LLMError("nečitelná odpověď modelu") from e
            if msg.get("error"):
                raise LLMError(f"ollama: {msg['error']}")
            piece = (msg.get("message") or {}).get("content") or ""
            if piece:
                parts.append(piece)
                on_text("".join(parts))
            if msg.get("done"):
                if msg.get("done_reason") == "length":  # cut off: never take half a text for the whole
                    raise LLMError("text je na model moc dlouhý, nedopsal ho")
                break
        return "".join(parts)

    def _open(self, path: str, body: dict, timeout: float):
        """A streaming request: the open response, read line by line (NDJSON); close it to stop the model."""
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise LLMError(f"model {self.model} není stažený: ollama pull {self.model}") from e
            raise LLMError(f"ollama HTTP {e.code}") from e
        except TimeoutError as e:
            raise LLMError("ollama neodpověděla včas (model se možná načítá, zkus to znovu)") from e
        except (urllib.error.URLError, ConnectionError) as e:
            raise LLMError("ollama neběží (spusť ollama serve)") from e

    def _chat(self, system: str, user: str, schema: dict, temperature: float, num_predict: int) -> str:
        body = {
            "model": self.model, "stream": False, "format": schema, "keep_alive": self.keep_alive,
            "options": self._opts(temperature=temperature, num_predict=num_predict),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if "thinking" in self.capabilities():
            body["think"] = False
        res = self._post("/api/chat", body, self.timeout)
        return (res.get("message") or {}).get("content") or ""


def _clean_options(opts: list, translation: str, word: str) -> list[dict]:
    """Complete, distinct options that really change the word (the model sometimes echoes the original)."""
    seen = {_word_key(word), translation.strip()}
    out = []
    for o in opts:
        if not isinstance(o, dict):
            continue
        w, sent = o.get("word"), o.get("sentence")
        if not isinstance(w, str) or not isinstance(sent, str) or not w.strip() or not sent.strip():
            continue
        if w.strip().lower() not in sent.lower():
            # The model sometimes names the word in the wrong language; the rewritten sentence is what counts.
            w = _changed_span(translation, sent)
        key = _word_key(w)
        if key in seen or sent.strip() in seen:
            continue
        seen.update((key, sent.strip()))
        why = o.get("why")
        out.append({"word": w.strip(), "sentence": sent.strip(), "why": why.strip() if isinstance(why, str) else ""})
    return out


def _changed_span(old: str, new: str) -> str:
    """The words of new that differ from old (common leading and trailing words dropped)."""
    a, b = old.split(), new.split()
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    j = 0
    while j < min(len(a), len(b)) - i and a[-1 - j] == b[-1 - j]:
        j += 1
    return " ".join(b[i:len(b) - j]).strip(" ,.;:!?")


def _word_key(w: str) -> str:
    return _ARTICLES.sub("", w.strip(" \t.,;:!?\"'“”„«»()").lower())


def _salvage(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r"\{[^{}]*\}", text):
        try:
            out.append(json.loads(m.group(0)))
        except json.JSONDecodeError:
            pass
    return out
