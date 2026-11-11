"""Word mode: dictionary rows for a word or phrase, the offline MT row, the online row (Alt+L), going back from the
rows Alt+L / Alt+E added (Alt+←), copying and remembering the words looked up (history.py)."""
import logging
import time

from qtrans.items import Item, esc, hit_item
from qtrans.normalize import has_czech_chars, norm
from qtrans.search import INFLECTED

log = logging.getLogger("qt")


class WordMode:
    """Mixin of the App. Uses its win, ctl, cfg, searcher, hist, _open_db(), _restored(), _copy() and
    _copy_when_ready."""

    def _init_words(self):
        self._reset_words()

    def _reset_words(self):
        self._mt_item = None
        self._last_hits = []
        self._extras = []  # rows added by Alt+L / Alt+E, one list per key press, for Alt+←
        self._word_query = False  # a 1-3 word dictionary query: its words go into the history
        self._remembered = False

    def _run_words(self, raw: str, q: str):
        t = time.perf_counter()
        items = []
        hits = []
        if self.searcher is None:
            self._open_db()  # a query before the idle load (D-Bus activation) still finds the dictionary
        if self.searcher is None:
            items.append(Item("info", "Slovník chybí", "spusť <tt>python -m dictbuild.build_db</tt>", copy=""))
        else:
            hits = self.searcher.search(self._search_text(raw), limit=self.cfg["ui"]["rows"] * 2)
            self._last_hits = hits
            items += [hit_item(h) for h in hits]
        self._word_query = 1 <= len(q.split()) <= 3
        q = self._restored(q)  # the translations get the text with its diacritics, even before they are in the input
        pairs = self._mt_pairs(q, hits)
        if pairs and self.ctl.schedule_mt(q, pairs, self._mt_done):
            self._mt_item = Item("mt", "Překládám…", "<span alpha='60%'>strojový překlad</span>", copy="", pending=True)
            pos = 0 if not hits or len(q.split()) >= 3 else min(len(items), 3)
            items.insert(pos, self._mt_item)
        self.win.set_items(items)
        log.debug("query %r: %d hits in %.1f ms", raw, len(hits), (time.perf_counter() - t) * 1000)

    def _search_text(self, raw: str) -> str:
        """Diacritics typed by the user make the search strict ('děkuji moc' is not 'moč'); those the automatic
        diacritics put in are only a guess ('dál si pozor' for 'dal si pozor'): the dictionary gets them stripped."""
        if self.win.diacritics is None or self.win.user_diacritics:
            return raw
        from qtrans.autodiac import strip_diacritics
        return strip_diacritics(raw)

    def _mt_pairs(self, q: str, hits, force: bool = False) -> list[str]:
        nwords = len(q.split())
        exact = hits and hits[0].tier >= 900
        if not force and nwords < 2 and (exact or len(q) < 3):
            return []
        if has_czech_chars(q):
            return ["cs-en"]
        if hits:
            return ["cs-en"] if hits[0].lang == "cs" else ["en-cs"]
        return ["cs-en", "en-cs"]

    def _mt_done(self, res):
        item = self._mt_item
        if item is None:
            return
        q = self.win.get_text().strip()
        if isinstance(res, Exception) or not res:
            new = None
        else:
            # The wrong direction echoes the input back almost unchanged; keep the one that differs most.
            pair, text = max(res, key=lambda pr: _difference(q, pr[1]))
            new = Item("mt", text, f"<span alpha='60%'>strojový překlad · {pair.replace('-', ' → ')}</span>",
                       badge=pair.split("-")[1].upper(), copy=text, copy_alt=q)
        lst = self.win.list
        idx = lst.index_of(item)
        if idx is None:
            return
        if new is None or _difference(q, new.head) == 0:
            lst.replace(idx, [], select=lst.selected if lst.selected < idx else max(0, lst.selected - 1))
            self._mt_item = None
            return
        lst.replace(idx, [new], select=idx if self._copy_when_ready else None)
        self._mt_item = new
        if self._copy_when_ready:
            self.copy_item(new)

    # --- rows added by Alt+L / Alt+E, Alt+← removes them again ---------------------------------------
    def _add_extra(self, item):
        self.win.insert_top(item)
        self._extras.append([item])
        self.win.set_back(True)

    def _replace_extra(self, old, new_items):
        """The pending row old became new_items (in the list as well)."""
        idx = self.win.list.index_of(old)
        if idx is None:
            return False
        self.win.list.replace(idx, new_items, select=idx)
        for group in self._extras:
            if any(it is old for it in group):
                group[:] = [it for it in group if it is not old] + list(new_items)
        return True

    def back_words(self) -> bool:
        """Alt+←: remove the rows the last Alt+L / Alt+E added (also while they are still pending)."""
        if not self._extras:
            return False
        group = self._extras.pop()
        self._copy_when_ready = False
        self.win.set_items([it for it in self.win.list.items if not any(it is g for g in group)])
        self.win.set_back(bool(self._extras))
        return True

    # --- online (Alt+L) ----------------------------------------------------------------------------
    def _word_online(self):
        q = self._restored(self.win.get_text().strip())
        if not q:
            return
        if not self.cfg["online"]["enabled"]:
            self._add_extra(Item("info", "Online překlad je vypnutý", "zapni ho v <tt>config.toml</tt> ([online] enabled)", copy=""))
            return
        from qtrans import online
        pairs = self._mt_pairs(q, self._last_hits, force=True)
        timeout = self.cfg["online"]["timeout"]
        item = Item("online", "Ptám se LINDATu…", "<span alpha='60%'>online · ÚFAL MFF UK</span>", copy="", pending=True)
        self._add_extra(item)

        def work():
            return [(p, online.translate(q, *p.split("-"), timeout=timeout)) for p in pairs]

        # (Bound to its own row: a second Alt+L before the first answer must not leave a row pending.)
        self.ctl.run_io(work, lambda res: self._online_done(item, res))
        log.info("online lookup %s", pairs)

    def _online_done(self, item, res):
        if self.win.list.index_of(item) is None:  # removed by Alt+←
            return
        q = self.win.get_text().strip()
        if isinstance(res, Exception) or not res:
            log.info("online failed: %s", res)
            new = Item("info", "Online nedostupné", f"<span alpha='60%'>{esc(str(res)[:90])}</span>", copy="",
                       key="online")
        else:
            pair, text = max(res, key=lambda pr: _difference(q, pr[1]))
            new = Item("online", text, f"<span alpha='60%'>online · ÚFAL MFF UK · {pair.replace('-', ' → ')}</span>",
                       badge=pair.split("-")[1].upper(), copy=text, copy_alt=q)
        self._replace_extra(item, [new])
        if self._copy_when_ready and new.kind == "online":
            self.copy_item(new)

    # --- copying -----------------------------------------------------------------------------------
    def copy_item(self, item, alt_text=False):
        if item is None:
            return
        if item.pending:
            self._copy_when_ready = True
            return
        text = item.copy_alt if (alt_text and item.copy_alt) else item.copy
        if not text:
            return
        self._remember_item(item)
        self._copy(text)

    def copy_nth(self, n: int):
        item = self.win.selected_item()
        if item and n < len(item.alts):
            self._remember_item(item)
            self._copy(item.alts[n])

    # --- history -------------------------------------------------------------------------------------
    def _remember_item(self, item):
        """A dictionary row copied after a word query (or from the history itself) goes to the top of the history."""
        if item.kind == "dict" and item.lang and (self._word_query or self.win.history):
            self.hist.record(item.lang, item.copy_alt or item.head, item.alts)
            self._remembered = True

    def _remember_on_close(self):
        """Closing after a word query without copying: remember its top hit if it is the word itself or its form."""
        hits = self._last_hits
        if self._word_query and not self._remembered and hits and hits[0].tier >= INFLECTED:
            h = hits[0]
            self.hist.record(h.lang, h.text, [t for t, _n in h.translations])
            self._remembered = True


def _difference(a: str, b: str) -> int:
    na, nb = norm(a), norm(b)
    return 0 if na == nb else 1 + abs(len(set(na.split()) ^ set(nb.split())))
