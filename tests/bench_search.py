"""Per-keystroke latency: types every golden query letter by letter. Usage: python tests/bench_search.py"""
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrans.paths import DB  # noqa: E402
from qtrans.search import Searcher  # noqa: E402
from tests.test_search import load_cases  # noqa: E402


def main() -> int:
    t = time.perf_counter()
    s = Searcher(DB)
    s.search("warmup")
    print(f"open + first query: {(time.perf_counter() - t) * 1000:.1f} ms")
    times = []
    for q, *_ in load_cases():
        for i in range(1, len(q) + 1):
            t = time.perf_counter()
            s.search(q[:i])
            times.append((time.perf_counter() - t) * 1000)
    times.sort()
    p95 = times[int(0.95 * (len(times) - 1))]
    print(f"{len(times)} keystrokes: p50 {statistics.median(times):.2f} ms, p95 {p95:.2f} ms, max {times[-1]:.2f} ms")
    return 0 if p95 < 5 else 1


if __name__ == "__main__":
    sys.exit(main())
