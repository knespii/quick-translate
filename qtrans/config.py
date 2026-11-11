"""User config from $XDG_CONFIG_HOME/quick-translate/config.toml, merged over defaults. Never written by the app."""
import copy
import logging
import tomllib

from qtrans.paths import CONFIG

DEFAULTS = {
    "online": {"enabled": True, "timeout": 4},
    "llm": {"model": "gemma3:4b", "url": "http://127.0.0.1:11434", "keep_alive": "10m", "timeout": 60},
    "ui": {"rows": 7, "width": 680},
    "prefill": {"enabled": True, "max_chars": 80, "max_paragraph_chars": 2000},
    "mt": {"enabled": True, "debounce_ms": 120},
    "paragraph": {"auto_llm": True},
    "history": {"enabled": True},
    "diacritics": {"enabled": True},
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    try:
        with open(CONFIG, "rb") as f:
            return _merge(DEFAULTS, tomllib.load(f))
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logging.getLogger("qt").warning("config %s ignored: %s", CONFIG, e)
        return copy.deepcopy(DEFAULTS)
