"""Opt-in online translation (Alt+L) through the LINDAT translation service of UFAL, Charles University.

Only called on an explicit key press. Personal, non-commercial use per the service terms.
"""
import json
import urllib.parse
import urllib.request

URL = "https://lindat.mff.cuni.cz/services/translation/api/v2/languages/?src={src}&tgt={tgt}"


def translate(text: str, src: str, tgt: str, timeout: float = 4) -> str:
    req = urllib.request.Request(
        URL.format(src=src, tgt=tgt),
        data=urllib.parse.urlencode({"input_text": text}).encode(),
        headers={"Accept": "application/json", "User-Agent": "quick-translate/0.1"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    if not isinstance(out, list):
        return str(out).strip()
    # One item per sentence; a sentence without trailing whitespace needs a space before the next one.
    return "".join(s if i == 0 or out[i - 1][-1:].isspace() else " " + s for i, s in enumerate(out)).strip()
