from qtrans.normalize import core_tokens, deletions, has_czech_chars, norm, osa_distance


def test_norm_folds_diacritics_and_case():
    assert norm("Dát si POZOR!") == "dat si pozor"
    assert norm("zapomněl") == "zapomnel"
    assert norm("  e-mail ") == "e mail"
    assert norm("one's own") == "ones own"
    assert norm("don't") == "dont"
    assert norm("Øresund ł") == "oresund l"
    assert norm("Straße") == "strasse"


def test_czech_chars():
    assert has_czech_chars("pes ěšč")
    assert not has_czech_chars("dog")


def test_core_tokens():
    assert core_tokens(["dat", "si", "pozor"]) == ["dat", "pozor"]
    assert core_tokens(["se"]) == ["se"]
    assert core_tokens(["take", "care", "of", "sth"]) == ["take", "care", "of"]


def test_typo_helpers():
    assert "zapomenut" in deletions("zapomenout")
    assert osa_distance("zapomenot", "zapomenout") == 1
    assert osa_distance("zapomenuot", "zapomenout") == 1  # transposition
    assert osa_distance("pes", "kocka") > 1
