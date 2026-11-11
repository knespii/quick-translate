"""Describe a word ("?věc na otevírání konzerv"): a full-text search over definitions (reverse.py), and on Alt+E
the local LLM's suggestions, checked against the dictionary."""
import logging

from qtrans.items import Item, esc, hit_item
from qtrans.normalize import has_czech_chars, norm

log = logging.getLogger("qt")


class DescribeMode:
    """Mixin of the App. Uses its win, ctl, cfg, searcher, _llm(), _restored() and WordMode's _add_extra()."""

    def _init_describe(self):
        self._rev = None
        self._describe_lists = []
        self._prewarmed = False

    def _describe_query(self, q: str):
        if not q:
            self.win.set_items([Item("info", "Popiš slovo, které ti nejde na jazyk",
                                     "<span alpha='60%'>např. <i>?věc na otevírání konzerv</i>  ·  Alt+E zeptá se LLM</span>",
                                     copy="")])
            self._llm_prewarm()
            return
        rev = self._reverse()
        czech = has_czech_chars(q) or not _looks_english(q)
        lists = ([rev.search_cs(q)] if czech else []) + [rev.search_en(q)]
        self._describe_lists = lists
        self.win.set_items(self._describe_items(rev.fuse(lists)))
        if czech:
            self.ctl.schedule_mt(q, ["cs-en"], self._describe_mt_done)

    def _describe_mt_done(self, res):
        if not res or isinstance(res, Exception):
            return
        rev = self._reverse()
        lists = self._describe_lists + [rev.search_en(res[0][1])]
        # Rows Alt+E added meanwhile stay on top (a pending one still waits for its answer).
        extras = [it for it in self.win.list.items if any(it is x for g in self._extras for x in g)]
        self.win.set_items(extras + self._describe_items(rev.fuse(lists)))

    def _describe_items(self, hits):
        items = [hit_item(h, label=h.label) for h in hits[: self.cfg["ui"]["rows"] * 2]]
        if not items:
            items = [Item("info", "Nic podle popisu", "<span alpha='60%'>zkus jiná slova nebo Alt+E (LLM)</span>", copy="")]
        return items

    def _reverse(self):
        from qtrans.reverse import Reverse
        if self._rev is None or self._rev.searcher is not self.searcher:
            self._rev = Reverse(self.searcher.con, self.searcher)
        return self._rev

    def _llm_prewarm(self):
        if self._prewarmed:
            return
        self._prewarmed = True
        llm = self._llm()
        self.ctl._pool.submit(lambda: _quiet(llm.prewarm))

    # --- LLM suggestions (Alt+E in word mode) --------------------------------------------------------
    def _llm_suggest(self):
        text = self._restored(self.win.get_text().strip().lstrip("?").strip())
        if not text or self.searcher is None:
            return
        hints = [(i.head, i.alts[0]) for i in self.win.list.items if i.kind == "dict" and i.alts][:15]
        item = Item("llm", f"Ptám se modelu {self.cfg['llm']['model']}…",
                    "<span alpha='60%'>první dotaz může trvat i desítky sekund (načítá se model)</span>",
                    copy="", pending=True)
        self._add_extra(item)
        llm = self._llm()
        self.ctl.run_io(lambda: llm.suggest(text, hints), lambda res: self._llm_done(item, res))
        log.info("llm question (%d hints)", len(hints))

    def _llm_done(self, item, res):
        if self.win.list.index_of(item) is None:  # removed by Alt+←
            return
        if isinstance(res, Exception):
            self._replace_extra(item, [Item("info", "LLM nedostupné", f"<span alpha='60%'>{esc(str(res))}</span>",
                                            copy="", key="llm")])
            return
        rows = []
        con = self.searcher.con
        for c in res:
            cs, en, why = (c.get("cs") or "").strip(), (c.get("en") or "").strip(), (c.get("why") or "").strip()
            # The Czech words come from the dictionary (via the English word); the model's own Czech only if
            # the dictionary does not know the English word, and then marked as unverified.
            row = con.execute("SELECT id, lang, text, key, nwords, zipf, flags, pos FROM term WHERE key = ? AND lang = 1 "
                              "ORDER BY zipf DESC LIMIT 1", (norm(en),)).fetchone() if en else None
            if row:
                from qtrans.search import Hit, LANG
                h = Hit(row[0], LANG[row[1]], *row[2:])
                trans = [t for t, _n in self.searcher.translations(h, 4)]
                sub = esc(", ".join(trans)) + (f"  <span alpha='60%'>({esc(why)})</span>" if why else "")
                rows.append(Item("llm", h.text, sub, badge="EN", label="LLM · slovník",
                                 copy=trans[0] if trans else h.text, copy_alt=h.text, alts=trans))
            else:
                known = con.execute("SELECT 1 FROM term WHERE key = ? AND lang = 0", (norm(cs),)).fetchone()
                label = "LLM · ve slovníku" if known else "LLM · neověřeno"
                sub = esc(f"{cs} — {why}" if why else cs)
                rows.append(Item("llm", en or cs, sub, badge="EN" if en else "CS", label=label,
                                 copy=cs or en, copy_alt=en or cs, alts=[x for x in (cs, en) if x]))
        if not rows:
            rows = [Item("info", "LLM nic nenavrhl", "", copy="")]
        self._replace_extra(item, rows)


def _looks_english(q: str) -> bool:
    from qtrans.search import _cues
    return bool(_cues(norm(q))[0]) or any(w in q.lower().split() for w in ("the", "a", "for", "of", "someone", "thing"))


def _quiet(fn):
    try:
        fn()
    except Exception as e:  # prewarm is best-effort
        log.info("llm prewarm skipped: %s", e)
