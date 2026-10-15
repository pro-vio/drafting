# Zotero internals — reading & writing zotero.sqlite

Read this when you need to **edit existing items**, **write annotations**, or **add statutes**
directly in the database. For just rendering citations you don't need any of this — the API is enough.

## Access model (why direct sqlite)

- **Read, live (Zotero running):** the local web API on `http://127.0.0.1:23119`.
  - `GET /api/users/<uid>/items?format=csljson` → `id` == the Better BibTeX citekey. Use for citations.
  - `GET /api/users/<uid>/collections/<key>/items?format=json` → raw fields (nameOfAct, history, …).
  - `HTTP 000` from port 23119 = Zotero is **closed**.
- **Iteme noi intr-o colectie anume:** `zot.adauga_itemi(items, colectie="...")`. Face singura
  tot ciclul — rezolva colectia cat Zotero e deschis, il inchide gratios, scrie cu
  `collection_id` explicit, il lasa deschis la final. **Asta e calea recomandata**, si motivul
  e in sectiunea de mai jos despre conector.
- **Create new items, live, fara colectie-tinta:** `POST /connector/saveItems` (Zotero open,
  returns HTTP 201). **Antetul `X-Zotero-Connector-API-Version: 3` e obligatoriu** — fara el
  cererea cade cu `HTTP 400 CONNECTOR_VERSION_OUTDATED`, iar mesajul nu spune ce versiune
  asteapta. Nu construi cererea de mana: `zot.save_items(items)` o impacheteaza corect.
  Ateriza in **colectia selectata in interfata**, deci nu poti tinti o colectie cu ea.
- **Edit / move / delete existing items, or write annotations:** the API is READ-ONLY for this
  (`POST` → "Endpoint does not support method"). Do it by writing `zotero.sqlite` **directly**,
  with **Zotero CLOSED** and a **backup first**. `zot.open_rw()` refuses if Zotero is running.

Local-only library (no sync) ⇒ direct writes are safe. Always bump `items.version` and set
`synced=0` (helper `zot.touch`) so a later sync reconciles cleanly.

## Doua sesiuni care scriu in acelasi timp

Doi agenti lucreaza in paralel. Fiecare cere subcolectia lui. Itemii ies amestecati: o parte
din ai primului ajung unde scria al doilea. Nimeni n-a gresit nimic, si nimic nu semnaleaza.

Amestecul are **doua cauze diferite**, si fiecare cere alt remediu.

### Cauza intai: conectorul nu tinteste, ci nimereste

`POST /connector/saveItems` aseaza itemii **in colectia selectata in interfata**. Selectia e o
stare a ferestrei, una singura pentru tot Zotero. Agentul A o pune pe colectia lui si incepe sa
scrie; agentul B o schimba pe a lui; restul itemilor lui A aterizeaza la B. Ambii au cerut
corect, iar raspunsul conectorului e `201` in ambele cazuri.

**Remediul e sa nu mai depinzi de selectie.** Cand colectia conteaza, calea determinista e:

```python
cheie, cid = zot.find_collection("proiect-exemplu")  # cat Zotero e inca deschis
with zot.write_session("iteme noi") as cur:          # isi ia randul singur
    zot.add_item(cur, "journalArticle", campuri, collection_id=cid)
```

Costa un ciclu de inchidere, dar colectia e scrisa in `collectionItems`, nu dedusa din ce
fereastra era deschisa. Conectorul ramane bun exact acolo unde colectia nu conteaza: iteme
puse in biblioteca, mutate pe urma.

**`add_item` nu mai scrie de doua ori acelasi lucru.** Inainte de INSERT cauta un item cu acelasi
DOI (normalizat), acelasi ISBN sau acelasi titlu exact la acelasi tip, prin cursorul ciclului.
Implicit intoarce itemul gasit, il pune in colectia ceruta si tipareste „EXISTA deja"; cu
`daca_exista="refuza"` ridica `LookupError`; cu `daca_exista="creeaza"` scrie oricum — pentru cazul
in care dublura e voita, cum sunt formele succesive ale unui act, care au acelasi titlu si se
deosebesc prin `dateEnacted`. Masurat pe 23 septembrie 2026: doua cicluri la optsprezece minute
distanta au adaugat aceleasi patru articole de doua ori, iar dublurile au iesit la iveala abia doua
zile mai tarziu.

**Cand dublura exista deja: care item ramane** (regula autorului, 15 octombrie 2026). In ordine:
originalul (articolul publicat in revista, nu o copie a lui, si analog la celelalte tipuri); apoi cel
care nu cere OCR; apoi cel citat; apoi oricare. Scopul regulii il spune autorul: „sa nu stergi notele
si versiunile originale, cu care se lucreaza usor". Partea mecanica o face operatia `fuzioneaza` din
`scrieri_amanate.py`: notele dublurii trec la itemul pastrat (nota de cache ramane, fiindca descrie
fisierul dublurii), atasamentele cu adnotari sau note trec si ele, itemul pastrat intra in toate
colectiile dublurii si primeste `dc:replaces`, iar dublura merge la cos. Ce ramane alege cine pune
sarcina; operatia nu alege. Inainte de fuziune: textele se citesc si se compara (`adu_lege.py` spune
„dublura" numai la text identic), iar cheile ambelor iteme se cauta in proiecte.

### Cauza a doua: ciclul de inchidere e o resursa unica

`write_session` **cere** Zotero inchis la intrare — inchiderea o face omul sau agentul — si,
dupa o scriere reusita, **il porneste singur la iesire** (`redeschide=False` opreste asta),
fiindca starea implicita buna e cea de citire. Doua sesiuni suprapuse se calca in orice punct:
una porneste Zotero cat cealalta inca scrie, si scrierea a doua vede baza blocata sau, mai rau,
o scrie sub un Zotero pornit intre timp.

Un al doilea ciclu **din acelasi proces** inchide Zotero-ul pornit de primul si asteapta sa
dispara procesul, nu doar portul. Unul pornit de altcineva — omul, alta sesiune — nu il inchide:
acolo refuza, corect. Regula practica ramane aceeasi: toate scrierile unei sarcini intra intr-un
singur ciclu, inclusiv memoizarea textului (`ensure_cached` primeste cursorul, ca orice alta
scriere), fiindca fiecare ciclu inchide si porneste aplicatia, iar asta costa. Reteta completa e in docstringul lui `write_session` si in
`references/pdf-text-cache.md`.

De aceea `write_session` **isi ia randul inainte de a atinge ceva** — `zot.coada`, un lacat de
fisier la `~/Zotero/.zotero-scriere.lock`. Cine nu-l are, asteapta si spune pe cine asteapta:

```
astept randul: scrie 'import-hcl' (PID 26480)
```

Nu trebuie chemat de mana; `write_session` il ia singur. Se cheama direct doar cand vrei sa
tii randul peste mai multe operatii:

```python
with zot.coada("reconciliere atasamente"):
    ...
```

Trei lucruri de stiut despre el:

- **Randul se ia inaintea inchiderii lui Zotero**, nu dupa. Altfel doua sesiuni ar inchide
  amandoua Zotero si abia apoi ar descoperi ca una trebuie sa astepte — cu Zotero inchis
  degeaba in fata utilizatorului.
- **Se elibereaza si cand scrierea crapa.** Eliberarea sta pe `finally`, fiindca un lacat
  ramas de la o exceptie ar tine urmatoarea sesiune pe loc cat traieste procesul care a crapat,
  pana la `TimeoutError`.
- **Un lacat vechi se sparge dupa starea procesului care l-a pus.** Proces sigur mort: dupa 30
  de secunde, cat sa nu se sparga lacatul unuia care tocmai porneste. Proces care nu se poate
  verifica (lista de procese nu raspunde): dupa 15 minute. Proces viu: niciodata, fiindca o
  scriere lunga arata identic cu una moarta, deci doar vechimea nu e dovada.

### Calea ieftina, si de ce nu ajunge un avertisment

Conectorul aseaza itemii in colectia selectata in interfata. Selectia e una singura pentru tot
Zotero, deci cu doua sesiuni in aceeasi biblioteca ea nu e a nimanui: trei articole au aterizat in
subcolectia altui proiect, iar in aceeasi zi, pe aceeasi cale, alti itemi nimerisera bine — selectia
se potrivise din intamplare, deci defectul n-a lasat urma.

Avertismentul exista de dinainte, in documentatie si ca parametru care refuza cand i se cere o
colectie. **Plasa prinde insa doar pe cine stie sa dea parametrul.** Cine cheama simplu
`save_items(items)` — apelul natural, cel pe care il scrii cand nu banuiesti nimic — nu intalneste
nici avertismentul, nici plasa. Un avertisment care trebuie citit ca sa te fereasca e o regula
tinuta de om, iar regulile tinute de om cad.

Deci calea corecta a fost facuta sa coste un rand, ca si cea gresita:

```python
chei = zot.adauga_itemi(items, colectie="teoria-jocurilor")
```

Ea primeste itemii **si in forma conectorului** (`itemType`, `title`, `creators`), asa incat mutarea
unui apel nu cere si rescrierea datelor. Colectia se da ca nume, cale sau cheie; daca nu exista,
**ridica eroare** — n-o creeaza tacut, fiindca o colectie nascuta dintr-o greseala de scriere pare
munca facuta si se gaseste mai greu decat una care lipseste.

Si nu inchide Zotero sub cineva care a anuntat ca lucreaza in el: fara buget de asteptare spune cine
il tine si se opreste; cu `asteapta=<secunde>`, asteapta atat. Cand nu merita asteptat, treaba se
lasa in coada de scrieri amanate.


### În ce stare se lasă aplicația: deschisă

Protocolul spunea cum se închide Zotero și cum se așteaptă, dar nu **în ce stare se lasă după**.
Regula, plătită de două ori în aceeași zi:

> **Cine scrie în bază lasă aplicația deschisă după el.**

Motivul e asimetria: scrierea cere aplicația închisă și e **rară**; citirea — randarea citărilor,
prin API-ul local — o cere deschisă și e **deasă**. Starea implicită bună e cea de citire, iar cine
are nevoie să scrie o închide oricum singur, fiindcă protocolul îl obligă.

Ce s-a întâmplat fără regula asta: o sesiune a scris și a lăsat Zotero închis — corect după literă.
Alta a regenerat un raport de peste două mii de rânduri și a primit „Zotero indisponibil"; raportul
s-a construit până la capăt, **cu cod de ieșire zero**, și a trebuit aruncat.

`write_session` o face acum singură — după un commit reușit pornește Zotero, dacă nu rulează.
`redeschide=False` o oprește, pentru cazul în care urmează imediat altă scriere.

### Un răspuns bun nu înseamnă că baza e gata

La repornire, o singură confirmare de la API nu spune nimic: măsurat, baza a căzut înapoi în
tranzacția lungă **după** confirmare, iar o generare de zece minute a ieșit fără aparat, tot cu cod
zero.

```python
zot.asteapta_api(consecutive=3)      # trei răspunsuri bune LA RÂND, la câteva secunde
zot.porneste_zotero()                # pornește dacă nu rulează, apoi așteaptă stabil
```

O cădere șterge seria — contează răspunsurile **la rând**, fiindcă unul singur poate prinde o
fereastră între două faze ale recuperării.

Și se interoghează **date**, nu `ping`. Simptomul care induce în eroare, măsurat după aproape două
mii de iteme cu `synced=0`: Zotero răspunde la ping, dar orice interogare de date întoarce
`HTTP 500`, iar `zotero.sqlite-journal` stă pe disc. Nu e o problemă a scriptului care întreabă. Se
limpezește după o închidere grațioasă și așteptarea până dispare jurnalul — **niciodată**
`taskkill /F`: peste o tranzacție în curs, forțarea strică baza.


### Aplicația ținută deschisă e altceva decât o cursă între scriitori

Coada de mai sus rezolvă o problemă: două sesiuni care scriu în același timp. Nu rezolvă cealaltă
problemă, care e mai frecventă — **Zotero ținut deschis de un om sau de o sesiune care lucrează în
interfață**. Acolo nu se ciocnesc doi scriitori; o resursă e ocupată pe termen nedefinit, iar cine
așteaptă are un singur semnal: „e pornită". Când pe mașină lucrează șapte sesiuni, ăla nu e o
informație.

Două unelte, amândouă ieftine:

**Anunțul.** Cine deschide Zotero cu intenția de a lucra în el spune asta:

```python
with zot.lucrez_in_zotero("adaug fișe pentru capitolul 3"):
    ...
```

Scrie `~/Zotero/.zotero-in-lucru.json` cu cine, de când și pentru ce, și îl șterge la ieșire.
`zot.cine_lucreaza()` îl citește. Nu blochează nimic — transformă doar „e pornită" în „e a sesiunii
X, de acum zece minute". Nu e o garanție: un om care deschide aplicația din interfață nu anunță
nimic, iar funcția întoarce None. Acoperă cazul obișnuit, care e între sesiuni.

**Așteptarea cu buget.** Sesiunea care are de scris și găsește aplicația deschisă avea până acum
două ieșiri — să întrebe omul, sau să abandoneze sarcina:

```python
zot.asteapta_inchiderea(timeout=600)      # înaintea lui write_session
with zot.write_session("șase fișe", asteapta=600) as cur:   # sau direct, cu buget
    ...
```

Spune pe cine așteaptă, dacă se știe, și ridică `TimeoutError` cu ce a aflat. `asteapta=0` —
implicit — păstrează purtarea de până acum: refuz imediat.

**Coada de scrieri amânate** — `scripts/scrieri_amanate.py`. Sesiunea blocată își lasă sarcina
într-un dosar, iar prima care ajunge la baza liberă le rulează pe toate **într-un singur ciclu**:

```python
import scrieri_amanate as sa
sa.lasa("cinci rapoarte", [
    {"op": "add_item", "tip": "report", "campuri": {...}, "nume": "raport"},
    {"op": "attach_pdf", "parinte": "raport", "cale": "C:/…/x.pdf", "nume": "pdf"},
    {"op": "ensure_cached", "atasament": "pdf", "cale": "C:/…/x.pdf"},
])
```

**`nume` leagă pașii, deci o sursă nouă se amână întreagă.** Operațiile care creează ceva — itemul
și atașamentul — își botează rezultatul, iar pasul următor îl cere pe nume, fiindcă cheia încă nu
există când se scrie sarcina. Fără numele atașamentului, memoizarea nu se putea amâna: cerea tocmai
cheia pe care nimeni n-o scria, deci pleca itemul cu fișierul lui și textul rămânea neextras.

Două operații de editare, pentru corecțiile care nu pot aștepta un ciclu propriu:
`{"op": "set_field", "item": "<cheie sau nume>", "camp": "shortTitle", "valoare": "…"}` (câmpul se
verifică pe tipul itemului) și `{"op": "relateaza", "a": "…", "b": "…"}`. `add_item` primește și
`daca_exista`, ca în `zot.add_item`. `adu_lege.py` le folosește singur când Zotero e deschis.

```bash
python scrieri_amanate.py --arata      # ce așteaptă, de la cine, de când
python scrieri_amanate.py --ruleaza    # cu Zotero închis: le scrie pe toate
```

Câștigul nu e că nu se pierde munca — e că se plătește **un** ciclu de închidere pentru mai multe
sesiuni, în loc de câte unul de fiecare.

**Sarcina e date, nu cod.** Cine o lasă descrie ce vrea scris, într-un vocabular fix — `add_item`,
`attach_pdf`, `add_to_collection`, `ensure_cached` —, iar cine o rulează execută **codul lui**, nu
al altcuiva. Fără distincția asta, prima sesiune liberă ar rula scripturi scrise de altcineva, iar
cine răspunde de rezultat n-ar mai fi nimeni. Ce nu încape în vocabular rămâne la proprietar;
vocabularul se lărgește pe un caz real, nu preventiv.

**Fiecare sarcină în punctul ei de salvare.** Dacă una cade, se derulează înapoi numai ea, iar
celelalte se scriu. Fără asta, o sarcină greșită a unei sesiuni ar anula munca tuturor, iar cine a
lăsat-o n-ar afla de ce. Fiecare primește un fișier `.rezultat.json` lângă ea — ce s-a scris, ce
chei au ieșit, ce a căzut; sarcinile scrise dispar, cele căzute rămân pentru proprietarul lor.

Verificat pe o copie a bibliotecii, cu trei sarcini de la trei „sesiuni", una dintre ele cerând o
colecție inexistentă: aia a căzut singură, celelalte două s-au scris, iar raportul fiecăreia e pe
disc.


## Conversia unui item in alt tip

Cazul obisnuit: un preprint capata versiune publicata, deci itemul trebuie sa devina articol de
revista. `UPDATE items SET itemTypeID` singur nu ajunge, si esecul e tacut in doua feluri.

**Campurile care nu exista in tipul nou raman in baza.** Zotero nu le mai afiseaza, dar ele sunt
acolo: reapar daca itemul e mutat inapoi sau exportat, iar un audit le gaseste ca inconsistenta.
La preprint → articol de revista sunt patru posibile — `repository`, `genre`, `archiveID`,
`seriesNumber` —, dar numarul nu e ce trebuie retinut.

**Ce se sterge se afla din schema, niciodata dintr-o lista scrisa dinainte.** `campuri_valide`
interogheaza `itemTypeFields` pentru tipul nou si sterge tot ce nu e acolo, oricare ar fi.
Diferenta se vede exact cand nu te astepti: un item creat de mana n-are `genre` deloc, unul adus
de conector de pe o platforma de preprinturi il are, fara ca cineva sa-l fi scris intentionat.
O conversie care porneste de la campurile stiute le rateaza pe acelea — si pe al cincilea, pe
care nu l-a vazut inca nimeni.

**Tipurile de creator difera si ele.** Un `reviewedAuthor` ramas pe un tip care nu-l accepta
produce un rand orfan in `itemCreators`: autorul dispare din interfata fara sa fi fost sters.

Deci:

```python
raport = zot.schimba_tip(cur, item_id, "journalArticle", doar_arata=True)
print(raport["pierdute"])          # {camp: valoare} — se citeste INAINTE
zot.schimba_tip(cur, item_id, "journalArticle")
```

`doar_arata` intai, mereu: ce se pierde se citeste inainte de a se pierde, si uneori merita
salvat in `Extra` (numarul din depozit, de exemplu).

### Conversia rupe cheia de citare

Anul se schimba odata cu tipul — preprintul e din alt an decat articolul —, iar Better BibTeX
recompune cheia din an. Masurat pe patru conversii: 2022→2023, 2019→2020, 2023→2024, 2024→2025,
deci **toate patru** au capatat cheie noua.

Ce inseamna asta pentru un manuscris care citeaza deja lucrarea: marcajul `[@Autor2022]` ramane
la anul vechi, iar randarea il raporteaza **negasit**. Eroarea arata ca o cheie gresita, nu ca o
conversie reusita, si se descopera abia la randare.

Ordinea care o evita:

1. **inainte de conversie**, cauta marcajul lucrarii in manuscrise (`grep` pe cheia veche);
2. converteste;
3. `zot.asteapta_citekeys([cheia], vechi={cheia: citekey_dinainte})` — asteapta cheia **noua**,
   nu doar existenta uneia; la un item modificat, „are cheie?" raspunde da fara sa se fi
   intamplat nimic;
4. inlocuieste marcajele gasite la pasul 1.

### Cele doua fisiere, cand paginatia difera

Preprintul si articolul publicat au paginatii diferite, iar locatorii se dau pe versiunea
citata. Daca tii ambele PDF-uri pe acelasi item, da-le titluri proprii —
`attach_pdf(cur, parent, cale, title="Versiunea publicata (JASA 2023)")` — si scrie in `Extra`
care poarta paginatia citabila. Cine deschide itemul peste sase luni vede din lista, nu din
deschiderea fiecarui fisier.

Cand versiunea publicata nu se poate procura, `Extra` e singura aparare: scrie acolo, explicit,
ca fisierul atasat e preprintul si ca locatorii se dau pe versiunea publicata.


## Ce se citește cu Zotero deschis, și ce nu

Protocolul spune că **scrierile** cer Zotero închis. Ce nu spunea, și a costat o rundă: care
**citiri** merg cu el pornit.

- **Merg oricând:** `find`, `citekeys`, `detect_user_id`, `collection_ids`, și orice interogare
  pe o conexiune deschisă cu `_ro()` — e `mode=ro&immutable=1`, deci nu ia niciun lacăt.
  `item_id_for_key` și `key_for_item_id` intră aici: primesc conexiunea ca **prim argument**,
  iar aia e conexiunea de la `_ro()`.
- **Cer Zotero închis:** tot ce trece prin `open_rw` sau prin `write_session`.

Dacă o citire prin `_ro()` întoarce totuși `database is locked`, nu e o cale greșită — e Zotero
în mijlocul unei scrieri proprii. Se așteaptă cu `wait_until_readable()` și se reia; e chiar
perechea lui `wait_until_quiescent()`.


## Backup discipline (non-negotiable)

Before any DB write: a copy of `zotero.sqlite` into `zot.COPII` (`D:\zotero-lucru\copii\zotero.sqlite.pre-<task>.bak`,
never next to the database on C:); `zot.backup(tag)` does it.
The scripts that write also do this, but make your own checkpoint for ad-hoc edits.

**A copy is needed only until the write is verified** — Zotero has started on the new database
and shows what it should. After that it is just an old state of the database, about 200 MB each,
and nothing removes it: by 7 October 2026 eight had piled up, 1.4 GB. `write_session` prints the
count and size at the end of each cycle once there is more than one. `python zot.py copii` lists
them; `python zot.py copii --muta` moves all but the newest to the Windows Recycle Bin (`--toate`
moves the newest too). Nothing is deleted for good: emptying the bin is the author's. Zotero's own
automatic copies (`zotero.sqlite.bak`, `zotero.sqlite.<n>.bak`) are never touched.

**The tag becomes a file name**, so it follows the invented-examples rule: name the task
(`muta-adnotari`, `acte-plangere`), never a person.

## Tables you touch

- `items(itemID, itemTypeID, key, version, synced, dateModified, clientDateModified, libraryID)`
- `itemData(itemID, fieldID, valueID)` — one row per field on an item.
- `itemDataValues(valueID, value)` — **value is UNIQUE**. Reuse via `get_value` (insert if absent).
  Never UPDATE a shared value row; point the item's `itemData.valueID` at a new/existing value.
- `itemCreators` / `creators(firstName,lastName)` / `creatorTypes`.
- `collections(collectionID, key)` / `collectionItems(collectionID, itemID, orderIndex)`.
- `itemAttachments(itemID, parentItemID, linkMode, contentType, path, storageHash, storageModTime)`.
- `itemAnnotations(itemID, parentItemID, type, authorName, text, comment, color, pageLabel, sortIndex, position, isExternal)`.
- `deletedItems(itemID, dateDeleted)` — inserting here = move to **trash** (reversible, preferred over hard delete).

## Trashed items still appear in every table — filter them out explicitly

A trashed item is **not** removed from `items`/`itemAttachments`/`itemData` — it stays exactly as
it was, plus a row in `deletedItems`. Any read query meant to reflect "active library content"
(finding a PDF to open, listing an item's attachments, checking for duplicates) must exclude it,
or a trashed scan/duplicate silently reappears as if it were live. Trashing is also **per-item,
independently** — a parent can be active while one of its attachments is trashed (e.g. a scan
superseded by a text-native duplicate with highlights, the parent record kept). Always check the
specific row you're reading, not just its parent.

```sql
-- append to any items/itemAttachments query that means "currently in the library"
AND itemID NOT IN (SELECT itemID FROM deletedItems)
```

`zot.py` exposes this as `zot.NOT_TRASHED` (an f-string-able clause) and `zot.find()` already
applies it to both the item and its attachment lookup.

## itemTypeID — resolve by name

Code resolves item types by `itemTypes.typeName` — `zot.item_type_id(cur, name)`, or a subquery
`SELECT itemTypeID FROM itemTypes WHERE typeName IN (...)` — never by number. For orientation only,
read from this install's `itemTypes` on 13 September 2026: `1` = annotation · `3` = attachment ·
`7` = book · `22` = journalArticle · `28` = note · `36` = statute.

## fieldID — resolve, don't hardcode

IDs are stable within an install but **look them up** (`zot.field_id(cur,'name')`). Observed on one
install (illustrative, verify): `title 1`, `shortTitle 14`, `language 15`, `extra 19`, `publisher 25`,
`place 26`, `history 39`, `publicationTitle 41`, `originalPublisher 49`, `originalPlace 50`,
`nameOfAct 116`, `codeNumber 117`, `dateEnacted 119`.

## Patterns

**Edit a field** (e.g. fix ALL-CAPS title, swap place/publisher, set language):
```python
con = zot.open_rw(); cur = con.cursor()
zot.set_field(cur, item_id, zot.field_id(cur,'title'), "Proper Title Case")
zot.touch(cur, item_id); con.commit(); con.close()
```

**CMOS reprints & ugly keys via the Extra field** (read by the csljson API after a BBT scan):
- `Original Date: 1965` → renders `Olson (1965) 2003` (original in `[ ]` in-text, `( )` in list).
- `Citation Key: ShortPin` → pins an otherwise-ugly auto key (long institutional author).
Set these on the item's `extra` field; no native originalDate/citationKey field needed.

**RO/foreign titles get Title-Cased by CSL** when `language` is missing → set `language: ro`
so citeproc leaves the title as written. ALL-CAPS in the source passes through → fix at the item.

**Add a statute / any new item — use the batched helpers, don't hand-roll SQL.** `zot.py` ships
`write_session` + `add_item`/`attach_pdf`/`add_to_collection`/`find_collection`, which do the whole
protocol (wait-until-quiescent → backup → write → verify-on-disk) and all the create SQL. Resolve
collection ids with `find_collection` **while Zotero is still open**, then one closed cycle:

```python
import zot
key, cid = zot.find_collection("2026_teze")          # read-only — do this BEFORE closing Zotero
with zot.write_session("legislatie-noua") as cur:     # close Zotero first; this waits + backs up
    sid = zot.add_item(cur, "statute",
        {"shortTitle": "Legea 57/1968", "nameOfAct": "Lege nr. 57/1968 …",
         "codeNumber": "57/1968", "dateEnacted": "1968-00-00 1968",   # date format YYYY-00-00 YYYY
         "history": "Buletinul Oficial nr. …"}, collection_id=cid)
    zot.attach_pdf(cur, sid, "legislatie/Legea_57_1968.pdf")          # stored copy + md5 hash
    zot.add_item(cur, "webpage", {"title": "Servicii", "url": "…", "language": "ro"},
        creators=[("author", "Asociația Exemplu pentru Inițiative Civice")], collection_id=cid)  # literal author
```

`add_item` fields = `{fieldName: value}`; creators are `("author", "Whole Institution")` (literal,
fieldMode 1) or `("author", last, first)` (personal). Under the hood it still does what the manual
recipe did — insert `items`, `itemData` rows, `collectionItems`, and for PDFs copy to
`storage/<8charKey>/<file>` with linkMode 0, storageHash = md5, storageModTime = mtime·1000 — but as
one tested call. **For pure *creation* with Zotero left OPEN, `POST /connector/saveItems` (HTTP 201)
needs no close at all — but it files items into the collection selected in the UI**, so it fits
only when the target collection does not matter (see "Cauza intai" above). New items into a
specific collection: `zot.adauga_itemi(items, colectie=...)`, or `add_item(collection_id=...)`
inside a `write_session` — the latter also when the same batch edits or attaches.

**Native annotation** (color encodes author — convention: purple `#a28ae5` = "Claude", yellow = user):
compute the highlight rectangles read-only with PyMuPDF while Zotero is open (Zotero/PDF.js use
CropBox-relative, bottom-left origin: `H − y`), queue them, then flush all writes in one pass with
Zotero closed. The two steps are in `zot.py`: `zot.gaseste_fragment(pdf_path, fragment,
de_la_cuvantul=0)` (read-only; matches on words, so line breaks and soft hyphens don't break it;
returns one piece per page, with `pagina`, `dreptunghiuri`, `text`, `top`, `offset`,
`eticheta_pagina`) and `zot.adauga_evidentiere(cur, attachment_key, comentariu="…", **piece)`
inside a `write_session` (type 1, author „Claude", purple, `sortIndex` = `page|offset|top`,
`textNormalized`/`commentNormalized` filled the way Zotero fills them). The comment is required.
A highlight with the same position on the same attachment is not written twice — the existing
one is returned. For statutes brought by `adu_lege.py`, `--evidentiaza "art. 11 alin. (1)"
"<comment>"` does both in the act's own write cycle.

**What to highlight — the rule, not just the mechanism.** Highlight the sentence or paragraph the
manuscript's claim actually rests on, never a keyword from the title page or the abstract. The
annotation's comment states what the source asserts there *and* how the text uses it, including
any fit limitation we declared and any step that is our inference rather than theirs. Test before
writing: read the highlighted span alone — if it does not support the manuscript's claim, the
highlight is in the wrong place. Auto-harvesting the longest word-run matching a stored quote
breaks this rule even when it lands on the right source: it produced highlights reading
"corporatism", "electoral competition", "strategic entry" on title pages, on sources whose
load-bearing passages sit thirty pages in.

**Child note** (summaries, caches): `zot.add_child_note(cur, parent_item_id, note_text)` —
works for both a top-level item and an **attachment** as parent (Zotero's PDF-reader sidebar
uses attachment-as-parent for "add note from annotations"). `zot.find_child_notes(parent_item_id)`
reads them back (read-only); `zot.update_note(cur, note_item_id, note_text)` overwrites one in
place. **Write PLAIN TEXT** (blank-line paragraph breaks), NOT HTML: externally-written note HTML
is escaped/sanitized at the next startup (wrapped in `zotero-note znv1`, tags rendered as literal
text). Zotero converts the plain text to proper `<p>` paragraphs itself, stably — but also
substitutes `&nbsp;` for runs of 2+ consecutive spaces within a line, so anything read back that
needs to stay substring-searchable (e.g. a text cache) must fold NBSP back to a plain space; see
`references/pdf-text-cache.md` for the worked example (`pdf_text_cache.py` memoizes PDF text
extraction this way).

**Wrap a bare attachment in a proper bibliographic item — NOT automatic, check for it.**
Dropping a PDF into a collection (drag-and-drop, or `attach_pdf` on its own) creates a top-level
`attachment` item with a generic title and `parentItem: NULL` — Zotero/the skill does **not**
infer a `thesis`/`journalArticle`/etc. wrapper with real metadata (author, university, date) from
the file. Typical shape: a PDF lands in a collection as a bare attachment carrying only the
filename as its title (`Nume_2026`) instead of a proper `thesis` item — check
`itemType='attachment' AND parentItem IS NULL` with a generic title before assuming a
collection's bibliography is complete. Fix in the same `write_session` that
would otherwise separately create-then-reparent (one cycle, not two):
```python
with zot.write_session("wrap-thesis") as cur:
    tid = zot.add_item(cur, "thesis",
        {"title": "…", "thesisType": "Lucrare de licență", "university": "…",
         "place": "…", "date": "2026", "numPages": "…", "language": "ro",
         "extra": "Coordonator: …"},
        creators=[("author", "Lastname", "Firstname")], collection_id=cid)
    att_id = cur.execute("SELECT itemID FROM items WHERE key=?", (bare_attachment_key,)).fetchone()[0]
    cur.execute("UPDATE itemAttachments SET parentItemID=? WHERE itemID=?", (tid, att_id))
    cur.execute("DELETE FROM collectionItems WHERE itemID=?", (att_id,))  # only the parent stays filed
    zot.touch(cur, att_id)
```
Pull the metadata from the PDF's own title page (coordinator/absolvent, university, faculty,
program, place, year) — don't guess. If the same pass also needs the text cached, call
`pdf_text_cache.ensure_cached(cur, attachment_key, pdf_path)` inside the same session (see
`references/pdf-text-cache.md`) — one close/reopen for both fixes.

**Calea fișierului se citește ÎN ciclu, nu înaintea lui.** Cât Zotero e deschis, el poate crea singur
părintele atașamentului (preluarea metadatelor) și redenumește fișierul după el:
`showEmbedPdf.pdf` devine „Autor și Autor - 2002 - Titlu….pdf". Măsurat pe 27 septembrie 2026: calea
citită înainte de închidere nu mai exista în ciclu, `ensure_cached` a căzut cu `FileNotFoundError`,
iar sesiunea s-a anulat. Deci în ciclu, din baza închisă:

```python
    pdf_path = zot.attachment_path(bare_attachment_key, cur)   # în ciclu, pe cursorul lui
    pdf_text_cache.ensure_cached(cur, bare_attachment_key, pdf_path)
```

Și, la reluare, **verifică dacă părintele există deja**: Zotero l-a putut crea între timp. `add_item`
îl găsește după DOI sau titlu, nu face copie și îi completează câmpurile goale — inclusiv linia
„Ancorează:" din `extra` —, fără să atingă ce e plin.

## Phantom attachments (and the "Cannot change attachment linkMode" sync error)

A sync from another device can insert an `itemAttachments` row (`linkMode=0` imported_file)
whose bytes were **never downloaded to this machine**: `storageHash` is NULL and the
`storage/<key>/` folder does not exist. These are **not files you lost** — they are stubs.
They are the usual cause of Zotero's sync error **"Cannot change attachment linkMode"**: a
local imported-file stub with no bytes cannot be reconciled against the server's copy.

Tell them apart from a legitimately-not-yet-downloaded file: a real pending download keeps a
`storageHash` (sync metadata) even before the bytes arrive. **No hash AND no folder = phantom.**
Trashing the phantom rows (they carry no local file, and — check first — no annotations) clears
the conflict; on the next sync Zotero propagates the deletion. `scripts/audit_library.py`
flags them (section B); `scripts/reconcile_attachments.py` trashes the annotation-free ones.

```sql
-- phantom PDF attachment rows (break linkMode sync); exclude ones that still carry annotations
SELECT ia.itemID FROM itemAttachments ia
WHERE ia.contentType='application/pdf' AND ia.storageHash IS NULL
  AND ia.itemID NOT IN (SELECT itemID FROM deletedItems)
  AND ia.itemID NOT IN (SELECT parentItemID FROM itemAnnotations);
-- then confirm storage/<key> is absent on disk before trashing
```

**A phantom WITH annotations is often rescuable — check its own parent item first.** In the
real case that produced this recipe, all 7 annotated phantoms sat on items that ALSO had a
live copy of the same document as a second attachment. Before writing them off as "file on
another device", verify the live twin is the **same rendition** empirically: for each stored
highlight, extract text from the live PDF at the annotation's `position` rects
(`page.get_text(clip=...)`, try both y-origins) and compare with the annotation's `text`
(61/63 matched). If it matches, move ONLY the annotations the live copy lacks — dedup on the
order-independent bbox signature (`reconcile_attachments.ann_sig`), since an earlier recovery
pass may have already copied most of them — then trash the phantom. Annotations transfer
safely here because same rendition ⇒ same coordinate space; never do this across different
renditions.

## Reconciling duplicate PDFs on one item (keep the editable/OCR copy)

When an item has two+ present PDF files, keep the **text-extractable** one (text-native, or a
scan with an OCR overlay) over an image-only scan — `ocr_overlay.detect_scan()` classifies each.
The hard constraint: **annotations are in one rendition's coordinates and cannot move to a
different-content file** (the rectangles would land on the wrong text). So only ever discard a
loser that (a) is byte-identical to the keeper — move its unique annotations first, by the
order-independent bbox signature — or (b) carries **zero** annotations. A scan that *has*
annotations is an **OCR candidate**, not trash: overlay it (ask-first) so it becomes the
editable keeper. `scripts/reconcile_attachments.py` encodes exactly this; it never discards an
annotated, different-content copy.

## Gotchas

- **Shutdown race:** after a graceful kill, a residual Zotero process may flush AFTER your write
  and silently discard it. Wait until all `zotero.exe` processes AND `zotero.sqlite-journal` and any non-empty `-wal`
  are gone, write, then **verify on disk** before restarting Zotero.
- The **local API omits annotations** from `/items/<attKey>/children` — a zero result does not mean
  the highlights failed; check `itemAnnotations` in the DB or the Zotero reader.
- A junk-looking attachment titled like a date (`1969-01-01…`) is usually the item's **PDF**
  (`itemAttachments.parentItemID` points to the real item) — NOT a duplicate to delete. Check first.
- Duplicate/junk bib items do happen (mangled titles, wrong years) — verify the resolved item
  (author + year + title) before citing or editing; fuzzy lookups can hit the wrong one.
- `place`/`publisher` are easy to enter swapped → check rendered output, not just the field.
- **Trashed items don't disappear from the tables** — an `INSERT OR IGNORE` into `deletedItems`
  can silently no-op if the item was already trashed (PK conflict on `itemID`), which is itself
  a useful tell that it was trashed earlier than you thought. See "Trashed items still appear in
  every table" above — apply `zot.NOT_TRASHED` to any read query over item/attachment tables.
