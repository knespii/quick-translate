"""Word frequencies -> zipf values (log10 of occurrences per billion words)."""
import io
import math
import zipfile


def _cnk(path) -> dict[str, float]:
    counts = {}
    with zipfile.ZipFile(path) as z:
        name = z.namelist()[0]
        with z.open(name) as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8-sig"):
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 3 or not cols[2].isdigit():
                    continue
                w = cols[1].lower()
                counts[w] = counts.get(w, 0) + int(cols[2])
    total = sum(counts.values())
    return {w: math.log10(c / total * 1e9) for w, c in counts.items()}


def czech(lemma_zip, word_zip) -> tuple[dict, dict]:
    return _cnk(lemma_zip), _cnk(word_zip)


def english(path, min_count: int = 3) -> dict[str, float]:
    counts = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            w, _, c = line.rstrip("\n").rpartition(" ")
            if not w or not c.isdigit():
                continue
            c = int(c)
            if c < min_count:
                break  # the file is sorted by count
            counts[w] = counts.get(w, 0) + c
    total = sum(counts.values())
    return {w: math.log10(c / total * 1e9) for w, c in counts.items()}
