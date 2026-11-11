"""Paragraph mode state without the window: versions and Alt+←/→, replacement steps and Ctrl+Z, the checks of the
LLM's rewritten sentences, and the source sentence a word comes from. Also the history rows (items.py)."""
from qtrans.items import ago, history_item
from qtrans.paragraph_mode import Para, plausible_fix, sentence_spans, source_sentence


def para_with(*keys):
    p = Para("Jak se máš brouku. Stýská se mi po našich výletech.")
    for k in keys:
        p.versions[k] = (f"text {k}", f"zdroj {k}")
    p.current = keys[0] if keys else None
    return p


def test_cycle_goes_round_in_order():
    p = para_with("llm", "mt", "online")
    p.current = "llm"
    assert p.keys() == ["mt", "online", "llm"]
    assert p.cycle(-1) and p.current == "online"
    assert p.cycle(-1) and p.current == "mt"
    assert p.cycle(-1) and p.current == "llm"  # round the end
    assert p.cycle(1) and p.current == "mt"


def test_cycle_needs_two_versions():
    assert not para_with("mt").cycle(1)
    assert not para_with().cycle(-1)


def test_cycle_leaves_the_original():
    p = para_with("mt", "llm")
    p.show_original = True
    p.cycle(1)
    assert not p.show_original


def test_replacement_steps_and_undo():
    p = para_with("mt", "llm")
    p.current = "llm"
    p.replace("text llm 1")
    assert p.current == "edit" and p.edit_base == "llm" and p.keys() == ["mt", "llm", "edit"]
    assert p.versions["edit"] == ("text llm 1", "zdroj llm")
    p.replace("text llm 2")
    p.amend("text llm 2 fixed")  # the grammar fix belongs to step 2
    assert p.shown() == "text llm 2 fixed" and p.versions["llm"][0] == "text llm"
    assert p.undo_step() and p.shown() == "text llm 1"  # word + fix at once
    assert p.undo_step() and p.current == "llm" and "edit" not in p.versions
    assert not p.undo_step()  # nothing left: Ctrl+Z goes to the input


def test_editing_another_version_starts_a_new_edit():
    p = para_with("mt", "llm")
    p.replace("mt edited")
    p.current = "llm"
    p.replace("llm edited")
    assert p.edit_base == "llm" and p.undo == ["text llm"]
    p.current = "mt"
    assert not p.undo_step()  # only the shown edit version is undone


def test_plausible_fix():
    assert plausible_fix("Chybí mi naše společné cesta.", "Chybí mi naše společné cesty.")
    assert plausible_fix("How are you, Bug?", "How are you, dear?")
    assert not plausible_fix("How are you, Bug?", "How are you, dear? Stysku se mi po našich výletech.")
    assert not plausible_fix("Seděli jsme na břeh.", "Seděli jsme na břeh.")  # nothing changed
    assert not plausible_fix("Seděli jsme na břeh.", "")
    assert not plausible_fix("Seděli jsme na břeh.", None)
    assert not plausible_fix("Seděli jsme na břeh.", "Here is the corrected sentence for you.")


def test_sentence_spans():
    text = "Ahoj. Jak se máš?  Dobře, např. dnes.\nNový řádek"
    assert [text[s:e] for s, e in sentence_spans(text)] == ["Ahoj.", "Jak se máš?", "Dobře, např. dnes.", "Nový řádek"]
    assert sentence_spans("") == []


def test_source_sentence():
    src = "jak se máš brouku. stýská se mi po našich výletech."
    tr = "How are you, Bug? I miss you on our outings."
    assert source_sentence(src, tr, tr.index("outings"), []) == "stýská se mi po našich výletech."  # by number
    assert source_sentence(src, tr, 13, [(11, 17)]) == "jak se máš brouku."  # by the source word
    # Different sentence counts: the sentence at the same relative position.
    assert source_sentence(src, "How are you, Bug, I miss you on our outings.", 40, []) == \
        "stýská se mi po našich výletech."


def test_ago():
    now = 1_000_000.0
    assert ago(now - 5, now) == "právě teď"
    assert ago(now - 300, now) == "před 5 min"
    assert ago(now - 3 * 3600, now) == "před 3 h"
    assert ago(now - 30 * 3600, now) == "včera"
    assert ago(now - 5 * 86400, now) == "před 5 dny"


def test_history_item():
    it = history_item({"text": "zapomenout", "translations": ["forget", "leave behind"], "at": 0.0}, "cs", 100.0)
    assert (it.kind, it.head, it.badge, it.lang) == ("dict", "zapomenout", "CS", "cs")
    assert it.copy == "forget" and it.copy_alt == "zapomenout" and it.alts == ["forget", "leave behind"]
    assert it.label == "před 1 min"
