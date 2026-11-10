"""Command-line lookup: python -m qtrans.cli zapomnel [more words...]"""
import sys
import time

from qtrans.paths import DB
from qtrans.search import Searcher


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m qtrans.cli <query>", file=sys.stderr)
        return 2
    s = Searcher(DB)
    raw = " ".join(argv)
    t = time.perf_counter()
    hits = s.search(raw, limit=10)
    ms = (time.perf_counter() - t) * 1000
    for h in hits:
        tr = ", ".join(x for x, _ in h.translations)
        lab = f" [{h.label}]" if h.label else ""
        print(f"{h.score:7.1f} {h.lang.upper()} {h.text}{lab} → {tr}")
    print(f"({len(hits)} hits, {ms:.1f} ms)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
