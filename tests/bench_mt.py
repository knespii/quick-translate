"""Measure Bergamot load time and per-phrase latency. Usage: python tests/bench_mt.py"""
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrans.paths import MODELS  # noqa: E402

CS = ["dát si pozor", "zapomněl jsem klíče", "mít pravdu", "vzdát se", "otvírák na konzervy",
      "dej mi vědět", "přijít na to", "mít smůlu", "pes štěká na souseda", "ujistit se, že je zamčeno"]
EN = ["give up", "make sure", "look forward to it", "take off your shoes", "on the tip of my tongue",
      "I forgot my keys", "keep in mind", "be careful", "can opener", "get used to it"]


def main() -> int:
    if not (MODELS / "cs-en").is_dir():
        print("SKIP: models missing, run python -m dictbuild.fetch_models")
        return 0
    from qtrans.mt import MTEngine
    t0 = time.perf_counter()
    eng = MTEngine()
    print(f"load both directions: {(time.perf_counter() - t0) * 1000:.0f} ms, backend {eng.tr['cs-en'].backend()}")
    for pair, phrases in (("cs-en", CS), ("en-cs", EN)):
        eng.translate(phrases[0], pair)  # warm-up
        times = []
        for p in phrases:
            t = time.perf_counter()
            out = eng.translate(p, pair)
            times.append((time.perf_counter() - t) * 1000)
            print(f"  {pair} {times[-1]:6.1f} ms  {p!r} -> {out!r}")
        times.sort()
        p95 = times[min(len(times) - 1, int(round(0.95 * (len(times) - 1))))]
        print(f"{pair}: p50 {statistics.median(times):.1f} ms, p95 {p95:.1f} ms, max {times[-1]:.1f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
