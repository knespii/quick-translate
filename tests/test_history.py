import json

from qtrans.history import History


def test_record_and_recent_newest_first(tmp_path):
    h = History(tmp_path / "h.json")
    for w in ("pes", "kočka", "dům", "strom"):
        h.record("cs", w, ["x"])
    h.record("en", "dog", ["pes"])
    assert [e["text"] for e in h.recent("cs")] == ["strom", "dům", "kočka"]
    assert [e["text"] for e in h.recent("en")] == ["dog"]
    assert set(h.recent("en")[0]) == {"text", "translations", "at"}


def test_duplicate_moves_to_top(tmp_path):
    h = History(tmp_path / "h.json")
    h.record("cs", "pes", ["dog"])
    h.record("cs", "kočka", ["cat"])
    h.record("cs", "Pes ", ["dog", "hound"])
    r = h.recent("cs", 10)
    assert [e["text"] for e in r] == ["Pes", "kočka"]
    assert r[0]["translations"] == ["dog", "hound"]


def test_diacritics_are_distinct(tmp_path):
    h = History(tmp_path / "h.json")
    h.record("cs", "být", ["be"])
    h.record("cs", "byt", ["flat"])
    assert len(h.recent("cs")) == 2


def test_ignored_inputs(tmp_path):
    h = History(tmp_path / "h.json")
    h.record("cs", "", ["x"])
    h.record("cs", "pes", [])
    h.record("cs", "jedna dva tři čtyři pět", ["x"])
    h.record("de", "Hund", ["dog"])
    assert h.recent("cs") == [] and not (tmp_path / "h.json").exists()
    h.record("cs", "jedna dva tři čtyři", ["x"])
    assert len(h.recent("cs")) == 1


def test_persist_limit_and_clear(tmp_path):
    p = tmp_path / "sub" / "h.json"
    h = History(p, limit=3)
    for i in range(5):
        h.record("en", f"w{i}", ["x"])
    assert len(json.loads(p.read_text())) == 3
    assert [e["text"] for e in History(p).recent("en")] == ["w4", "w3", "w2"]
    h.clear()
    assert History(p).recent("en") == []


def test_broken_file_is_empty(tmp_path):
    p = tmp_path / "h.json"
    p.write_text("{not json")
    h = History(p)
    assert h.recent("cs") == []
    h.record("cs", "pes", ["dog"])
    assert History(p).recent("cs")[0]["text"] == "pes"


def test_io_error_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    h = History(blocker / "h.json")
    h.record("cs", "pes", ["dog"])
    assert h.recent("cs")[0]["text"] == "pes"


def test_disabled_writes_nothing(tmp_path):
    p = tmp_path / "h.json"
    h = History(p, enabled=False)
    h.record("cs", "pes", ["dog"])
    assert h.recent("cs") == [] and not p.exists()


def test_hand_edited_entries_without_translations_are_dropped(tmp_path):
    p = tmp_path / "h.json"
    p.write_text(json.dumps([{"lang": "cs", "text": "pes", "translations": [], "at": 1.0},
                             {"lang": "cs", "text": "kočka", "translations": [3], "at": 1.0},
                             {"lang": "cs", "text": "dům", "translations": ["house"], "at": 1.0}]))
    assert [e["text"] for e in History(p).recent("cs")] == ["dům"]
