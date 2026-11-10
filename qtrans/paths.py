"""Where the app keeps its data. Everything lives in <repo>/data unless QT_DATA_DIR is set."""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("QT_DATA_DIR") or REPO / "data")
RAW = DATA / "raw"
MODELS = DATA / "models"
DB = DATA / "dict.sqlite"

_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
STATE = _state / "quick-translate"
_config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
CONFIG = _config / "quick-translate" / "config.toml"
