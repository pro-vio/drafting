# Runtime optimization: frictionless process control

Covers both halves of `drafting` — Zotero process control and the docx read/validate loop.
Tested and validated 2026-07-03 (Windows 11, Zotero 7, Claude Code desktop). Goal: **one
permission accept from the user at setup, zero prompts afterwards** for everything that is
*running software* — while every *content* decision (what enters an analysis corpus, what gets
cited, which edit is right) stays with the user.

## The rulare / conținut split (design principle)

| Layer | Examples | Who decides |
|---|---|---|
| **Rulare** (running) | start/stop/restart Zotero, ping the local API, run skill scripts, pandoc renders; `Docx.unpack` (Python zipfile), reading a comment's range text, `repack` to a **new** path | Claude, autonomously, after a one-time allowlist accept |
| **Conținut** (content) | corpus inclusion when a hit doesn't match the search spec 100%, citing, editing items; which edits to propose, their wording, what is in scope | the user, always |

On the docx side the content gate is already built into the deliverable: the author's own
accept/reject **inside Word** is the decision point (SKILL.md principles A/B), so nothing there
needs a second gate on Claude's side. What still deserves a prompt is what is hard to reverse
*before* the file ever reaches Word — overwriting the original path, or deleting anything.

## One-time allowlist (user-level `~/.claude/settings.json`)

Rules are **prefix-matched**, so the skill must always issue commands in these exact canonical
shapes. Adjust paths per machine.

```json
"permissions": {
  "allow": [
    "Bash(curl -s http://127.0.0.1:23119/*)",
    "Bash(curl -s \"http://127.0.0.1:23119/*)",
    "Bash(curl -s http://localhost:23119/*)",
    "Bash(curl -s \"http://localhost:23119/*)",
    "Bash(tasklist *)",
    "Bash(python *)",
    "Bash(pandoc *)",
    "Bash(grep *)", "Bash(ls *)", "Bash(ls)", "Bash(head *)", "Bash(tail *)",
    "Bash(wc *)", "Bash(find *)", "Bash(sed -n *)", "Bash(diff *)",
    "Bash(echo *)", "Bash(cd *)", "Bash(mkdir *)", "Bash(cp *)",
    "Bash(git status*)", "Bash(git log*)", "Bash(git diff*)", "Bash(git remote*)", "Bash(git rev-parse*)",
    "WebSearch",
    "WebFetch",
    "PowerShell(Start-Process \"C:\\Program Files\\Zotero\\zotero.exe\")",
    "PowerShell(taskkill /IM zotero.exe)",
    "PowerShell(tasklist *)"
  ],
  "additionalDirectories": [
    "<the project folders the analysis reads/writes>",
    "C:\\Users\\<user>\\Zotero"
  ]
}
```

**v2 lessons (first live run generated a pile of prompts the v1 list missed):**
- Rules are **literal prefix matches**: `curl -s "http://…"` (quoted URL) does NOT match a rule
  written for the unquoted form — allow both spellings, or standardize and never deviate.
- **Pipes and `&&` chains prompt if ANY segment is unlisted** (`curl … | python -c …` needs
  `python` allowed too). Narrow per-script python rules don't survive real work (inline `-c`,
  scratchpad scripts) — either allow `Bash(python *)` or accept prompts.
- Read-only utilities (`grep`, `ls`, `head`, `tail`, `wc`, `find`, `sed -n`, `diff`, read-only
  `git`) each need their own rule.
- `Edit`/`Write` outside the session's project dir prompt — list the working folders (and the
  Zotero data dir, for DB backup/restore) in `permissions.additionalDirectories`.
- Deliberately NOT allowlisted: `rm`, `taskkill /F`, bare `sed`/`cat` (can write via `-i`/redirect),
  `git commit/push` — destructive or publishing actions stay behind a prompt.

Deliberate safety boundary: **force kill is NOT allowlisted.** `taskkill /F /IM zotero.exe` can
corrupt `zotero.sqlite`, so it still requires an explicit user accept. Same philosophy for any
direct DB write: graceful close → backup `zotero.sqlite` → write → restart → verify (see §F of
SKILL.md and `zotero-schema.md`).

**v3 lessons (aggregated accept-monitor pass across all concurrent sessions, 2026-07-05):** these
are command-**shape** problems, not missing rules — the base commands were already allowlisted,
so don't add new entries for them; fix the shape instead, in every session, not just one.

- **Never invoke Python via heredoc** (`python - <<'EOF' ... EOF`), even inside a `cd X && ...`
  chain. This was **half of all prompts in one day's aggregate log (54/106)** — `cd`, `pandoc`,
  and `python` are each individually allowlisted, but the permission matcher splits a compound
  command on `&&` and checks each segment; the heredoc form doesn't match `Bash(python *)` cleanly
  inside a chain the way a plain `python script.py` does. Fix: write the script to a scratch
  `.py` file first (via the Write tool), then a single simple `cd X && python script.py` — no
  heredoc, ever. See `feedback_forma_python_encoding` in project memory for the fuller writeup.
- **A literal `\|` (regex alternation) inside a quoted grep pattern** — including inside a real
  `ls … | grep "…\|…"` pipe — gets misread by the same segment-splitter as an additional shell
  pipe boundary, producing a fake trailing "segment" that matches nothing. ~12/106 prompts in the
  same aggregate pass. Fix: split into two separate `grep` calls instead of one pattern with `\|`,
  when running through the Bash tool. Low stakes (grep is read-only) — skip the workaround if a
  single occasional prompt doesn't bother you.

## Canonical commands (always these shapes, or the prefix rules won't match)

| Action | Command |
|---|---|
| Start Zotero | `Start-Process "C:\Program Files\Zotero\zotero.exe"` (PowerShell) |
| Graceful stop | `taskkill /IM zotero.exe` (PowerShell; **never** `/F` unprompted) |
| Is it running? | `tasklist //FI "IMAGENAME eq zotero.exe"` (Bash) |
| Wait for API after start | `curl -s http://127.0.0.1:23119/connector/ping --retry 15 --retry-delay 2 --retry-all-errors -o /dev/null -w "HTTP %{http_code}\n"` |
| Read library | `curl -s "http://127.0.0.1:23119/api/users/<userID>/items?..."` |

Docx side, same rule — keep to these shapes:

| Action | Command |
|---|---|
| Unpack | `Docx.unpack("Manuscript.docx", "_work", date="...")` — Python `zipfile`, **never** shell `unzip` (leaves a stale `document.xml` on paths with `§`/diacritics — a silent, costly trap) |
| Validate | `python scripts/validate_docx.py Manuscript_track.docx --grep "..."` — pandoc three ways (plain / accept / reject) |
| Repack | `d.repack("Manuscript_track.docx")` — always a **new** filename, never overwrite the original in place (Windows also locks working dirs) |

`Bash(pip install lxml)` belongs in the allowlist alongside `python *` and `pandoc *` for this
half. Deliberately NOT allowlisted here either: repacking **over** the original path, deleting
anything (`_work` dirs, backups, the original .docx), and `git commit` / `git push`.

Notes from testing:
- Cold start to API-up took well under the 15×2 s retry budget.
- Graceful `taskkill` (no `/F`) closed the main process cleanly; one child PID reported
  "can only be terminated forcefully" but exited with its parent — **verify with `tasklist`
  instead of escalating to `/F`.**

## DB-write protocol (lessons from the live run, 2026-07-03)

Three failure modes surfaced during the first real corpus-ingestion run (8 statutes, 8 PDFs,
16 highlights, 17 notes). All three are now part of the protocol:

1. **Shutdown race — the expensive one.** After a graceful `taskkill /IM zotero.exe`, a residual
   Zotero process can flush its own state to `zotero.sqlite` *after* your write, silently
   discarding it (one UPDATE pass was lost this way). Protocol: poll until **all** `zotero.exe`
   processes are gone **and** no `zotero.sqlite-journal` and no non-empty `-wal` remains (Zotero 10
   leaves a 0-byte `-wal` at every graceful close; it holds no frames and is ignored), wait a settle
   beat, write, **re-read from disk to verify the commit landed**, and only then restart Zotero.
2. **Notes: write plain text, not HTML.** Note HTML written directly to `itemNotes` is
   escaped/sanitized at the next Zotero startup (wrapped in `zotero-note znv1`, tags shown as
   literals). Store plain text with blank-line paragraph breaks — Zotero wraps it into proper
   `<p>` paragraphs itself and the result is stable across restarts.
3. **The local API does not list annotations.** `GET /api/users/<uid>/items/<attKey>/children`
   omits annotation items, so a zero result does NOT mean the highlights failed. Verify in the DB
   (`itemAnnotations` joined to `items.dateAdded`) or visually in the Zotero reader.

### One close/reopen per task — batch every write (the expensive-cycle lesson, 2026-07-04)

A single graceful close + restart costs **~2–4 min** (backup, poll-until-quiescent, the API-ping
budget). A run that inserted 10 references + a statute took **three** such cycles (~10 min of pure
protocol) because it closed → wrote → reopened → *then* read a collection key → had to close again.
The fix is ordering + batching, now encapsulated in `zot.write_session` (see `zotero-schema.md`):

- **Resolve everything you can while Zotero is OPEN, before the first close.** Collection ids
  (`zot.find_collection`, read-only), csljson lookups, which items already exist — all read live.
- **Then ONE `write_session`** covering every insert/edit/attach/filing for the task. It does
  wait-until-quiescent → backup → write → verify-on-disk internally. Never close→write→reopen→
  read→close→write; that is two cycles doing one job.
- **Pure creation doesn't need a close at all:** `POST /connector/saveItems` (Zotero open, HTTP
  201) — **but only when the target collection does not matter**: the connector files items into
  the collection selected in the UI, which another session can change under you. New items into a
  specific collection go through `zot.adauga_itemi(items, colectie=...)`, or `add_item(collection_id=...)`
  in the closed `write_session`, which is also the path when the batch edits existing items,
  attaches PDFs, or moves things — operations the API refuses.
- **Downloading source text** (statute PDFs, etc.) is orthogonal to the write cycle — do it up
  front, before closing Zotero, so the closed window stays as short as possible. For RO
  legislation see `references/ro-legislation-fetch.md` (legislatie.just.ro and cdep.ro work;
  the paywalled portals don't).

## Accept monitor — one optimization proposal per iteration

Allowlists decay: real work invents command shapes the rules don't match (see v2 lessons). So the
skill ships a **permission-prompt monitor** and a per-iteration review ritual.

**Wiring (one-time, per machine)** — `scripts/log_perm.py` logs every permission prompt (= every
accept the user is asked for) to `~/.claude/perm-requests.jsonl`. Hook it in
`~/.claude/settings.json`:

```json
"hooks": {
  "PermissionRequest": [{"hooks": [{"type": "command", "async": true, "timeout": 10,
    "command": "python \"C:\\Users\\<user>\\.claude\\skills\\drafting\\scripts\\log_perm.py\" PermissionRequest"}]}],
  "PermissionDenied": [{"hooks": [{"type": "command", "async": true, "timeout": 10,
    "command": "python \"C:\\Users\\<user>\\.claude\\skills\\drafting\\scripts\\log_perm.py\" PermissionDenied"}]}]
}
```

The hook only observes (no stdout → cannot alter the permission decision; `async` → never delays
the prompt). `PermissionDenied` entries mark commands the user REFUSED — never propose allowlisting
those.

**The log is machine-wide by design.** The hook fires once per Claude Code *process*, so
`~/.claude/perm-requests.jsonl` interleaves every concurrent session — and that's the point when
the same person runs the same skills across many projects: one shared log means one place to spot
a recurring prompt and fix it everywhere at once, instead of re-discovering the same missing rule
project by project. **Default to aggregating across all sessions**, not filtering to one.

Each entry still carries a `session` field (last 8 chars of the hook's `session_id`) — not for
excluding other sessions, but as optional provenance when it's useful to know *where* a pattern
came from (e.g. "this Bash shape shows up in 3 different projects → allowlist it globally" vs.
"only ever this one project → maybe that project's own `.claude/settings.json` is the right
place"). Only filter to a single `session` value when someone explicitly asks about one specific
project's prompts in isolation — that's the exception, not the default read of the log.

**Ritual (end of every iteration, or when the user asks "de ce am dat atâtea accept-uri?"):**

1. Read `~/.claude/perm-requests.jsonl` in full; keep entries newer than the last review, **across
   all sessions** (don't filter by `session` unless the user specifically wants one project isolated).
2. Cluster them by command class (same executable + same shape), separating denied ones out.
3. For each accepted cluster propose EXACTLY ONE fix — an allowlist rule, a command-shape
   standardization (so an existing prefix rule matches), or an `additionalDirectories` entry.
   Never propose rules crossing the safety boundaries (`rm`, `taskkill /F`, bare `sed`/`cat`,
   `git commit/push`).
4. The user approves per proposal → apply to `~/.claude/settings.json`.
5. Rotate: append the reviewed entries to `~/.claude/perm-requests.reviewed.jsonl`, truncate the
   live log. Target: next iteration runs with zero accepts outside the safety boundaries.

## Acceptance test (re-run after any change to the rules or on a new machine)

A full cycle that must complete with **zero permission prompts**:

1. `tasklist` → note whether Zotero is running.
2. If closed: start it → ping with retries → expect `HTTP 200`.
3. Graceful stop → `tasklist` must show no `zotero.exe` (do NOT force-kill if a child PID
   complains; re-check first).
4. Start again → ping `HTTP 200` → API read (`/api/users/<userID>/items?limit=1`) `HTTP 200`.

Result 2026-07-03: PASS — all four steps, no prompts, no force kill needed.

## După scriere: rescanarea Better BibTeX, și cum arată o tranzacție lungă

Protocolul de scriere are un pas în plus, la capăt: **după restart, declanșează rescanarea
Better BibTeX și așteapt-o.** Devine obligatoriu după mai mult de două cicluri de scriere
într-o sesiune — până atunci e igienă, de acolo încolo e ce ține citekey-urile în acord cu
baza.

Ce se întâmplă dacă n-o faci, măsurat pe zece cicluri într-o zi, cu peste 2000 de itemi
marcați `synced=0`: Zotero intră într-o tranzacție lungă de recuperare, iar simptomele ei
sunt derutante tocmai fiindcă seamănă cu altceva.

**Semnătura, toate trei deodată:**

| Ce vezi | Ce pare | Ce e |
|---|---|---|
| `/connector/ping` → **200** | Zotero e viu și sănătos | procesul răspunde, baza nu |
| `/api/users/<id>/items` **atârnă** | API stricat | citirea așteaptă lacătul |
| `zotero.sqlite-journal` staționar pe disc | reziduu de la o rulare veche | tranzacția e **în curs** |

**Ce NU se face:** repornit — nu ajută, tranzacția reia; și mai ales **niciodată omorât
forțat**. Un `taskkill /F` peste o tranzacție în curs e felul cel mai sigur de a strica baza.

**Ce se face:** oprire grațioasă și așteptare până dispare jurnalul.

### Cum se declanșează rescanarea, și cum se verifică

Sondat pe instalarea asta, 23 august 2026, ca să nu rămână o instrucțiune fără comandă:

**Declanșarea din afară nu se poate.** Endpoint-ul JSON-RPC al Better BibTeX
(`http://127.0.0.1:23119/better-bibtex/json-rpc`) nu expune nicio metodă pentru rescanare, și
nici `system.listMethods` prin care s-ar putea căuta una — răspunde `Method not found`. BBT
reatribuie singur când itemul se schimbă și la repornirea lui Zotero. Forțat, se face din
interfața Zotero, pe colecție sau pe bibliotecă.

**Cheia fixată bate cheia dinamică, iar BBT n-o știe.** Când un item are `Citation Key: X` în
câmpul `Extra`, documentele citează `X` — ăsta e rostul fixării. `item.citationkey` întoarce însă
cheia **dinamică**, și în cazul ăsta: măsurat, „ComisiaNationala2009" fixat, iar BBT răspunde
„ComisiaNaționalăpentruEvaluareaProgramelorPublice2009" (exemplu refăcut; forma e cea măsurată:
cheia dinamică lungă, cu diacritice). Din opt iteme cu
cheie fixată, patru difereau; într-un caz real, 27 din 57 de surse rămâneau nelegate — **tăcut**,
fiindcă lipsa unei chei nu se deosebește de „itemul nu există".

Deci `zot.citekeys` întreabă **întâi** cheile fixate, citite din bază cu `zot.chei_fixate`, și abia
apoi BBT, pentru itemele fără fixare. Exportul CSL-JSON al lui Zotero respectă fixarea — verificat
pe altă bibliotecă, 26 din 26 —, deci stratul de aici se aliniază la ce face deja exportul.

Și, fiindcă eșecul era tăcut: când rămân chei nelegate, funcția **o spune**, cu numărul. O listă
mai scurtă nu trebuie să treacă drept răspuns complet.

**Verificarea că BBT a atribuit.** `item.citationkey` întoarce, pentru o listă de chei de item,
citekey-ul fiecăruia sau `null` cât timp BBT încă n-a atribuit:

```python
import zot
zot.citekeys(["ZEFUPYRI", "NI9V46QF"])
# {'ZEFUPYRI': 'Krippendor2004a', 'NI9V46QF': 'Ofițeru2026'}

zot.asteapta_citekeys(cheile_scrise)     # True cand BBT are cheie pentru toate
```

Deci protocolul complet, la finalul unui ciclu de scriere: **scrie → repornește Zotero →
așteaptă cu `asteapta_citekeys` → abia apoi randează citările.** Fără pasul de așteptare,
randarea pornește înaintea rescanării și raportează chei „negăsite" care de fapt încă nu
există — un eșec care arată exact ca o cheie greșită.

**La un item MODIFICAT, verificarea de mai sus minte.** Cheia lui exista deja, deci
`asteapta_citekeys` intoarce `True` pe loc — inclusiv cand BBT n-a aflat de modificare si cheia
a ramas cea veche. Masurat pe un item caruia i s-a scris anul direct in baza: repornirea lui
Zotero n-a regenerat cheia, fiindca scrierea directa ocoleste Zotero si nu produce evenimentul
de modificare pe care BBT il asculta. Verificarea trece, randarea foloseste cheia veche, iar
greseala se vede abia in bibliografia livrata.

Cand ai modificat un camp din care se compune cheia — autor, an, titlu — spune care era cheia
dinainte, ca asteptarea sa fie dupa o **diferenta**, nu dupa o prezenta:

```python
vechi = zot.citekeys([cheia])                     # inainte de scriere
...                                               # ciclul de scriere
zot.asteapta_citekeys([cheia], vechi=vechi)       # asteapta pana cheia CHIAR se schimba
```

Daca expira, nu insista: refacerea se cere din interfata, clic dreapta pe item → Better
BibTeX → *Refresh BibTeX key*. Din afara nu exista comanda, din acelasi motiv ca mai sus.

**Endpoint-ul expiră când Zotero e ocupat.** Măsurat: același apel a mers, apoi a expirat, apoi
a mers iar. Nu e un semn că metoda lipsește; e chiar semnalul din tabelul de mai sus.
`asteapta_citekeys` prinde singură expirările și reîncearcă. În cod,
`zot.wait_until_readable()` — perechea lui `wait_until_quiescent()`: acela așteaptă ca baza să
devină **scriibilă**, ăsta așteaptă să devină **citibilă**.

## Filtrul înghite codul de ieșire

O rulare lungă se citește prin `| tail` sau `| grep` — altfel progresul acoperă tot. Dar `$?`
raportează starea **ultimei comenzi din conductă**, adică a filtrului, care reușește întotdeauna.
Un script care s-a oprit corect, cu cod 1, se citește ca reușit.

Măsurat, pe același script care iese cu `SystemExit`:

| Cum îl chemi | Ce spune `$?` |
|---|---|
| `python x.py > /dev/null` | 1 — corect |
| `python x.py 2>&1 \| tail -1` | **0** — al filtrului |
| `python x.py 2>&1 \| tail -1` apoi `${PIPESTATUS[0]}` | 1 — corect |
| aceeași conductă, într-o buclă, `$?` după buclă | **0** |

Deci, la orice rulare filtrată a cărei reușită contează: **`${PIPESTATUS[0]}`, nu `$?`.** Iar
într-o buclă, codul se citește **în interiorul** ei, nu după — după buclă `$?` e starea ultimei
iterații, și numai a ei.

Formularea care explică de ce se ratează, a sesiunii care a plătit-o: *conducta a fost tratată ca
o fereastră prin care te uiți, nu ca o comandă care are ea însăși un rezultat.* Un `| tail` pus ca
să scurteze ieșirea nu pare o decizie — și de aceea capcana e deschisă la fiecare rulare lungă,
nu la cele speciale.

E capcana pereche a celei de la mesajul de eroare tipărit înaintea progresului: acolo cauza se
pierde în ieșire, aici se pierde chiar semnalul că a fost o cauză. Amândouă fac o oprire corectă
să arate ca o reușită — iar diferența se vede doar dacă cineva verifică pe urmă rezultatul.


## Codul cu backslash

Nu prin unealta de shell, în nicio formă — o pereche de backslash se înjumătățește pe drum, iar
ce rămâne devine un caracter de control care nu se vede. **Regula 24 din `reguli-de-lucru.md`**,
cu măsurătoarea și cu verificarea care o prinde. Aici nu se repetă: e regulă de lucru, nu de
skill, și are un singur loc.

## Ieșirea non-ASCII, pe Windows

Consola Windows scrie implicit în codul de pagină moștenit (cp1252). Orice `print` care
conține un diacritic sau o cratimă tipografică oprește rularea cu
`UnicodeEncodeError: 'charmap' codec can't encode character` — **după** ce fișierul de ieșire
a fost deja scris. Mesajul arată ca o eroare de date, nu ca una de terminal, iar rularea pare
că a eșuat deși produsul ei există.

Remediul stă în script, nu în comanda care îl cheamă, ca să nu depindă de cine îl invocă:

```python
import sys
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
```

Două linii, la începutul oricărui script care tipărește metadate. Toate scripturile
skill-urilor le au acum. Varianta din comandă — `PYTHONIOENCODING=utf-8` ca prefix — merge,
dar se uită exact atunci când cheamă altcineva scriptul.

## Portability checklist (propagating to another machine)

- [ ] Find `zotero.exe` path (`C:\Program Files\Zotero\` typical on Windows) and fix it in the rules.
- [ ] Replace `<user>` in the script-path rules.
- [ ] Detect the library user id (`python scripts/zot.py userid`) for API URLs.
- [ ] Run the acceptance test above.
- macOS/Linux equivalents: `open -a Zotero` / `zotero &`, `osascript -e 'quit app "Zotero"'` /
  `pkill -TERM -x zotero` (graceful), same curl rules; adjust allowlist tool names accordingly.

## Restaurarea după o mutare sau o mașină pierdută

Sincronizarea Zotero aliniază totul la ce știe serverul: datele și, dacă e pornită, și
fișierele. Asta e sigur când serverul are tot, și distructiv când singura copie a ceva e
locală. Pe 8 octombrie 2026, două pierderi au venit din aceeași presupunere:

- **un storage copiat de pe o mașină moartă**, pus printr-un junction sub un Zotero viu cu
  sincronizarea de fișiere pornită: un fișier pe care doar mașina moartă îl avea a fost
  înlocuit cu versiunea serverului de sub aceeași cheie, iar coșul era gol;
- **colecții restaurate dintr-o copie veche**, marcate `synced=1` dar absente pe server:
  Zotero nu le mai urcă, iar la o sincronizare completă absența se propagă ca ștergere.

Ordinea, la orice restaurare:

1. **Sincronizarea de fișiere se oprește întâi** (Setări → Sincronizare → „Sync attachment
   files in My Library"). E setarea omului; agentul i-o cere, nu o schimbă.
2. **Copia străină intră sub copie de siguranță** înainte de a o pune sub Zotero. Abia apoi
   junction-ul sau mutarea.
3. **Inventarul se face contra serverului**, pe colecții, nu doar pe itemi: ce există local și
   lipsește pe web. O colecție găsită așa se urcă din nou cu `zot.reurca_colectie`, nu se
   recreează cu `add_collection`, care refolosește colecția cu același nume.
4. **Fișierele fără item** se inventariază după cheie: un folder din `storage` a cărui cheie nu
   e în baza de date e un item pierdut, nu gunoi.
5. Sincronizarea de fișiere se repornește numai după ce omul a văzut inventarul.
