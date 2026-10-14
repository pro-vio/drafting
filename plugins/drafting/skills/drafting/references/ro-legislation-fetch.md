# Fetching Romanian legislation text (source recipe)

Downloading the text of a Romanian statute is a solved lookup, not an exploration — but the
obvious portals fail in non-obvious ways. Try sources in this order; stop at the first that yields
extractable text. (Derived from a live run that burned ~30 min on trial-and-error across all four.)

| Source | URL shape | Verdict |
|---|---|---|
| **cdep.ro** (Chamber of Deputies legislative DB) | `http://www.cdep.ro/pls/legis/legis_pck.htp_act_text?idt=<ID>` | **BEST.** Returns the full act as plain HTML — convert to text/markdown with pandoc. Old acts included (e.g. Legea 57/1968). Needs the internal `idt` (see below). |
| **legislatie.just.ro** (official consolidated portal) | `https://legislatie.just.ro/Public/DetaliiDocument/<ID>` (or `DetaliiDocumentAfis`) | **Consolidated text, and it does extract from raw HTML** — see the recipe below. |
| **monitoruljuridic.ro** | — | **Paywall.** Skip. |
| **lege5.ro** | — | **Paywall / registration wall.** Skip. |

## legislatie.just.ro: extract per article, and take the *consolidation's* own id

An earlier version of this file said the portal's body could not be scraped and required a
browser or OCR. That was wrong, and it cost a run. The body **is** in the raw HTML, marked up
per article: each heading is `id="id_art<N>_ttl"` and each body `id="id_art<N>_bdy"` (same for
`id_cap…`, `id_sec…`, `id_anx…`). Slice the HTML between consecutive markers and strip tags —
you get the act article by article, which is exactly the granularity a locator needs.

**The trap that matters more than the scraping: two URLs, two different forms.**

    /Public/DetaliiDocument/<id>       CONSOLIDATED text, carries "(la DD-MM-YYYY, … a fost
                                       modificat de …)" amendment markers
    /Public/DetaliiDocumentAfis/<id>   text AS ORIGINALLY PUBLISHED, no markers

Measured: Legea 24/2000 — 86 articles vs 81. Legea 273/2006 — 133 vs 86. The `Afis` page looks
entirely plausible and never says it is the superseded form. **Always start from the first URL.**

**Third trap, on large acts: the `DetaliiDocument` page may not carry the text.** Measured on
7 October 2026, two patterns, and in both the text sits on a `DetaliiDocumentAfis/<other id>`
page that the first page links to. That `Afis` page carries the *consolidated* text, with its
markers — unlike `Afis` on the act's own id, which stays the original form.

- *The page is only a header.* Codul de procedură fiscală: consolidation 309358 has no article
  marker at all; the text — 430 articles, amended up to 08-05-2026 — is on `Afis/309359`,
  linked from the title in the header block (`S_HDR` inside `div_Formaconsolidata`). The base
  page 170005 does the same, to 312603.
- *The annex is only a link.* HG 1/2016: the page has the decision's four articles, while the
  annex — the methodological norms, i.e. almost all of the act — is a title linking to another
  `Afis` page (306834 from consolidation 306833; 307847 from base page 174822).

The link is **not** "id + 1": from the base page, id + 1 is another act. Follow only the links
on the page. Small acts are unaffected: Legea 554/2004 (266941) carries its 64 article markers
on `DetaliiDocument` itself. `adu_lege.py` does this in `forma()`, and lists every page the text
came from in the PDF header.

Second, smaller trap: the "istoric consolidări" list can lag the act's own page. For Legea
273/2006 the page carried an amendment of 25-02-2026 that the most recent listed consolidation
(01-01-2026) did not. So fetch both, read the latest `(la DD-MM-YYYY` marker out of each, and
keep the newer. `scripts/adu_lege.py`, in this skill, implements exactly this and
files the result as a `statute` item with a text-layer PDF and cached text.

Note also that the portal writes cedilla forms (`ş`, `ţ` — U+015F/U+0163), not comma-below.
That is the source's orthography, not a conversion loss; normalise when searching, not in the
stored text.

**If you build a PDF from the extracted text**, do not use a PDF base-14 font: WinAnsi has `â`
and `î` but not `ă`, `ș`, `ț`, and the loss is silent. Load a TTF (`fitz.Font(fontfile=…)` to
measure, `page.insert_font(...)` per page), then re-extract and count the diacritics.

## Finding the cdep `idt`

The `idt` is cdep's internal act id, not the law number. Resolve it first:
- Search cdep's legislative DB (`http://www.cdep.ro/pls/legis/legis_pck.frame`) or a plain web
  search `site:cdep.ro <lege nr>/<an>`, then read the `idt=` off the result URL.
- Sanity-check you got the right act/version (republished vs. original) before ingesting — cdep
  carries multiple forms of the same law.

## Then ingest it

Convert and file in one step — no separate hand-rolled SQL:

```bash
# 1. fetch + convert to markdown/text (cdep HTML → readable)
curl -s -A "Mozilla/5.0" "http://www.cdep.ro/pls/legis/legis_pck.htp_act_text?idt=27845" \
  | pandoc -f html -t markdown -o "legislatie/Legea_57_1968.md"
# (or -t pdf via wkhtmltopdf/weasyprint if you want a stored PDF to attach)
```

Then create the `statute` item + attach the file, inside one `write_session` (see
`zotero-schema.md` → "Add a statute"). Set `shortTitle`, `nameOfAct`, `codeNumber` (`57/1968`),
`dateEnacted` (the adoption date, read from the *published* form, `DetaliiDocumentAfis/<base id>`),
and `history` (the Monitorul Oficial publication, plus the republication that precedes the form)
so the `build_legislation_list.py` house format renders correctly. A consolidated form from the
Portal is only the file's source — under Legea 24/2000, art. 70^1 (3), a documentation tool with
no legal effect — so it goes on the second line of `extra` ("Versiune consolidată la DD.MM.YYYY,
Portal Legislativ …"), with the form's page in `url`. See `citari-zotero.md`, section G. An act
with no consolidation history and no amendment markers in its text carries "Forma publicată, fără
modificări ulterioare" instead. When `--la-data` falls before the first consolidation of an act that was
amended later, `adu_lege.py` brings the published form from `DetaliiDocumentAfis/<base id>` (only if
that text has no amendment notes; `--forma-publicata` forces it for a page whose own publication the form list does
not show, such as a republication), with "Forma publicată, Portal Legislativ …" and "Aplicabilă până la
DD.MM.YYYY" (the day before the first consolidation; the start is not written, because the text may
defer its own entry into force), `url` on the `Afis` page, and links it as Related to the other forms. An act the
Portal lists without any consolidation is written "Forma publicată, Portal Legislativ …, care nu listează
modificări ale actului", not "fără modificări ulterioare": the Portal keeps no history for old acts (a 1968 law
that was republished shows none). `--la-item CHEIE` attaches the form to an existing item without a file instead
of creating one; fields the Portal cannot give (a Buletinul Oficial publication) stay as the item has them. `adu_lege.py` refuses, before writing, an item that would lack any
of these fields, and derives a missing short title from the act type and number ("Legea 76/2002",
"OUG 111/2010"); a customary name ("Codul fiscal") is given with `--scurt`.

A consolidated form is named by the decision that produced it, as the Monitorul Oficial names it
("cu modificările și completările ulterioare, inclusiv cele aduse prin …"): `adu_lege.py` reads the
amending acts from the form's own amendment notes ("(la ZZ-LL-AAAA, … a fost modificat de … publicată
în MONITORUL OFICIAL nr. …)") and writes them to `extra` as "Modificată prin: …", with the period the
form applied ("Aplicabilă: DD.MM.YYYY–DD.MM.YYYY", the end being the day before the next consolidation).
The consolidation history is read whole (`consolidari_complete`): the base page omits its newest entry
(shown in red, without a link) and, for heavily amended acts, part of the older ones. See
`citari-zotero.md` §G.

**Content stays the user's call** (the rulare/conținut split): if you can't confirm you fetched the
exact version cited, surface that — don't ingest a best-guess act.
