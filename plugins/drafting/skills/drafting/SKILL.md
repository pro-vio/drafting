---
name: drafting
description: >-
  Produce and revise a manuscript end to end: edit a Word (.docx) as TRACKED CHANGES (author
  "Claude") so the author accepts or rejects every suggestion instead of receiving a rewrite,
  and manage its references with Zotero as the single source of truth — live in-text citations
  plus an auto References list via pandoc + citeproc (any CSL style), and a chronological
  "Legislation and case law" list that CSL cannot express. Use whenever someone is revising a
  paper, article, thesis or report and wants suggestions as tracked changes ("implement the
  reviewer's comments as track changes", "reply to the comments in this Word doc", "corectează exprimarea fără să-mi schimbi vocea", "add a footnote for this source", "turn this line into a
  heading"), and whenever they do citation or bibliography work backed by a reference manager
  ("rebuild the bibliography", "references went stale after accepting track changes",
  "regenerate citations", "switch the style to APA", "update the citation styles", "cite this from my Zotero library", "add a
  legislation list"), including editing `[@citekey]` markers, CSL-JSON/.bib or a Works-Cited
  section. Also fixes Zotero items that render wrong (ALL-CAPS titles, swapped place/publisher,
  ugly citekeys, reprint dates, annotations) via direct DB edits, since the live API is
  read-only, audits the library, reconciles duplicate PDF attachments without losing
  annotations, offers an ask-first OCR overlay for scans, and memoizes PDF text extraction.
  Also brings Romanian legislation into Zotero from the official portal (legislatie.just.ro):
  the consolidated form or the form in force at a given date, as a statute item with a
  text-layer PDF and cached text — "adu legea", "adu actul normativ", "forma în vigoare la
  data", "act normativ în Zotero", "Portalul Legislativ", "Codul fiscal în Zotero".
  Not for creating a .docx from scratch (use the docx skill) and not for Zotero app setup.
---

# Drafting — manuscrisul și aparatul lui

Două jumătăți ale aceleiași munci: **textul**, editat ca schimbări urmărite, și **aparatul de
referințe**, generat din Zotero. Se folosesc de obicei împreună — o citare inserată în Word e
un track change al cărui conținut vine din bibliotecă — dar fiecare are drumul ei.

| Ce vrei | Unde scrie | |
|---|---|---|
| schimbări urmărite, comentarii, note de subsol, corectură care păstrează vocea | `references/track-changes.md` | `scripts/docx_track.py`, `scripts/validate_docx.py` |
| citări live, listă de referințe, listă de legislație, reparat iteme Zotero, audit, OCR, cache | `references/citari-zotero.md` | `scripts/build_manuscris.py`, `scripts/build_legislation_list.py`, `scripts/zot.py` |
| un act normativ adus în Zotero, în forma în vigoare azi sau la o dată | `references/ro-legislation-fetch.md` | `scripts/adu_lege.py` |

**Procurarea a trecut la `lit-review`.** Cine caută sursele le și procură: `anelis.py` și
documentul lui stau acum acolo, împreună cu etapa care le cheamă. Aici rămâne ce se face **cu**
biblioteca — citări, bibliografie, legislație, reparații de iteme, memoizarea textului.

## Regulile care țin peste amândouă

**Tu propui, autorul dispune.** Niciun document rescris tăcut. Ce nu se poate exprima ca
schimbare urmărită se semnalează într-un comentariu și se tratează separat — nu se strecoară.

**Referințele se utilizează, nu se afișează.** O sursă se citează doar dacă textul îi folosește
definiția, procedura sau cifra. Textul se propune întâi; autorul citește sursa după, deci la
afirmații egale câștigă sursa mai ușor de procurat și de citit — adesea articolul. Cărțile și
capitolele intră doar unde afirmația trăiește doar acolo. Fără alternative „de acoperire".

**Întâi metadatele, verificate pe text; abia apoi citarea.** Decizia autorului, 12 octombrie 2026.
Primul lucru la citirea unei surse e verificarea metadatelor ei pe textul fișierului — titlu,
autori, an, DOI, revistă; la actele normative, forma din antet și notele de modificare — și
semnalarea fiecărei nepotriviri. **Nu se citează un item ale cărui metadate n-au fost verificate pe
text, decât ca excepție aprobată de autor.** Uneltele: `verifica_metadate.py` (orice sursă; unde DOI-ul
sau anul lipsesc din PDF, le verifică la Crossref; autorul-organizație, pe pagina care publică documentul, după `references/organizatii-domenii.json`; la mai multe PDF-uri, pe toate; software-ul, pe mediul proiectului care l-a folosit, cu `--proiect`) și `completeaza_forme.py` (actele normative, din
toată biblioteca, nu dintr-o colecție). Itemul verificat poartă în Extra „Metadate verificate pe
text: AAAA-LL-ZZ"; `build_manuscris.py` refuză să randeze cu citări fără marcaj, iar excepția
aprobată se dă cu `--exceptie CHEIE`. Textul se ia din cache; la miss se extrage și se memoizează.
Cazurile care au cerut regula, din aceeași săptămână: un fișier etichetat cu altă formă a actului
decât cea pe care o conține, anii apariției online în locul anului volumului, un autor scris greșit,
o carte cu DOI-ul unui capitol.

**Verbatim doar la comandă; altfel, pe stil — și nu se mai întreabă.** Decizia autorului, 13
august 2026. Fără o comandă explicită („cuvânt cu cuvânt", „textul ăsta exact", „nu-l atinge"),
ce dictează el se reformulează în registrul din `stil-proteasa`, ca orice text scris pentru el.
Cu comandă, se copiază **literal** din conversație, niciodată din memoria a ce a vrut să spună;
dacă nu există un șir exact, se cere — nu se compune.

**Logica și structura rămân ale lui.** Reformularea schimbă cuvintele, nu ce spun ele: nu
completează un gând neterminat, nu adaugă o afirmație pe care n-a făcut-o, nu mută conținutul în
altă secțiune. Motivul, cu prețul lui, e în `references/track-changes.md`, principiul G.

**Dar se verifică ce a spus mai devreme, fiindcă se răzgândește des.** Nu se ia prima
formulare găsită căutând înapoi, ci **ultima** — și se spune într-o linie care a fost luată.
Regula ține și pentru indicațiile despre o secțiune: ultima o înlocuiește pe cea dinainte.
Amândouă, cu prețul lor, în `references/track-changes.md`, principiul G-bis.

**Citarea stă pe sensul din textul sursei, niciodată pe un cuvânt de pe pagina de titlu sau
din rezumat.** Titlul și rezumatul spun despre ce e articolul; ei nu spun ce afirmă el în locul
de care avem nevoie. O citare se sprijină pe fraza din corpul textului — sau pe materialul
bibliografic pe care îl avem efectiv, când sursa e o carte, un capitol sau un act normativ.
Testul, înainte de a insera citarea: pot arăta fraza care o susține, cu locator? Dacă nu, sursa
nu e verificată, iar asta se spune, nu se acoperă.

**Sublinierile din PDF urmează aceeași regulă.** Se subliniază fraza sau paragraful pe care stă
afirmația din manuscris, niciodată un cuvânt-cheie de pe prima pagină. Comentariul adnotării
spune două lucruri: ce afirmă sursa acolo, și cum e folosită în textul nostru — inclusiv unde am
declarat o limită de potrivire și unde concluzia e a noastră, nu a ei. Testul: dacă textul
subliniat, citit singur, nu susține afirmația din manuscris, sublinierea e pusă greșit. Recolta
automată pe cuvânt-cheie încalcă regula chiar când nimerește sursa potrivită.

**Un item intră în Zotero numai cu PDF-ul lui.** Fișierul îl descarcă agentul, când e liber, sau
îl adaugă autorul; itemul se scrie abia după aceea, cu fișierul atașat. Un item fără PDF e
excepție și cere **aprobarea explicită a autorului, pentru acel item** — nu se deduce din „adaugă-le
în Zotero". Când fișierul nu se poate lua automat, se dă autorului lista de descărcat, grupată pe
platforme, și se așteaptă. Motivul: un item fără fișier pare rezolvat în bibliotecă, dar nu se poate
citi, nici cita cu locator, nici memoiza, iar autorul îl descoperă gol abia când îl deschide.
Instrucțiunea lui, 23 septembrie 2026, după ce cinci articole închise au fost scrise fără PDF la
cererea „adaugă cele cinci în Zotero": *„descarci sau adaug eu. fără pdf este excepție, doar cu
aprobarea mea explicită"*.

**Fiecare sursă adăugată spune de ce a fost adusă.** În câmpul `Extra` al itemului merge o
linie `Ancorează: <ce afirmație din manuscris se sprijină pe ea>`. Costă o linie la adăugare și
se plătește când abonamentul e limitat: ea decide ce se procură întâi, iar raportul de procurare
o tipărește lângă fiecare intrare.

Se scrie **în momentul adăugării**, nu după — atunci știi de ce ai adus sursa. Scrisă mai
târziu, se transformă în rezumat al lucrării, iar rezumatul e deja în item și nu ajută la nimic.
Testul e afirmația: „refuzul de a răspunde e el însuși informație, iar auditorii care refuză pot
fi folosiți ca să compromită confidențialitatea" ține; „lucrare despre auditul interogărilor" nu
ține.

**Orice intrare în Zotero începe la cache.** `get_cached` întâi — read-only, merge cu Zotero
deschis — extracție doar la miss, și cache-ul se populează chiar la acel miss. Când Zotero e
deschis și nu se poate scrie, extrag dar spun că memoizarea a rămas nefăcută.
Detaliile: `references/pdf-text-cache.md`. **Ordinea căutării — întâi textele memoizate, apoi biblioteca, abia apoi internetul — cu comanda fiecărei trepte și cu ce trebuie să întoarcă una ca să treci la următoarea: `references/ordinea-cautarii.md`.** De citit înainte de orice căutare pe web despre o sursă.

**Un ciclu de închidere per sarcină.** API-ul local e read-only pentru editări: ele se scriu
direct în `zotero.sqlite`, cu Zotero închis și cu backup. Toate scrierile unei sarcini intră
într-un singur `write_session` — cheile de colecție se rezolvă cât Zotero e încă deschis.

**Ce n-ai putut scrie se lasă, nu se pierde.** Când Zotero e ținut deschis de altcineva,
`scrieri_amanate.py` primește sarcina ca **date** — nu ca script —, iar prima sesiune care ajunge
la baza liberă o scrie odată cu ale celorlalți, într-un singur ciclu.

**Nu ești singur în Zotero.** `write_session` își ia rândul înaintea închiderii și așteaptă
dacă scrie altcineva, deci scrierile paralele nu se mai calcă. Iar când contează în ce colecție
ajung itemii, ținta se scrie explicit — `add_item(collection_id=...)`, nu conectorul, care îi
așază în colecția selectată în interfață și deci se mută sub tine. Amândouă, în
`references/zotero-schema.md`.

**La o restaurare, serverul nu e o copie de siguranță.** Sincronizarea aliniază la server și
datele, și fișierele, deci șterge tot ce exista doar local. Înaintea oricărei sincronizări:
sincronizarea de fișiere oprită de om, copia străină sub copie de siguranță, inventarul contra
serverului. O colecție care lipsește de pe server se urcă din nou cu `zot.reurca_colectie`;
`add_collection` refolosește colecția cu același nume. Ordinea completă: `references/runtime-optimization.md`,
„Restaurarea după o mutare".

**Pagina web e o ieșire, nu o probă.** `build_manuscris.py` face corpul cu citările rezolvate;
`pagina_html.py` îl împachetează într-o pagină de citit — tipografie, cuprins pe margine, bară de
file la documentele pe părți, text nefinalizat marcat. Proiectul dă doar lista fișierelor și ordinea lor,
într-un `pagina.json` — sau, când documentul e un singur `.md` cu părțile ca secțiuni, nimic:
`--pe-parti` desparte la construire. Docx-ul rămâne pentru ce pleacă spre instituție.
Detaliile: `references/pagina-html.md`.

**Validarea nu se sare.** Un `document.xml` editat de mână poate fi stricat în feluri pe care
Word le repară tăcut. `validate_docx.py` rulează pandoc în trei feluri — simplu, cu schimbările
acceptate, cu ele respinse — iar `--grep` confirmă că o inserare anume a ajuns în textul
acceptat.

## Unde se întâlnesc cele două jumătăți

Citarea academică trăiește ca `[@key]` în masterul `.md` și se randează cu pandoc + citeproc;
în Word intră **forma randată**, „(Autor An)", ca schimbare urmărită. Legislația intră în text
ca titlu scurt, iar lista cronologică se generează separat. Presa nu intră în bibliografie: e
notă de subsol, Chicago, cu autorul și data verificate.

Când munca trece în Word, **acel .docx devine sursa de adevăr** pentru pasul acela; masterul
`.md` și lanțul Zotero rămân pentru regenerare, dacă autorul se întoarce la ele. Divergența
dintre ele se semnalează, nu se acoperă.

## Resurse

- `references/track-changes.md` — fluxul complet docx: helperul `Docx`, implementarea
  comentariilor unui recenzent, corectura care păstrează vocea, validarea, cele șapte principii.
- `references/citari-zotero.md` — fluxul complet de citări: randarea, lista de legislație,
  reparațiile în bază, auditul bibliotecii, reconcilierea atașamentelor.
- `references/ooxml-track-changes.md` — schema OOXML, capcana împărțirii în runs, comentarii
  în fir și note de subsol. **De citit înainte de a extinde helperul.**
- `references/zotero-schema.md` — tabelele, fieldID-urile, tiparele de scriere și de adnotare.
  **De citit înainte de orice editare directă în bază.**
- `scripts/adu_lege.py` — aduce un act normativ de pe Portalul Legislativ în Zotero: forma
  consolidată sau, cu `--la-data AAAA-LL-ZZ`, forma în vigoare la data aceea, ca item `statute`
  cu PDF și text memoizat, legat „Related" de celelalte forme ale aceluiași act. `--cauta
  "Legea 207/2015"` dă id-ul paginii; `--lista` aduce mai multe acte într-un singur ciclu, după
  ce arată tabelul formelor alese, iar `--confirma` le scrie. `--la-item CHEIE` pune forma adusă pe un item care există deja fără fișier (titlul scurt și notele lui rămân) — **forma e cea a textului pe care proiectul l-a folosit**, căutat întâi în folderul proiectului (antetul Portalului, notele), nu una aleasă la aducere; `--forma-publicata` ia textul de pe pagina de formă publicată a id-ului dat, de pildă o republicare pe care lista de forme a Portalului n-o arată. `--evidentiaza "art. 11 alin. (1)"
  "<comentariu>"` subliniază articolul sau alineatul în PDF, în același ciclu; pe un PDF deja
  în bibliotecă, highlight-ul se pune cu `zot.gaseste_fragment` și `zot.adauga_evidentiere`
  (`references/zotero-schema.md`). Colecția se dă întotdeauna. Titlul scurt lipsă se deduce din
  denumire și cod („OUG 111/2010”); un act căruia i-ar lipsi un câmp cerut de lista de legislație
  se oprește înainte de scriere. Un act aflat deja în bibliotecă, într-o singură formă, se filează
  în colecția cerută fără a fi adus din nou. Cu Zotero deschis, actul se aduce oricum, iar scrierea
  se lasă întreagă în `scrieri_amanate.py`. A stat
  în `hcl-asemt` până pe 30 septembrie 2026; calea veche e o trimitere care îl cheamă pe acesta.
- `scripts/verifica_acte.py --colectie <cheie>` — trec actele colecției prin lista de legislație?
  Arată actele cărora le lipsesc câmpuri, dublurile (același cod, aceeași formă) și formele care
  au același titlu scurt, pe care lista nu le poate deosebi. Read-only. Rulează singur la finalul
  lui `adu_lege.py` și la începutul lui `build_legislation_list.py`; `--propune` arată titlurile
  scurte deductibile, `--amana` le lasă în coada de scrieri amânate, împreună cu scoaterea
  creatorilor de pe acte (emitentul stă în `extra`, regula din `citari-zotero.md` §G).
- `scripts/completeaza_forme.py --colectie <cheie>` — completează formele aduse înainte de
  12.10.2026 cu actele de modificare care le-au produs și cu perioada în care s-au aplicat, din
  textul formei (cache întâi) și din istoricul Portalului. Implicit e probă; `--amana` lasă
  scrierea în coadă, `--fara CHEIE` ține deoparte un item de verificat.
- `scripts/adu_web.py` — o sursă web oficială (comunicat, pagină, PDF de pe un sit) ca item `webpage`, cu
  instantaneul atașat: textul citit în browser (`--text`) sau PDF-ul (`--pdf`); titlul, data și autorul se verifică
  pe text, autorul-organizație pe domeniul adresei. Forma: CMOS 18, 14.104 și 14.98 pentru pagini și comunicate;
  `--tip report` pentru o broșură, fișă sau raport emis de o instituție (14.117), cu instituția și genul scris pe
  document. Fără dată în text, `n.d.` și data accesării (data de creare din proprietățile fișierului nu e dată de
  publicare); data ultimei modificări de pe server se adaugă după data accesării, ca „versiunea de pe server din …”
  (`references/citari-zotero.md`, „Online sources without a displayed date”). Un fișier care nu-și scrie titlul și
  data în el (o schemă, un jurnal de modificări) se verifică și pe pagina care îl listează: `--pagina` primește
  textul ei, salvat. Nu creează dubluri: se oprește la aceeași adresă, la același fișier (amprenta din
  `storageHash`) sau la același titlu, și arată itemul existent. Scrierea merge prin coadă.
- `scripts/fetch_metadata.py` — metadate din Crossref pentru un item care CHIAR lipsește, cu
  filtrarea celor două capcane măsurate: notița de corectură și preprintul. Propune candidați,
  nu inserează.
- `scripts/readopt_cache_notes.py` — reatașează notele de cache desprinse de Zotero, o singură
  rulare, cu simulare implicită.
- **Procurarea surselor fără fișier** — unde se ia fiecare, grupat pe platforme, cu ce e liber
  verificat efectiv: în skill-ul `lit-review`, `scripts/anelis.py` și `references/anelis.md`.
  Se cheamă de acolo, prin etapa `procurare`; aici nu se rescrie.
- `scripts/pagina_html.py` — manuscrisul ca pagină de citit: cuprins pe margine, bară de file la
  documentele pe părți, text nefinalizat marcat. Proiectul dă doar `pagina.json` — ce documente
  intră și în ce ordine. Detaliile și capcanele: `references/pagina-html.md`.
- `references/chicago-surse-online.md` — **ce spune manualul** despre pagini web, bloguri,
  rețele sociale, forumuri, conținut generat de un model și comunicări personale, cu numărul
  secțiunii pentru fiecare. De deschis **înainte** de a compune forma unei citări dintr-o sursă
  online: skill-ul avea stilul (`chicago-author-date.csl`), adică implementarea regulilor, dar nu
  și regulile, iar forma se ghicea. `scripts/adu_chicago.py` aduce de la sursă materialul liber —
  ghidul de exemple și cuprinsul capitolelor 13 și 14 — în `references/chicago/`.
- `scripts/adu_stiluri.py` — compară stilurile CSL ținute în skill cu zotero.org, sursa din care
  și le ia Zotero; GitHub are copii mai vechi, deci nu e sursă. Spune dacă diferă doar data sau
  și regulile, iar o copie se înlocuiește numai la cererea autorului, cu `--inlocuieste <id>`.
  Starea fiecărui stil: `references/stiluri-csl.md`.
- `references/ordinea-cautarii.md` — cele trei trepte, ce comandă acoperă fiecare, ce întoarce când nu găsește, și ce nu acoperă niciuna.
- `references/pdf-text-cache.md` · `references/ocr-overlay.md` · `references/ro-legislation-fetch.md`
  · `references/runtime-optimization.md` · `references/chicago-author-date.csl`
