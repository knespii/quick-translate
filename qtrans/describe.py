"""Describe a word ("?věc na otevírání konzerv"): once typing pauses, the local LLM names the word; each candidate is
shown as the Czech word with its English one under it, checked against the dictionary. Without the LLM, a full-text
search over the dictionary's definitions (reverse.py) stands in."""
import logging
import re

from gi.repository import GLib

from qtrans.items import Item, esc, hit_item
from qtrans.normalize import has_czech_chars, norm

log = logging.getLogger("qt")

ASK_AFTER_MS = 600  # the LLM is asked once typing pauses this long


class DescribeMode:
    """Mixin of the App. Uses its win, ctl, cfg, searcher, _llm(), _restored() and WordMode's _add_extra()."""

    def _init_describe(self):
        self._rev = None
        self._describe_timer = 0
        self._described = {}  # description -> its rows (going back to one asks no more)
        self._prewarmed = False

    def _describe_query(self, q: str):
        if self._describe_timer:
            GLib.source_remove(self._describe_timer)
            self._describe_timer = 0
        if not q:
            self.win.set_items([Item("info", "Popiš slovo, které ti nejde na jazyk",
                                     "<span alpha='60%'>např. <i>?věc na otevírání konzerv</i>  ·  ukáže české slovo "
                                     "a pod ním anglické</span>", copy="")])
            self._llm_prewarm()
            return
        if q in self._described:
            self.win.set_items(self._described[q])
            return
        self.win.set_items([self._describe_pending()])
        gen = self.ctl.gen

        def fire():
            self._describe_timer = 0
            if gen == self.ctl.gen:
                self._describe_ask(q)
            return GLib.SOURCE_REMOVE
        self._describe_timer = GLib.timeout_add(ASK_AFTER_MS, fire)

    def _describe_pending(self):
        return Item("llm", "Hledám slovo…", f"<span alpha='60%'>{esc(self.cfg['llm']['model'])} · první dotaz může "
                    "trvat i desítky sekund (načítá se model)</span>", copy="", pending=True, key="describe")

    def _describe_ask(self, q: str):
        """Ask the LLM now (also Alt+E): its candidates replace the rows."""
        if self._describe_timer:
            GLib.source_remove(self._describe_timer)
            self._describe_timer = 0
        vulgar = asks_rude(q)
        llm = self._llm()
        self.win.set_items([self._describe_pending()])
        self.ctl.run_io(lambda: llm.suggest(q), lambda res: self._describe_done(q, res, vulgar))
        log.info("llm describe question")

    def _describe_done(self, q, res, vulgar_ok):
        if isinstance(res, Exception):
            # Without the LLM: the definitions search, crude but better than nothing.
            rows = self._definition_rows(q, vulgar_ok)
            rows.insert(0, Item("info", "LLM nedostupné, hledám v definicích",
                                f"<span alpha='60%'>{esc(str(res))}</span>", copy="", key="llm"))
            self.win.set_items(rows, select=1 if len(rows) > 1 else 0)
            return
        rows = self._candidate_rows(res, vulgar_ok)
        self._described[q] = rows
        if len(self._described) > 20:
            self._described.pop(next(iter(self._described)))
        self.win.set_items(rows)

    def _definition_rows(self, q, vulgar_ok):
        rev = self._reverse()
        czech = has_czech_chars(q) or not _looks_english(q)
        # (A Czech description's own words match English definitions by accident: 'co' -> co-star.)
        hits = rev.fuse([rev.search_cs(q)] if czech else [rev.search_en(q)])
        if not vulgar_ok:  # (a crude match turns up slang: 'hýbat' in a gloss of 'jebat')
            hits = [h for h in hits if not rude(h.text, [t for t, _n in h.translations[:2]])]
        rows = [hit_item(h, label=h.label) for h in hits[: self.cfg["ui"]["rows"]]]
        return rows or [Item("info", "Nic podle popisu", "<span alpha='60%'>zkus jiná slova</span>", copy="")]

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

    # --- the LLM's candidates as rows: the Czech word, the English one under it -------------------------
    def _candidate_rows(self, res, vulgar_ok: bool = False) -> list:
        rows, seen = [], set()
        con = self.searcher.con
        thing = self._llm().last_thing.strip()
        log.debug("llm took it for: %s", thing[:120])
        for c in res:
            cs, en, why = (c.get("cs") or "").strip(), (c.get("en") or "").strip(), (c.get("why") or "").strip()
            # The Czech word comes from the dictionary (the English word's translations: the model's own Czech if
            # it is one of them, else the most common); the model's own Czech only when the dictionary does not
            # know the English word, and then marked as unverified.
            row = con.execute("SELECT id, lang, text, key, nwords, zipf, flags, pos FROM term WHERE key = ? AND lang = 1 "
                              "ORDER BY zipf DESC LIMIT 1", (norm(en),)).fetchone() if en else None
            if row:
                from qtrans.search import Hit, LANG
                h = Hit(row[0], LANG[row[1]], *row[2:])
                trans = [t for t, _n in self.searcher.translations(h, 4)]
                if not trans or (not vulgar_ok and rude(h.text, trans[:2])):
                    continue  # (the model was told not to; a small one does anyway)
                head = next((t for t in trans if norm(t) == norm(cs)), trans[0])
                english, label = h.text, "LLM · slovník"
                others = [t for t in trans if t != head][:2]
            elif not cs or (not vulgar_ok and rude(en, [cs])):
                continue
            else:
                # The English term is not in the dictionary (often a technical one). The model's Czech for it is
                # kept, unverified, unless the dictionary knows that Czech word as something else entirely
                # ('vrtulník' offered for 'lead screw').
                cs_row = con.execute("SELECT id, lang, text, key, nwords, zipf, flags, pos FROM term WHERE key = ? "
                                     "AND lang = 0 ORDER BY zipf DESC LIMIT 1", (norm(cs),)).fetchone()
                if cs_row:
                    from qtrans.search import Hit, LANG
                    meant = {w for t, _n in self.searcher.translations(Hit(cs_row[0], LANG[0], *cs_row[2:]), 8)
                             for w in norm(t).split()}
                    if en and not meant & set(norm(en).split()):
                        continue
                head, english, others = cs, en, []
                label = "LLM · neověřeno"
            if norm(head) in seen:
                continue
            seen.add(norm(head))
            sub = f"<b>{esc(english)}</b>" if english else ""
            if others:
                sub += f"  <span alpha='60%'>· {esc(', '.join(others))}</span>"
            if why:
                sub += f"  <span alpha='60%'>({esc(why)})</span>"
            rows.append(Item("llm", head, sub, badge="CS", label=label, copy=head, copy_alt=english or head,
                             alts=[x for x in (head, english) if x], lang="cs"))
        if not rows:
            rows = [Item("info", "LLM nic nenavrhl", "<span alpha='60%'>zkus to popsat jinak</span>", copy="")]
        if thing:  # (so a misunderstood description shows: describe it differently)
            rows.append(Item("info", "Model to pochopil jako", f"<span alpha='60%'>{esc(thing[:160])}</span>",
                             copy="", key="llm-thing"))
        return rows

    # --- LLM suggestions (Alt+E in word mode) --------------------------------------------------------
    def _llm_suggest(self):
        text = self._restored(self.win.get_text().strip().lstrip("?").strip())
        if not text or self.searcher is None:
            return
        vulgar = asks_rude(text)
        item = Item("llm", f"Ptám se modelu {self.cfg['llm']['model']}…",
                    "<span alpha='60%'>první dotaz může trvat i desítky sekund (načítá se model)</span>",
                    copy="", pending=True)
        self._add_extra(item)
        llm = self._llm()
        self.ctl.run_io(lambda: llm.suggest(text), lambda res: self._llm_done(item, res, vulgar))
        log.info("llm question")

    def _llm_done(self, item, res, vulgar_ok: bool = False):
        if self.win.list.index_of(item) is None:  # removed by Alt+←
            return
        if isinstance(res, Exception):
            self._replace_extra(item, [Item("info", "LLM nedostupné", f"<span alpha='60%'>{esc(str(res))}</span>",
                                            copy="", key="llm")])
            return
        self._replace_extra(item, self._candidate_rows(res, vulgar_ok))


# Unmistakably vulgar or sexual words (folded stems). The dictionary marks only some of their pairs as vulgar,
# and common words have vulgar senses too ('screw', 'cock' = kohout), so only words with no decent sense are
# listed, and the head and its first translations decide.
_RUDE = re.compile(r"\b(?:fuck|shit|cunt|whore|slut|asshole|motherf|wank|jeb|vyjeb|mrd|zmrd|"
                   r"sukat|sukal|souloz|kurv|pic[aeiuy]\b|curak|kokot|prdel|hovn|srac|chcat)")
_ASKS = re.compile(r"\b(?:sprost|vulg|nadavk|rude|swear|curse|slang)")


def rude(head: str, translations=()) -> bool:
    """Is the word, or one of its first translations, vulgar or sexual?"""
    return any(_RUDE.search(norm(w)) for w in (head, *translations) if w)


def asks_rude(description: str) -> bool:
    """Does the description ask for a rude word itself (by using one, or saying it wants one)?"""
    return bool(_RUDE.search(norm(description)) or _ASKS.search(norm(description)))


def _looks_english(q: str) -> bool:
    from qtrans.search import _cues
    return bool(_cues(norm(q))[0]) or any(w in q.lower().split() for w in ("the", "a", "for", "of", "someone", "thing"))


def _quiet(fn):
    try:
        fn()
    except Exception as e:  # prewarm is best-effort
        log.info("llm prewarm skipped: %s", e)
