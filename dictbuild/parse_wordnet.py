"""Open English WordNet (WN-LMF XML): synset member lemmas + definitions, for the describe-a-word mode."""
import gzip
import xml.etree.ElementTree as ET


def parse(path) -> list[tuple[list[str], str]]:
    """Returns [(lemmas, definition)] — one row per synset."""
    entry_lemma = {}
    synsets = []
    with gzip.open(path, "rb") as f:
        for _ev, el in ET.iterparse(f, events=("end",)):
            tag = el.tag
            if tag == "LexicalEntry":
                lemma = el.find("Lemma")
                if lemma is not None:
                    entry_lemma[el.get("id")] = lemma.get("writtenForm")
                el.clear()
            elif tag == "Synset":
                d = el.find("Definition")
                members = (el.get("members") or "").split()
                if d is not None and d.text and members:
                    synsets.append((members, d.text.strip()))
                el.clear()
    out = []
    for members, definition in synsets:
        lemmas = [entry_lemma[m] for m in members if m in entry_lemma]
        if lemmas:
            out.append((list(dict.fromkeys(lemmas)), definition))
    return out
