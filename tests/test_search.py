"""Golden queries against the built dictionary (skipped when data/dict.sqlite is missing)."""
from pathlib import Path

import pytest

from qtrans.paths import DB

GOLDEN = Path(__file__).with_name("golden.tsv")


def load_cases():
    cases = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        q, head, rank = cols[0], cols[1], int(cols[2])
        trans = cols[3] if len(cols) > 3 and cols[3] else None
        cases.append((q, head.split("|"), rank, trans.split("|") if trans else None))
    return cases


def check(searcher, q, heads, rank, trans):
    hits = searcher.search(q, limit=max(rank, 10))
    for h in hits[:rank]:
        if h.text in heads:
            if trans is None or any(t in trans for t, _ in h.translations):
                return True, hits
    return False, hits


@pytest.fixture(scope="module")
def searcher():
    if not DB.exists():
        pytest.skip("run python -m dictbuild.build_db")
    from qtrans.search import Searcher
    return Searcher(DB)


def test_golden_pass_rate(searcher):
    cases = load_cases()
    failed = []
    for q, heads, rank, trans in cases:
        ok, hits = check(searcher, q, heads, rank, trans)
        if not ok:
            top = "; ".join(f"{h.text}→{','.join(t for t, _ in h.translations[:3])}" for h in hits[:4])
            failed.append(f"{q!r}: want {heads}≤{rank} {trans or ''} | got {top}")
    rate = 1 - len(failed) / len(cases)
    print(f"\ngolden: {len(cases) - len(failed)}/{len(cases)} passed ({rate:.0%})")
    for f in failed:
        print("  FAIL", f)
    assert rate >= 0.90, "\n".join(failed)


def test_empty_and_pronouns(searcher):
    assert searcher.search("") == []
    assert searcher.search("   ") == []
    assert 0 < len(searcher.search("se")) <= 14
    assert 0 < len(searcher.search("si")) <= 14
