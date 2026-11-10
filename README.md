# Quick Translate

Rychlý překladač čeština ↔ angličtina ve stylu Spotlightu pro GNOME (Wayland).
**Super+T** otevře okno, píšeš česky nebo anglicky (klidně bez diakritiky, klidně „zapomněl“, „gave up“),
výsledky se ukazují při psaní. Všechno běží offline; online jen na vyžádání (Ctrl+L).

## Ovládání

| Klávesa | Co dělá |
|---|---|
| Super+T | otevřít / zavřít (označený text se předvyplní) |
| ↑ ↓, PgUp PgDn | výběr řádku |
| Enter | zkopírovat překlad a zavřít |
| Shift+Enter | zkopírovat heslo (hledané slovo) |
| Alt+1…9 | zkopírovat n-tý překlad vybraného řádku |
| Ctrl+L | online překlad přes LINDAT (ÚFAL MFF UK) — jediná věc, která jde na internet |
| `?popis` | „mám to na jazyku“: hledání slova podle popisu, např. `?nástroj na zatloukání hřebíků` |
| Ctrl+E | zeptat se lokálního LLM (ollama) na slovo podle popisu |
| Esc | zavřít |

### Režim odstavce

Delší text (víc než 8 slov, víc vět nebo víc řádků) se místo slovníku přeloží celý: označ odstavec třeba
v prohlížeči a stiskni Super+T. Hned se ukáže offline strojový překlad (Bergamot, větu po větě), okno
se zvětší až na ~60 % výšky obrazovky a dál se roluje.
Český odstavec se po chvilce sám zpřesní lokálním LLM (anglický zůstává strojový; vypíná `[paragraph] auto_llm`).

| Klávesa | Co dělá |
|---|---|
| Enter | zkopírovat celý překlad a zavřít |
| Shift+Enter | nový řádek v dotazu |
| Ctrl+↑ / Ctrl+↓ | přepnout mezi originálem a překladem (kontrola, co se překládalo) |
| Ctrl+L | lepší překlad online přes LINDAT |
| Ctrl+E | lepší, přirozenější překlad lokálním LLM (první dotaz načítá model) |
| PgUp PgDn | rolovat překlad |
| klik na slovo překladu | alternativy ze slovníku; klik na alternativu ji dosadí místo slova |
| Ctrl+Z | vrátit poslední dosazené slovo |

Lepší verze nahradí strojový překlad; štítek nad textem říká, odkud překlad je.

**Oprava slova:** klikni na špatně přeložené slovo (třeba „bug“ za „brouku“) a nabídnou se jiné překlady
slova z originálu, ze kterého vzniklo (brouk: *beetle, love, honey, sweetheart…*). Když slovník takové slovo
v originálu nenajde, nabídne *podobná slova* přes druhý jazyk, případně „Ve slovníku nic“. Dosazené slovo
převezme velké počáteční písmeno, štítek ukáže *upraveno* a Enter zkopíruje upravený text. Úpravy platí jen
pro zobrazenou verzi: nový odstavec, Ctrl+L nebo Ctrl+E je zahodí; automatické zpřesnění LLM upravený
překlad nepřepíše (jen nabídne Ctrl+E). Do češtiny se dosazuje základní tvar, skloňování je potřeba
dorovnat ručně. Tažením myší jde text dál normálně označit, Esc zavře nejdřív nabídku. Označený text se
předvyplní až do 2000 znaků (`[prefill] max_paragraph_chars`), krátký text dál jen do 80 znaků.

Řádek *strojový překlad* je offline neuronový překladač z Firefoxu (Bergamot) — objeví se u frází a vět,
které slovník nezná. Štítky u řádků vysvětlují, proč se řádek objevil: *tvar „zapomněl“*, *vid. protějšek*,
*bez se/si*, *fráze z dotazu*, *překlep?*

## Instalace (jednou)

```bash
python3 -m venv --system-site-packages .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m dictbuild.fetch_models   # Bergamot cs↔en (~49 MB) do data/models
.venv/bin/python -m dictbuild.fetch          # slovníky a frekvence (~100 MB) do data/raw
.venv/bin/python -m dictbuild.build_db       # data/dict.sqlite (~95 MB, ~30 s)
install/install.sh --phase1                  # D-Bus služba + zkratka Super+T
```

Odinstalace: `install/install.sh --uninstall` (ostatní vlastní zkratky zůstanou).

### GNOME rozšíření (volitelné, doporučené)

Bez rozšíření se okno otevírá uprostřed obrazovky, neprůhledné. Rozšíření ho dá nahoru jako Spotlight,
se spolehlivým focusem, zavře ho kliknutím mimo a dá pod něj **Liquid Glass** z rozšíření Atelier:
mražené sklo, které na okrajích láme to, co je pod ním (tapetu i okna), s odleskem, obrysem a stínem.
Sklo se řídí nastavením Atelieru (varianta regular/clear/off, ztmavení); bez Atelieru je „regular“.
Vypadá stejně jako widgety Atelieru (bez ztmavení a mražení navíc). Vysoký kontrast GNOME sklo vypne.

```bash
ln -s "$PWD/extension/quick-translate@que.cz" ~/.local/share/gnome-shell/extensions/
# odhlásit a přihlásit (Wayland neumí načíst nové rozšíření bez nového přihlášení), pak:
gnome-extensions enable quick-translate@que.cz
gapplication action cz.que.QuickTranslate quit   # aplikace se při dalším Super+T spustí s přirozenou výškou okna
```

Vyzkoušení bez odhlášení ve vnořeném GNOME (s oknem editoru pod překladačem, snímky obrazovky a měřením,
kolik procesoru Shellu stojí psaní):

```bash
tests/nested_shell.sh /tmp/qt-nested                  # tmavý styl, se sklem
NO_GLASS=1 tests/nested_shell.sh /tmp/qt-nested-plain # bez skla, pro srovnání
STYLE=default tests/nested_shell.sh /tmp/qt-light     # světlý styl
```

### LLM pro „mám to na jazyku“ (volitelné)

```bash
ollama pull gemma3:4b   # ~3.3 GB, vejde se do 6 GB VRAM
```

Model jde změnit v konfiguraci. Česká slova, která LLM navrhne, se ověřují ve slovníku
(štítek *ve slovníku* / *neověřeno*).

## Konfigurace

`~/.config/quick-translate/config.toml` — vzor v `install/config.example.toml`
(počet řádků, šířka, předvyplňování, online, model LLM).

## Vývoj

```bash
bin/quick-translate --gapplication-replace        # spustit nový kód místo běžící instance (log do terminálu)
.venv/bin/python -m qtrans.cli zapomnel           # hledání z příkazové řádky
.venv/bin/python -m pytest -q                     # testy (zlaté dotazy v tests/golden.tsv)
.venv/bin/python tests/bench_search.py            # rychlost hledání na stisk klávesy
.venv/bin/python tests/bench_mt.py                # rychlost strojového překladu
```

Logy: `~/.local/state/quick-translate/qt.log`, pády procesu spuštěného přes D-Bus:
`journalctl --user -f | grep QuickTranslate`.

Struktura: `qtrans/` aplikace (search.py hledání, app.py okno, mt.py offline překlad, reverse.py hledání
podle popisu, llm.py ollama, online.py LINDAT), `dictbuild/` stavba databáze, `extension/` GNOME rozšíření,
`docs/` plán a rešerše.

## Data a licence

GNU/FDL Anglicko-český slovník (GFDL), Wiktionary přes kaikki.org (CC BY-SA), ČNK SYN2015 (CC BY 4.0),
FrequencyWords (CC BY-SA), Open English WordNet (CC BY 4.0), modely Firefox Translations (MPL-2.0).
Pro osobní použití v pořádku; databázi ani modely nezveřejňuj — sdílej jen skripty.

## Stav a známá omezení

- Slovník: ~200 tis. hesel, ~200 tis. překladových dvojic, ~370 tis. tvarů; hledání ~0.5 ms (p95 < 4 ms) na stisk.
  Zlaté testy: `tests/golden.tsv` (105 dotazů včetně regresí z auditu kvality).
- Strojový překlad (Bergamot) dává jednu variantu a občas chybuje u krátkých frází („can opener → může otvírák“);
  slovník má proto vždy přednost, Ctrl+L dá druhý názor online.
- Hledání podle popisu (`?…`) bez LLM je jen orientační (fulltext nad definicemi WordNetu a Wiktionary);
  s Ctrl+E (gemma3:4b) je výrazně lepší. První dotaz na model trvá ~15 s (načtení do GPU), další ~2–6 s.
- Bez GNOME rozšíření (nebo před novým přihlášením) je okno uprostřed, neprůhledné a na chvíli v Alt+Tab;
  klik na plochu ho nezavře (klik do jiného okna, Esc a Super+T ano).
- Liquid Glass (`extension/…/glass.js`; `glassMaterial.js` a `glassLook.js` jsou kopie z Atelieru, při jeho
  změnách je potřeba je zkopírovat znovu): sklo roste po 64 px a při animaci seznamu se mění jen parametry
  shaderu; ve vnořeném GNOME stálo 40 změn dotazu ~0.3 s procesoru Shellu se sklem i bez něj (rozdíl ~10 %).
  Pod překladačem je klon tapety a oken pod ním. Nad samotnou tapetou je sklo čiré (jako widgety Atelieru),
  jakmile je pod kartou okno, zmatní (rozostření jako Atelier pod textem), aby text okna nerušil. Okna pod ním
  se po dobu jeho otevření kreslí celá (Mutter je jinak ořezává a klon by byl děravý). Světlo, které se
  v Atelieru naklání za kurzorem, tu není (stálo by překreslení při každém pohybu myši).
- Stažené zkrácené tvary („you're right“) slovník nerozloží — celou větu přeloží řádek strojového překladu.
