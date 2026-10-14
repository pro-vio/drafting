---
name: citari-zotero (jumatatea de citari a skill-ului drafting)
description: >-
  Manage a manuscript's references with Zotero as the single source of truth: render LIVE
  in-text citations + an auto References list via pandoc + citeproc (any CSL style), and
  generate a custom chronological "Legislation and case law" list that CSL can't express.
  Use whenever someone does citation/bibliography work on a paper, article, or manuscript
  backed by Zotero — e.g. "rebuild the bibliography",
  "references went stale after accepting track changes", "regenerate citations", "switch the
  style to APA", "cite this from my Zotero library", "add a legislation/case-law list", or
  editing `[@citekey]` markers, CSL-JSON/.bib, or a Works-Cited section from a reference
  manager. Also fixes Zotero items that render wrong (ALL-CAPS titles, swapped place/publisher,
  ugly citekeys, reprint dates, annotations) via direct DB edits, since the live API is
  read-only. Reach for it even when "Zotero" isn't named but the user is wrangling
  reference-managed citations. Not for Zotero app setup or PDF organising.
---

# Zotero citations

**Referințele se utilizează, nu se afișează.** Un item intră în bibliotecă și în text doar
dacă manuscrisul îi folosește definiția, procedura sau cifra — niciodată ca împăunare.
Textul se propune întâi; autorul citește sursa după, deci la afirmații egale câștigă sursa
mai ușor de procurat și de citit — adesea articolul; cărțile și capitolele de carte intră
doar unde afirmația folosită trăiește doar acolo. Fără alternative „de acoperire".

Keep a manuscript's citations **live and never stale** by treating **Zotero as the single source
of truth** and regenerating from it, instead of hand-maintaining citation text or a parallel .bib.

Two products, two pipelines:
1. **Academic apparatus** — in-text author–date citations + the auto-generated "References" list,
   via `pandoc --citeproc` with a swappable CSL style. → `scripts/build_manuscris.py`
2. **Legislation & case law** — a separate, chronological list of statutes / secondary legislation
   / court decisions, in a custom house format a CSL style cannot express. → `scripts/build_legislation_list.py`

The golden rule everything follows: **the manuscript holds `[@citekey]` markers, not frozen
citation text.** You change something in Zotero, you re-run a script. That's the whole workflow.

## Prerequisites

- **Zotero running** (local API on port 23119) for reading. `HTTP 000` = it's closed.
- **Better BibTeX** installed (gives each item a stable citekey). After editing items directly in
  the DB, trigger a BBT scan / "regenerate citekeys" so the API reflects pins in the Extra field.
- **pandoc** on PATH (with built-in citeproc).
- **Zotero library user id** for the API URL. The scripts auto-detect it (`zot.detect_user_id`)
  or take `--user-id N` / the `ZOTERO_USER_ID` env var. To find it manually: it's the number in
  your Zotero sync URL, or run `python scripts/zot.py userid`.

The Zotero data dir (database + `storage`, i.e. the PDFs) is read from Zotero's own setting
(`extensions.zotero.dataDir` in `prefs.js`), so moving it from Zotero's settings is enough;
`ZOTERO_DIR` overrides it. Everything the tools produce outside the database lives in
`zot.LUCRU` (default `D:\zotero-lucru`, override `ZOTERO_LUCRU`): `copii/` (the pre-write
database copies), `instantaneu/` (the read snapshot taken while Zotero holds a full `-wal`, one
per journal state, older ones deleted), `scrieri-amanate/` (the deferred-write queue), `_import/`
(PDFs prepared by `adu_lege.py`) and `tmp/`. Nothing goes to C: — PDFs on E:, the rest on D:
(the author's decision, 12.10.2026; the per-process snapshots once filled C: with 19 GB).

## Workflow 1 — render live citations

The manuscript uses pandoc citation syntax in the body:
`[@key]` parenthetical · `[-@key]` narrative (author already named in prose) · `[@key, 45–48]` locator.

```
python scripts/build_manuscris.py <manuscript.md> [--csl <style>] [--out <file.html|.docx>]
python scripts/build_manuscris.py <manuscript.md> --check   # fast: resolve keys only, no render
```

What it does: extract the cited keys → pull CSL-JSON for **only those keys** from Zotero (the
standard `?format=csljson` endpoint, where `id` == the citekey) → `pandoc --citeproc`. The
"References" list is produced automatically, so it contains **exactly what is cited** — nothing
stale, nothing extra. The output format follows the `--out` extension (`.html`, `.docx`, …).

- **`--check` = the fast edit-loop path.** Extracts keys, hits Zotero, prints which resolve and which
  are missing, then exits — **no pandoc render, no file written** (exit 0 = all resolve, 1 = some
  missing). Use it after adding/fixing a key to confirm it resolves; only do a full render once it's
  green. Skips the ~1–2 s pandoc+CSL pass that dominates a "did my key land?" check.

- **Swappable style:** `--csl apa` (or `ieee`, `american-sociological-association`, … from
  `~/Zotero/styles`, or a `.csl` path). Default = the bundled `references/chicago-author-date.csl`.
  Changing the style reflows both the in-text form and the list.
- **Verify by rendering**, then scan for leftover raw `[@` keys after any bulk citation edit.
- If a key is unresolved, the script lists it — add/fix it in Zotero and re-run (the marker and
  its place in the text stay; only the resolution updates).

## Workflow 2 — legislation & case law list

A CSL style handles the academic side only. Statutes and case law follow a custom format, so they
live in their own list, separate from "References".

Keep these items in a dedicated Zotero collection (e.g. `legislation`), as `statute` items with:
`shortTitle` (the English/working short title, e.g. "Fiscal Code (2015)"), `nameOfAct` (full
official title in the original language), `history` (the official gazette reference, e.g. Monitorul
Oficial nr. …), `dateEnacted`, `codeNumber` (e.g. "227/2015"), `extra` (locator/notes).

To **add** a statute (download its text, create the item, attach the PDF, file it into the
collection) do it as one batched write — `zot.write_session` + `zot.add_item`/`attach_pdf` (see
`references/zotero-schema.md`), and fetch the text via `references/ro-legislation-fetch.md`.
For an act on legislatie.just.ro, `scripts/adu_lege.py` does all of it — consolidated
form, form in force at a date, text-layer PDF, cached text — in one cycle. Resolve the collection id
**before** closing Zotero and keep everything in a single close/reopen cycle.

The two halves are checked against each other by `scripts/verifica_acte.py --colectie <KEY>`:
statutes missing a field the list needs (`shortTitle`, `nameOfAct`, `history`) or the rule needs
(`dateEnacted`, the version line in `extra`, `url`), duplicates (same code, same form), and forms
of one act sharing a short title — the list keeps all of them or none, since the text cites by short
title; how the text names one form is the author's decision. It runs at the end of `adu_lege.py`
and at the start of `build_legislation_list.py`.

```
python scripts/build_legislation_list.py --manuscript <md> --collection <COLLECTION_KEY> \
       [--source zotero|local --local-json <file>] [--write]
```

What it does: read the collection → **filter to what is actually cited in the body** → format →
sort chronologically and deterministically → (with `--write`) replace the
`# Legislation and case law` section (backup `.pre_leglist.bak`). Default is a dry-run preview.

- **Filter = real citation, not mention.** An item counts if its `codeNumber` appears in the body,
  or (for laws) its short title does. A purely narrative mention (no citation) is correctly excluded.
- **House format** (see §G): laws `{shortTitle}. {nameOfAct}, {history}.` · secondary legislation
  (HCL) `{nameOfAct}.` · cases `{nameOfAct}[, {history}].`
- **Deterministic order:** year → fine date (gazette publication; for cases, the decision date in
  `nameOfAct`) → type → issuer → act number. Same input ⇒ same output, regardless of API order.
- `--source local` reads a JSON mirror with the same logic — use it to preview offline (Zotero
  closed) and to **check parity** against the live `zotero` source before writing.

## Online sources without a displayed date

An online source that shows no date of publication or revision is cited with "n.d." and the access date (CMOS 18,
14.104; APA says the same and excludes footer copyright dates). When the server gives the file's last-modified date,
it is added after the access date as the server version — "accessed October 13, 2026 (server version of March 6,
2024)" / „accesat la 13 octombrie 2026 (versiunea de pe server din 6 martie 2024)" — and never replaces the
publication date (the author's rule, 13.10.2026). The access date is when the file was actually read, not the server
date. In the item: `accessDate` holds the access date (Zotero accepts only a date there), and Extra holds the line
"Versiunea de pe server: AAAA-LL-ZZ HH:MM GMT (Last-Modified)", which `adu_web.py` writes from the HTTP header.
`build_manuscris.py` joins the two at render time by passing the access date to citeproc as a literal; the CSL style
is untouched.

## Software (CMOS 18, 14.169)

A program has no text to read, so its item is verified against its counterpart in the project that used it: the
version actually run. For R packages that is the project's `renv.lock` and the installed package's `DESCRIPTION`
(`Version`, `Date/Publication`); `verifica_metadate.py --proiect <project dir>` checks the item's `versionNumber` and
`date` there and writes "Versiune verificată în renv.lock (<project>)" before the verification marker. The item is a
`computerProgram` with no attachment — nothing goes into Zotero storage for it. Chicago lists software under its
developer, with version, date and URL; a package that asks to be cited through a book (its `CITATION` file) is a
separate decision of the author, not a substitute for the software entry.

## Optional: OCR overlay for scanned PDFs

Some attachments are image-only scans (no extractable text) — every read either extracts
nothing or costs vision tokens. Burning a permanent, invisible OCR text layer fixes this once,
but it touches the actual library file, so **always ask first — never trigger it silently**.

When about to read a Zotero PDF and it turns out to be a scan (`ocr_overlay.detect_scan`),
stop and present the tradeoff: a rough time estimate for a one-time OCR overlay
(`ocr_overlay.estimate` — local compute, no API cost) vs. the rough token cost of reading it
via vision *this time, and every time after*, since the file stays unchanged if declined. Only
run `overlay_pdf` on explicit agreement. Full ask-first script, verification steps, and the
file-replace + DB-update recipe: `references/ocr-overlay.md`.

## Standing rule: check the PDF text cache first, every time

Not optional and not a special case — **every entry into Zotero starts at the cache.** The source
doesn't change between two reads, so a second extraction is redone work, and it is redone every
session. The order is always: check the memoized text, extract only on a miss, and **populate the
cache on that miss**. An extraction that leaves nothing behind leaves the next session exactly
where this one started. When the cache can't be populated because Zotero is open and in use,
extract so as not to block the user, but **say the memoization was skipped** — silence here is how
the cache stays empty forever.

Memoize as a child note on the attachment, keyed to `storageHash`, so a second read of the same
file reuses it instead of
re-extracting. `pdf_text_cache.get_cached(key)` is read-only (Zotero can stay open); only fall
through to a closed `write_session` + `pdf_text_cache.ensure_cached(...)` on an actual cache
miss — checking first, without closing Zotero, is the real efficiency win, not just skipping
re-extraction. Composes with the OCR overlay above: if `ocr_overlay.detect_scan()` says the PDF
is an unprocessed scan, run that ask-first flow first so there's a real text layer to cache.
Full recipe, note format, and the NBSP-fold gotcha: `references/pdf-text-cache.md`. About to
check many sources at once (e.g. verifying a thesis's citations against its sources)? Prefetch
the whole collection in one batch instead of hitting the cache one file at a time — see
"Pregătire în lot" in the same reference and `scripts/prefetch_collection.py`.

## Optional: library audit & duplicate-attachment reconciliation

Beyond citations, the skill can health-check the library and clean up duplicate PDFs.

`scripts/audit_library.py` (read-only, run with Zotero open) reports: referential integrity
across every child table; **phantom attachments** (imported_file stubs with no hash/folder —
the cause of the "Cannot change attachment linkMode" sync error, see `references/zotero-schema.md`);
**linked files missing from disk** (linkMode 2, absolute path or `attachments:` under
`baseAttachmentPath`), each listed with its path — the attachments a move to another computer
breaks — while web links (linkMode 3) are counted apart, outside the PDF total; items with 2+ present PDFs, each classified text-native vs image-scan; and metadata smells
(missing/placeholder titles, one DOI/ISBN stamped on many unrelated items). `--json out.json`
writes findings for the reconcile step.

```
python scripts/audit_library.py --json findings.json
python scripts/reconcile_attachments.py findings.json           # dry-run
python scripts/reconcile_attachments.py findings.json --apply   # collapse dups + trash phantoms
```

`scripts/reconcile_attachments.py` keeps the **editable** copy (text-native, or an OCR-overlaid
scan) per the priority rule, and **never loses an annotation**: it only collapses byte-identical
duplicates (moving unique annotations first) or trashes zero-annotation losers. A scan that
*carries* annotations is surfaced as an **OCR candidate** — run the ask-first `ocr_overlay` flow
so the scan becomes the editable keeper, rather than discarding it. Two annotated different-content
renditions are left for a human. Writes go through `write_session` (Zotero closed, auto-backup);
trashing is reversible (`deletedItems`).

## Working principles (why this setup)

These are the lessons that make the pipeline robust. Follow them; they prevent the classic failures.

**A. One source of truth.** Zotero only. No hand-made `biblioteca.json` / `refs.json` in parallel —
parallel copies diverge and go stale. Do **not** work from a flattened manuscript: running
`pandoc --track-changes=accept` freezes `[@key]` into plain "(Author Year)" text and severs the
link to Zotero. Keep the master with `[@key]` markers.

**B. Resolve keys via the bulk csljson API**, where `id` == the citekey (reads native citekey +
pinned `Citation Key:` / `Original Date:` from Extra). Do **not** use Better BibTeX `item.export`
(all-or-nothing, serves a stale cache after direct DB writes) or `item.search` (flaky on multi-word
terms).

**C. Pipeline = extract keys → bulk csljson → pandoc --citeproc --csl.** The References list is
generated, never authored. Verify by rendering and scan for residue after bulk conversions.

**D. Style is a swappable parameter** (`--csl`). CSL covers the academic apparatus only — the
legislation list is custom (Workflow 2), not CSL.

**E. Zotero data hygiene.** **Fiecare item poartă fișierul lui** — un item fără atașament e o
excepție autorizată de autor, nu starea normală. Nu deduce nimic din lipsa PDF-ului: nici că
sursa nu e accesibilă, nici că n-a fost procurată. Semnalează lipsa și mergi mai departe. Reprints (CMOS): put `Original Date: <year>` in Extra → renders
`Author (orig) year`. Ugly auto keys: pin `Citation Key: <key>` in Extra. Foreign-language titles
get Title-Cased by CSL unless you set `language: ro` (etc.). ALL-CAPS in the source passes through —
fix it at the item. Verify the resolved item (author + year + title); watch for duplicate/junk items.

**F. Writing to Zotero.** New items with no target collection → `/connector/saveItems` live
(Zotero open; it files them into the collection selected in the UI). New items into a specific
collection → `zot.adauga_itemi(items, colectie=...)`, which writes the collection explicitly. Editing/moving/deleting existing items, or annotations → write
`zotero.sqlite` directly with **Zotero closed + backup** (API is read-only for these). Batch **all**
DB writes for a task into **one** close/reopen cycle — resolve collection ids (`zot.find_collection`)
while Zotero is still open, then a single `zot.write_session` — because each cycle costs ~2–4 min.
See `references/zotero-schema.md` for tables, fieldIDs, and the `scripts/zot.py` helpers, and
`references/runtime-optimization.md` for the batching rule.

**Cheile cu caractere speciale se scriu între acolade: `[@{D’Orazio2019}]`.** Better BibTeX
generează chei care conțin apostrof tipografic (U+2019), iar în forma simplă `[@D’Orazio2019]`
pandoc rupe citarea exact la apostrof. Acolada delimitează cheia întreagă. Aceeași formă e
necesară pentru orice cheie cu punct, virgulă sau paranteză în ea.

`build_manuscris.py` recunoaște ambele forme. Dacă vezi în raport o cheie afișată **cu
acolade** — `@{Cheie}` — e o versiune veche a scriptului, care lua acoladele drept parte din
cheie; trage skill-ul.

**Zero chei rezolvate opreste randarea.** Nu e avertisment, e cod de eroare: un document fără
nicio citare și cu bibliografia goală nu e rezultat parțial. Cel mai des nu înseamnă „chei
greșite", ci sintaxă neînțeleasă sau colecție greșită — iar din raport cele două arată la fel.
Pentru cazul în care chiar asta vrei: `--allow-empty-bib`.

**Când itemul chiar lipsește, metadatele se iau din Crossref — și se verifică înainte de
inserare.** `scripts/fetch_metadata.py "<titlu>"` interoghează după titlu și întoarce
candidați ordonați, cu motivele retrogradării fiecăruia; nu alege niciodată singur.

Regula, fiindcă e o capcană măsurată, nu o precauție: **căutarea după titlu întoarce, înaintea
lucrării, notița ei de corectură și preprintul.** Pe cincisprezece titluri, două au ieșit
greșit — o notiță „Corrections: <titlul>", cu DOI propriu și o singură pagină, și un preprint
SSRN (`posted-content`, fără revistă, fără pagini) în locul articolului publicat un an mai
târziu. Amândouă ar fi intrat în bibliotecă arătând plauzibil și s-ar fi randat ca surse
inexistente.

**Cele două semne, verificate înainte de `add_item`: tipul și numărul de pagini.** O singură
pagină sau lipsa revistei sunt exact semnătura celor două capcane. Scriptul le marchează, dar
verificarea rămâne a ta — el propune, nu inserează.

**Never create a duplicate — file the existing item into the subcollection instead.** A
bibliography handed to you (export.bib, a reading list, a student's reference list) tells you
*what* to add, never that it's *absent* from the library — MyLibrary is one flat pool and an item
matching your title may already sit elsewhere, unfiled or in an unrelated collection, possibly
with a PDF you'd otherwise fail to attach. **Before any `add_item` call, search the live library
first** (Zotero still open, read-only, safe): `GET /items?q=<title words>&qmode=titleCreatorYear&itemType=-attachment`.
For each hit, check its attachments (`GET /items/<key>/children`) and its current collections
(`data.collections`, resolved via `GET /collections/<key>`) — prefer the copy that already has a
PDF over a bare metadata stub. If a match exists: resolve its key to an itemID with
`zot.item_id_for_key(cur, key)` inside your `write_session` and call `zot.add_to_collection(cur,
item_id, target_collection_id)` — **do not** `add_item` a second copy. Only `add_item` when the
search genuinely comes back empty. This was missed once (2026-07-09, a student's thesis bibliography):
8 of 11 references were re-created from bib metadata alone, and 7 of those already existed in
MyLibrary — several with PDFs already attached — sitting unfiled two days prior. The fix is a
merge (file the real item, trash the metadata-only stub, both a *content* decision — confirm with
the user before trashing, per the rulare/conținut split above), not a rewrite of the collection.

**G. Citing legislation (house style, consistent with CMOS shortened citations).** De la ce ne
abatem, cu numărul: CMOS 18 tratează actele normative în capitolul 14, la „Legal and Public
Documents" — **14.170** trimite la manualele de specialitate (Bluebook, ALWD) în locul unor reguli
proprii, **14.183** e secțiunea despre legi și statute, iar **14.172** cere forma de notă pentru
citările de tip juridic. Cuprinsul cu numerele e în `references/chicago/cuprins-cap14.txt`.

CMOS doesn't
prescribe a format for non-US legislation and usually puts laws in notes, not the reference list;
a separate "Legislation and case law" list is a deliberate house choice (standard socio-legal
practice). In-text = the **short title** (derived from the official title), treated as a shortened
note, not author–date. Carry the **year** at first mention, and at **every** mention for laws with
multiple versions in the text (e.g. two Fiscal Codes 2015/2003) so the short form stays
identifiable. Several forms of the SAME act are named by the act that amended them — see below. The list is separate, chronological, and filtered to what's actually cited.

**Ce intră în câmpurile unui act românesc, și de unde.** Regula pentru SUA, la care trimite
Chicago (14.170 → Bluebook), se aplică prin corespondență: legea adoptată se citează cu data
adoptării și publicarea oficială; un cod dintr-o bază de date, cu actualitatea declarată de bază.

| Ce | Câmp | De unde |
|---|---|---|
| data adoptării | `dateEnacted` | forma publicată (`DetaliiDocumentAfis/<act de bază>`), verificată cu formula de semnare din text |
| publicarea în Monitorul Oficial, și republicarea dinaintea formei | `history` | aceeași pagină; republicarea, din pagina ei (Legea 24/2000, art. 70 alin. (3): republicarea e textul consolidat oficial) |
| emitentul, apoi versiunea consolidată de pe Portal | `extra`, primele două rânduri | „Versiune consolidată la DD.MM.YYYY, Portal Legislativ (Ministerul Justiției)" — sursa fișierului, „instrument de documentare care nu produce efecte juridice" (art. 70^1 alin. (3)) |
| actele de modificare care au produs forma, fiecare cu Monitorul Oficial | `extra`, „Modificată prin: …" | notele de modificare din textul formei („(la ZZ-LL-AAAA, … a fost modificat de … publicată în MONITORUL OFICIAL nr. …)") cu data formei |
| perioada în care forma s-a aplicat | `extra`, „Aplicabilă: DD.MM.YYYY–DD.MM.YYYY" sau „Aplicabilă de la DD.MM.YYYY" | începutul e data consolidării (data de aplicare a modificării); sfârșitul, ziua dinaintea consolidării următoare, din istoricul complet al Portalului. La forma publicată a unui act modificat ulterior: „Aplicabilă până la DD.MM.YYYY", fără început, fiindcă textul poate amâna intrarea în vigoare |
| pagina formei | `url` | sursa textului |
| — | creatori | **niciunul**. Emitentul stă pe primul rând din `extra`, la toate actele (regulă unică, cerută de autor pe 12.10.2026, după ce o colecție avea 12 acte cu emitentul și ca autor și 44 fără). Legea se citează după denumire, nu după autor; un creator ar face stilul CSL să-l pună în locul autorului. `verifica_acte.py` semnalează abaterea, iar `--amana` o corectează |

**Forma unui act se numește prin decizie, nu prin editarea textului** (decizia autorului, 12
octombrie 2026). Consolidarea e o operație pe text; ce se citează e actul care a produs forma.
Așa o numește și Monitorul Oficial — „Legea nr. 227/2015 privind Codul fiscal, cu modificările și
completările ulterioare, inclusiv cu cele aduse prin prezenta lege" (Legea 296/2023, MO nr.
977/2023) —, iar nota de republicare enumeră actele de modificare, fiecare cu Monitorul Oficial
lui. Corespondentul Chicago e „as amended" (Chicago Q&A la 14.183) și, la o formă care nu mai e
în vigoare, data ediției codului (Maroonbook, regula 4.7(F)).

- **În listă:** „Legea nr. 227/2015 privind Codul fiscal, Monitorul Oficial … nr. 688 din 10
  septembrie 2015, cu modificările și completările ulterioare, inclusiv cele aduse prin Legea nr.
  296/2023 (Monitorul Oficial … nr. 977 din 27 octombrie 2023)". Când în listă rămân mai multe
  forme ale aceluiași act, rândul fiecăreia poartă și perioada: „; aplicabilă 01.01.2024–17.01.2024".
  Linkul formei de pe Portal încheie rândul, ca sursă a textului.
- **În text, când apar mai multe forme ale aceluiași act** (decizia autorului, 12.10.2026):
  - la prima mențiune: „Codul fiscal, modificat prin Legea 296/2023 (aplicabil 1.01–17.01.2024)";
  - la următoarele: „Codul fiscal (mod. Legea 296/2023, 1.01–17.01.2024)".

  Când forma a fost produsă de mai multe acte, textul numește actul care a modificat prevederea
  citată; lista le numește pe toate și recunoaște forma după oricare dintre ele, în oricare din
  cele două forme de mai sus.

Formele succesive ale unui act sunt iteme separate, legate „Related". `dateEnacted` e aceeași la
toate formele. Itemii aduși înainte de regulă n-au rândurile „Modificată prin" și „Aplicabilă";
lista îi afișează cu rândul de versiune, ca înainte, până sunt completați.

**H. Indirect ("apud") citations — house convention, NOT generic CMOS.** Secțiunea de la care ne
abatem e **CMOS 18, 14.160**, „Citations taken from secondary sources" — manualul cere să se
citeze sursa originală ori de câte ori se poate, iar când nu se poate, să se dea sursa de mâna a
doua cu formula „quoted in". Convenția de casă folosește „apud" în locul ei, cu aceeași cerință de
transparență. A source the author has
**not read directly** (unprocured book, a source known only through a secondary work) is cited
in-text with **`apud`** and a locator for BOTH works — `(Persson 2004, 43, apud Kouba 2018, 70)`,
`(Moodie & Eustace 1974, 201, apud Klemenčič 2011)` — and the indirectly-cited work is **NOT added
to the reference list**; only the source actually consulted (the "apud" target, here Kouba /
Klemenčič) gets a reference entry. This deliberately **overrides** CMOS 14.260 (which would list
both original and secondary and use "quoted in"). So: **directly-cited works → reference list;
indirectly-cited works → `apud` in-text only.** Do **not** flag an `apud`-cited source as a
"missing reference" — its absence from the list is correct. When such a source is later procured
and read directly, drop the `apud` and give it a normal entry.

## Bundled resources

- `scripts/build_manuscris.py` — Workflow 1 (live academic citations).
- `scripts/build_legislation_list.py` — Workflow 2 (legislation/case-law list).
- `scripts/audit_library.py` — read-only health check (integrity, phantom attachments,
  missing linked files, duplicate PDFs classified text-native/scan, metadata smells); `--json` feeds the reconciler.
- `scripts/reconcile_attachments.py` — collapse duplicate PDFs keeping the editable/OCR copy,
  never losing an annotation; trashes phantom stubs that break linkMode sync.
- `scripts/zot.py` — Zotero helper: read offline, detect user id, find items/PDFs **library-wide
  or scoped to a collection** (`find(query, collection="<name>")`, CLI `find "<q>" --in "<name>"`;
  subcollections included, empty query lists the whole collection, an unknown collection name
  raises instead of returning an empty list — "no such collection" and "no match" are different
  facts; a bare name shared by several collections raises too, so give the `parent/child` path
  or the key). Note it searches **metadata**, not PDF text: for text, prefetch the collection's cache
  and search that. Low-level
  direct-write helpers (`open_rw`, `get_value`, `set_field`, `touch`, `field_id`); and the batched
  write cycle (`write_session`, `add_item`, `attach_pdf`, `add_to_collection`, `find_collection`) —
  one close/reopen for a whole task's inserts + attachments; the backups each cycle leaves
  (`python zot.py copii [--muta]`, to the Recycle Bin once the write is verified); native highlights
  (`gaseste_fragment` read-only, then `adauga_evidentiere` in the write cycle — see
  `references/zotero-schema.md`).
- `references/chicago-author-date.csl` — default style. It does not update itself:
  `scripts/adu_stiluri.py` compares every `.csl` in `references/` with
  `https://www.zotero.org/styles/<id>` (not GitHub, whose copies are older), tells apart a
  date-only change from a change in the rules, shows the rule diff with `--arata`, and
  replaces a copy only with `--inlocuieste <id>`, on the author's request. State in
  `references/stiluri-csl.md`.
- `references/zotero-schema.md` — DB schema, fieldIDs, write/annotation patterns, and the
  `write_session` ingest recipe. **Read this before any direct database edit.**
- `references/ro-legislation-fetch.md` — where to actually download Romanian statute text
  (legislatie.just.ro and cdep.ro both extract; monitoruljuridic/lege5 are paywalled).
- `scripts/ocr_overlay.py` — scan detection (`detect_scan`), time/token tradeoff estimate
  (`estimate`), and the invisible-text-layer overlay (`overlay_pdf`) for scanned PDFs.
- `references/ocr-overlay.md` — the ask-first protocol for OCR overlay: when to offer it, what
  to ask, verification (0-pixel-diff check), and the file-replace + DB-update recipe.
- `scripts/pdf_text_cache.py` — memoize PDF text extraction as a child note keyed to
  `storageHash` (`get_cached`, `ensure_cached`, `extract_pdf_text`); `zot.add_child_note` /
  `find_child_notes` / `update_note` are the general-purpose note helpers it's built on.
- `references/pdf-text-cache.md` — the check-first-without-closing-Zotero usage pattern, note
  format, and why the NBSP fold-back matters for keeping cached text greppable.
- `scripts/prefetch_collection.py` — batch-populate the PDF text cache for every attachment in a
  named collection, in one `write_session` (e.g. before verifying a thesis's citations against
  its sources — zero new permission prompts afterward, however many sources are cited). See
  "Pregătire în lot" in `references/pdf-text-cache.md`.
- `references/runtime-optimization.md` — frictionless process control (one-time permission
  allowlist, canonical start/stop/ping command shapes, DB-write protocol, acceptance test,
  portability checklist). Follow its canonical command shapes so the prefix-matched allow rules
  keep working.
- `scripts/log_perm.py` — permission-prompt monitor (hook target). At the end of each iteration,
  review `~/.claude/perm-requests.jsonl` and propose one allowlist fix per prompt cluster — the
  ritual is specified in `references/runtime-optimization.md` §"Accept monitor".
