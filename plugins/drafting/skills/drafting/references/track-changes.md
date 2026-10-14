---
name: scriere
description: >-
  Edit a Word (.docx) manuscript as TRACKED CHANGES (author "Claude") via a reliable lxml
  round-trip, so the author accepts/rejects every change instead of receiving a rewrite. Use
  whenever someone is revising a paper, article, thesis, report, or any .docx and wants
  suggestions as tracked changes rather than a clean overwrite — e.g. "implement the reviewer's
  comments as track changes", "apply these edits but let me accept them", "reply to the comments
  in this Word doc", "corectează exprimarea fără să-mi schimbi vocea", "insert this citation
  as a tracked change", "add a footnote for this source", "turn this line into a heading". Also
  the right tool for threaded comment replies, footnotes, and proofreading that preserves the
  author's style. Reach for it even when "track changes" isn't said but the user is round-tripping
  a Word document with a human reviewer in the loop. Not for creating a .docx from scratch
  (use the docx skill) and not for the citation/bibliography pipeline (see `references/citari-zotero.md`).
---

# Scriere — Word round-trip with tracked changes

**Referințele se utilizează, nu se afișează.** Când editarea atinge citări sau bibliografie:
o sursă se citează doar dacă textul îi folosește definiția, procedura sau cifra — niciodată
ca împăunare. Textul se propune întâi; autorul citește sursa după, deci la afirmații egale
câștigă sursa mai ușor de procurat și de citit — adesea articolul; cărțile și capitolele de
carte intră doar unde afirmația folosită trăiește doar acolo. Fără alternative „de
acoperire".

Edit a manuscript the way a careful co-author would: **every change is a tracked change**
(author "Claude"), so the human opens the .docx in Word and accepts or rejects each one. This is
a *round-trip*, not a rewrite — the author stays in control of what becomes canonical.

The golden rule everything follows: **you suggest, the author disposes.** Never hand back a
silently rewritten document. If something can't be expressed as a tracked change (text baked into
an image, say), flag it in a comment reply and handle it separately — don't smuggle it in.

## Prerequisites

- **pandoc** on PATH (for the mandatory validation step; it has built-in docx support).
- **lxml** (`pip install lxml`) for the XML editing helper.
- The author's name for changes is "Claude" by default — override via `Docx(author=…)`.
- **Frictionless read/validate loop**: unpacking, reading comment ranges, and pandoc validation
  should cost zero permission prompts — see `references/runtime-optimization.md` for the
  one-time allowlist and the rulare/conținut split (the author's own accept/reject in Word is
  already the content gate; nothing else here needs one).

## The core helper

`scripts/docx_track.py` exposes a `Docx` class that wraps an *unpacked* .docx and writes tracked
changes. Drive it from a tiny per-document script (the appliers are short and disposable; the
helper is the reusable part). Pattern:

```python
from docx_track import Docx
d = Docx.unpack("Manuscript.docx", "_work", date="2026-06-19T20:00:00Z")

# tracked text edits (operate on document.xml)
d.replace("teh association", "the association", "typo")     # del + ins
d.delete(" (citare Shlager)", "remove placeholder")          # pure deletion
d.insert_after_text("as outlined", " (Sandler 2015)", "cite")
d.insert_after_ins("value sym", " (Olson [1965] 2003)", "cite after the user's own insertion")
d.fix_in_ins("simetry", "symmetry", "typo inside user's insertion")
d.set_heading("Exploitation of the largest by the smallest", 3, "promote to H3")

# NEW anchored comment — paragraph-level; creates comments.xml etc. if the doc has none;
# occurrence="last" when the anchor is a heading that also appears in the TOC
d.add_comment("unique anchor text", "Reviewer note on this paragraph.", "note")

# threaded comment reply (links to the existing comment by its id)
d.reply_to_comment(13, "Inserted (Sandler 2015, 190) after the definition.", "reply #13")

# footnote (press source, Chicago — see Citations below)
d.add_footnote("Opinia Timișoarei",
               'Author, "Title," Source, Month Day Year, URL.', "press footnote")

d.report()                       # prints OK/MISS per edit
d.repack("Manuscript_track.docx")
```

Then **always validate** (next section). Each method returns `True`/`False` and logs `OK`/`MISS`
(`OK … (span)` = matched across runs). `replace`/`delete`/`insert_after_text` try a single direct run
first, then fall back to a **run-spanning** match across consecutive plain runs, with ASCII↔typographic
quote/space/dash normalization — so a plain-text needle still hits the document's `’ “ ” – —` or NBSP.
A `MISS` now almost always means the anchor sits **inside the user's own `w:ins`** (use
`insert_after_ins` / `fix_in_ins`) or the phrase is interrupted by a tracked change / hyperlink —
see `references/ooxml-track-changes.md`.

## Starea fișierului se citește, nu se presupune

Între două runde, autorul lucrează în Word: acceptă, respinge, șterge, salvează. Fișierul de pe
disc nu e cel pe care l-ai lăsat, iar deosebirea nu se vede în text — se vede în aparat.

**Prima operație a fiecărei reluări, înainte de orice editare:** numără `w:ins` și `w:del` din
`document.xml`. Un număr căzut la zero înseamnă că autorul a dat Accept All și că textul tău de
ieri e acum text obișnuit; ancorele care mergeau cu `insert_after_ins` / `fix_in_ins` trec acum
prin `insert_after_text` / `replace`, și invers.

Pe 13 august 2026 asta a costat patru editări ratate la rând și o extindere de helper pornită
pe diagnostic greșit: autorul acceptase în Word în timpul lucrului, iar semnul — 23 de inserări
căzute la 6 — era vizibil de la prima citire. **Un MISS repetat pe ancore care existau ieri se
tratează citind starea documentului, nu reparând unealta.**

## Workflow — implementing a reviewer's comments

1. **Unpack reliably.** `Docx.unpack` uses Python `zipfile`, never the shell `unzip` (which leaves
   a stale `document.xml` on paths with `§`/diacritics — a silent, costly trap).
2. **Read what each comment actually covers** — the text between its `commentRangeStart/End`, not
   just the comment's wording. Act on the substance, not the label.
3. **Execute each comment as a tracked change** (insert/delete/replace/heading), and optionally add
   a short threaded reply saying what you did, so the author sees the response in context.
4. **Anything that can't be a tracked change** (e.g. text inside a figure image) → reply explaining
   why + handle separately (regenerate the figure from its SVG; see the project's figure convention).
5. **Repack and validate.**

## Workflow — corectarea exprimării, fără să se schimbe vocea

**Nu e o trecere de gramatică**, iar numele care stătea aici — al unei unelte de verificat
gramatica — spunea altceva decât ce se face. Instrucțiunea autorului: *„nu știu ce este grammar la
tine, dar aici este exprimare, sensul cuvintelor, nu regulile de gramatică."* Numele unui produs
pus în locul operației e chiar tiparul din lista de AIeză: cititorul rămâne să ghicească ce se
face.

Ce se corectează: **cuvântul folosit cu alt sens decât cel voit** — calcul din engleză, metafora
peste un termen consacrat, verbul mai slab decât acțiunea —, plus greșelile limpezi: scăpări de
tastare, acord, prepoziții, diacritice lipsă, cuvinte dublate. Formele măsurate, cu perechile
respins/acceptat, sunt în `stil-proteasa`.

Regulile de gramatică intră numai când chiar sunt încălcate; ele nu sunt subiectul trecerii. **Do not** rewrite correct sentences "to sound better" and do
not touch the author's register or voice — that is theirs. When you can't tell whether something is
a typo or a style choice, leave it (or ask). Everything still goes in as tracked changes so the
author can reject anything. Collect the fixes as `(old, new, label)` triples and loop `d.replace`.

## Citations (defer the apparatus to `references/citari-zotero.md`)

This skill places citation **text** as tracked changes; it does not manage the bibliography.

- **Academic** → the live form comes from the citations half of this skill (`references/citari-zotero.md`) (`[@key]` in the master
  `.md`, rendered author–date). Here you insert the rendered "(Author Year)" as a tracked change at
  the right spot.
- **Legislation / case law** → in-text short title + a separate chronological list; that list is
  generated by the citations half. This skill just inserts the in-text short title.
- **Press / news** → a **footnote** (Chicago): `First Last, "Title," *Source*, Month Day, Year,
  URL.` Verify the real author and date — never fabricate; prefer an outlet/author already used in
  the document when it fits. Insert with `d.add_footnote`.

(House citation rules live in the project's `CONVENTII.md §7`; the style is Chicago.)

## Validation — never skip it

A hand-edited `document.xml` can be malformed in ways Word silently repairs or rejects. Prove the
file is sound:

```
python scripts/validate_docx.py Manuscript_track.docx --grep "Sandler 2015"
```

It runs pandoc three ways — plain (opens), `--track-changes=accept` (your edits appear),
`--track-changes=reject` (the original returns) — and `--grep` confirms a specific insertion
landed in the accepted text. Exit 0 = sound.

**Ce dovedește, și ce nu.** Validarea spune că fișierul e valid, nu că textul e bun. Un paragraf
de patru mii de semne, făcut din trei inserări lipite una de alta, acceptă și respinge perfect;
la fel și o ciornă veche rămasă în document lângă versiunea care o înlocuia. Ambele au trecut
validarea pe 13 august 2026 și s-au văzut abia la citire. **După fiecare rundă se mai fac două
verificări:** numărul de `w:ins` copii direcți ai paragrafelor atinse (mai mult de unul înseamnă
că blocurile s-au strâns) și o căutare de fraze duplicate în textul acceptat. For heavily-edited files, also run a redlining/XSD
validator against the ORIGINAL docx if the project has one (`_docxtools/office/validate.py`).

### Structura se citește din arhivă, nu dintr-o randare

`pandoc -t plain` spune ce **text** are documentul; nu spune ce **stiluri** poartă. Diferența
contează exact acolo unde greșeala e tăcută: titlurile scrise corect ca `Heading 1` ies din pandoc
ca text îngroșat, identic cu un paragraf pe care cineva l-a bolduit de mână. Cine se uită la
randare nu poate deosebi cele două cazuri.

Se citește direct din arhivă, într-o linie:

```bash
unzip -p Manuscript.docx word/document.xml | grep -o 'w:pStyle w:val="[^"]*"' | sort | uniq -c
```

Verificat pe două fișiere, 29 august 2026. Pe un docx generat din markdown: `1 Heading1`,
`1 Heading2`, `2 FirstParagraph` — deci stilurile există și sunt cele așteptate. Pe un document
scris de om în Word: **zero**, pe 55 KB de XML.

**Zeroul ăla e un rezultat, nu o defecțiune** — documentul chiar n-avea niciun stil numit, fiindcă
fusese formatat direct, cu bold și mărimi puse manual. Distincția merită ținută minte, fiindcă
ieșirea goală arată la fel cu o comandă care n-a rulat: dacă vrei să fii sigur că arhiva chiar s-a
citit, numără octeții întâi (`unzip -p ... | wc -c`).

Rețeta e și mai rapidă decât randarea, deci nu e doar o soluție de rezervă pentru mașinile fără
LibreOffice — e verificarea potrivită pentru orice întrebare despre structură.

## Working principles (why this setup)

**A. Round-trip, never rewrite.** The deliverable is a .docx of *suggestions* the author disposes
of. Silent rewrites destroy the author's control and trust. One batch of edits = one line saying
what changed, so reverting is clean.

**B. The author decides what's canonical.** When work moves into Word, that .docx becomes the
source of truth for that step; any master `.md` / Zotero pipeline is for regeneration if the author
returns to it. Surface divergence between them — don't paper over it.

**C. Propose → validate → apply, one change at a time.** No scope creep, no reopening a section the
author has closed. Suggest structural/conceptual changes and wait for a yes; apply small wording
directly. (This mirrors the project's `CONVENTII.md`.)

**D. Match on a unique substring; the helper handles run splits for you.** Word splits text into runs
unpredictably, so the helper tries a single direct run first, then spans consecutive plain runs, and
normalizes ASCII↔typographic quotes/spaces/dashes — you no longer have to copy `’ “ ” –` exactly or
pre-shorten needles. Keep the needle **unique** in the paragraph (it edits the first match). A `MISS`
means the anchor is inside the user's own `w:ins` (use `insert_after_ins` / `fix_in_ins`) or is broken
by a tracked change / hyperlink mid-phrase.

**E. Preserve the voice.** In proofreading, change only what is objectively wrong. Style, rhythm,
and register belong to the author; uncertainty resolves toward leaving the text alone.

**F. Reliability over shortcuts.** Unpack with `zipfile`, set `xml:space=preserve` on edge spaces,
keep change ids unique, repack to a fresh path (Windows locks working dirs), and **always validate**
with pandoc before returning the file.

**F-bis. O reformulare cerută înseamnă înlocuire, nu adăugare.** Când autorul spune că o frază
nu se înțelege și ceri voie să o rescrii, sarcina are două jumătăți: pui versiunea nouă **și**
scoți versiunea pe care o înlocuiește, în aceeași trecere. Pe 13 august 2026 am făcut doar prima
jumătate, iar documentul a rămas cu ambele variante ale aceluiași paragraf, la doi pași una de
alta. Ce se înlocuiește se caută înainte de a insera, ca să știi ce ai de scos.

**G. Logic and structure belong to the author; you only have probabilities.** The author's own
decisions — argument, structure, wording they dictate as canonical, the shape of a section — are
not yours to complete, polish, or reorganize, even slightly, even if it reads as an improvement.
Model output is inherently probabilistic completion; exploration and verification tasks (finding a
source, checking a claim, summarizing what's already in the document) are a legitimate use of that
— but authored logical/structural content is not, because there the author's exact intent is the
only correct answer, and a probabilistic "close enough" is a silent corruption of it, not a
suggestion. **Substance is his; the wording is mine unless he says otherwise.** A style pass may
not complete an unfinished thought, add a claim he did not make, or move content to another
section — those are the author's decisions, and a probabilistic "close enough" there is a silent
corruption, not a suggestion.

When he *does* ask for his exact words ("word for word", "this text as it is"), **copy the literal
string from the conversation, never retype it from memory of what it meant.** If no exact string
exists yet (he said it verbally, never wrote it down), ask him to write it — do not compose it
yourself, not even to finish an obviously incomplete sentence. Before writing anything presented as
the author's own words, check each phrase against the conversation: is this literally there, or did
I complete it? A "sounds like what they meant" is not a yes.

This was missed three times in one session (2026-07-09, a student's thesis): a tracked-insert summary of
the author's own dictated design was paraphrased **and restructured**; a second insertion
(after explicit approval) regrouped the content under the wrong heading; and a sentence the author left as "the classic methods are
flawed" was silently completed with "by subjectivity and the Hawthorne effect" — pulled from the
*student's own thesis text*, not from anything the author had said, blending a third source into
what was presented as the author's exact dictation. All three read as harmless improvements in the
moment; all three were content the author never approved, sitting in a graded student's thesis.
What they share is the part that stays forbidden under the current default: not the rewording, but
the added and rearranged substance.

**G-bis. Întrebarea „verbatim sau pe stil?" nu se mai pune.** Decizia autorului, 13 august 2026:
**verbatim doar la comanda lui explicită; altfel, se reformulează pe stil.** Repunerea ei ca
întrebare o redeschide la fiecare inserare, iar într-o rundă cu multe schimbări ale lui asta
înseamnă zeci de opriri, fiecare cu un ocol prin el pentru un răspuns pe care skill-ul îl are
scris.

Comanda se recunoaște după cuvinte, nu după ton: „cuvânt cu cuvânt", „textul ăsta exact", „așa
cum l-am scris", „nu-l atinge". Fără ele, textul lui e materie primă: intră reformulat în
registrul din `stil-proteasa`, ca orice text pe care îl scriu pentru el.

Ce **nu** face reformularea, indiferent de implicit: nu completează un gând neterminat, nu
adaugă o afirmație pe care el n-a făcut-o și nu mută conținutul în altă secțiune. Se schimbă
cuvintele, nu ce spun ele. Granița asta e principiul G, iar prețul ei e povestea de mai sus.

Al doilea pas, la fel de important și în ambele cazuri — reformulare sau comandă de verbatim:
**care versiune a lui?** Autorul se răzgândește des, deci prima formulare găsită
căutând înapoi e rareori cea bună. Se caută **toate** locurile unde a dat formularea și se ia
**ultima**, apoi se spune într-o linie care a fost luată — „am pus varianta din mesajul despre
X", nu o întrebare. Când ultima versiune nu doar sună altfel, ci **spune** altceva decât una de
dinainte, se spune și asta, tot ca afirmație: el vede imediat dacă e răzgândire sau scăpare.

Aceeași căutare se face și pentru indicațiile despre o secțiune, nu doar pentru textul de
inserat: ultima indicație o înlocuiește pe cea dinainte, iar o instrucțiune veche nu mai e
mandat. Reflexul greșit e să lucrez de pe prima formulare pe care am reținut-o și să nu mai
verific — iar asta nu se vede la validare, fiindcă documentul iese corect, doar că poartă o
versiune pe care autorul a abandonat-o.

**Implicitul de mai sus e pentru textul care abia intră în document, nu pentru cel care e deja
acolo.** Ce a scris autorul în manuscris — inserările lui urmărite, editările făcute în Word
între runde — rămâne sub principiul E: se schimbă doar ce e obiectiv greșit (typo, acord,
diacritic lipsă), niciodată registrul sau ritmul. `fix_in_ins` e unealta acelei treceri de
corectură, nu una de restilizare. Reformularea pe stil se aplică la ce dictează el acum pentru
inserare, nu la manuscrisul lui.

## Bundled resources

- `scripts/docx_track.py` — the reusable `Docx` helper: `unpack`/`repack`/`save`; tracked
  `replace`/`delete`/`insert_after_text`/`insert_after_ins`/`fix_in_ins`/`set_heading`;
  `add_comment` (new paragraph-anchored comment; auto-creates the comment parts);
  `reply_to_comment`; `add_footnote`; `report`.
- `scripts/validate_docx.py` — the mandatory pandoc accept/reject/plain check (+ `--grep`, `--show`).
- `references/ooxml-track-changes.md` — OOXML schema, the run-splitting gotcha, comment-threading
  and footnote part-by-part patterns, packing/validation notes. **Read before extending the helper.**
- `references/runtime-optimization.md` — the batched read/validate permission allowlist (python,
  pandoc, pip install lxml) and the rulare/conținut split for this skill.
