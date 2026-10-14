# Pagina HTML, ca ieșire de sine stătătoare

Cererea din care s-a născut, 23 august 2026: *„mi-e mai comod cu un html decât cu un docx"*.
Pagina web nu mai e o probă înaintea docx-ului — e forma în care se citește manuscrisul în
timpul lucrului. Docx-ul rămâne pentru ce pleacă spre instituție.

## Două scripturi, fiindcă sunt două lucruri

`build_manuscris.py` face **corpul**: rezolvă citările din Zotero și randează cu pandoc.
`pagina_html.py` face **învelișul**: tipografia, cuprinsul pe margine, bara de file când
documentul are părți, marcarea textului nefinalizat. Se schimbă unul fără celălalt, și fiecare
se poate folosi singur.

Înainte, învelișul îl scria fiecare proiect — adică fiecare avea a doua sursă de adevăr pentru
ceva ce e al skill-ului. Proiectul păstrează acum doar **ce documente intră și în ce ordine**:

```json
{
  "titlu": "Planul lucrării",
  "parti": [
    {"eticheta": "Partea I",  "scurt": "Ontologia", "fisier": "ontologie.md"},
    {"eticheta": "Partea II", "scurt": "Datele",    "fisier": "date.md"}
  ],
  "legenda": "Textul în gri și italic nu este finalizat.",
  "nota": "Pagina se construiește din surse; nu se editează în pagină."
}
```

```bash
python pagina_html.py --config pagina.json
python pagina_html.py doc.md --out doc.html     # o singură bucată, fără file
```

Titlurile documentelor coboară un nivel la alipire — fiecare `#` devine `##`, sub titlul părții
—, deci sursele se scriu cu secțiuni de nivel 1, iar titlul stă în front-matter.

## Trei lucruri măsurate, pe care pagina le are deja rezolvate

**Măsura de lectură stă pe copiii secțiunii, nu pe `main`.** Conținutul e în `section.fila`, deci
`main { max-width: 72ch }` strânge un tabel de douăsprezece coloane până devine ilizibil, iar
`main > p` nu prinde nimic. Regula corectă e `.fila > *`, cu `.fila > .tabel` exceptat.

**Tabelele își iau lățimea din conținut.** Pandoc scrie `<colgroup>` cu procente; într-un
container cu `overflow-x: auto` procentele se bat cap în cap cu lățimea tabelului, fiecare
derulare cere o reașezare, iar pagina îngheață — măsurat în Chrome. `build_manuscris.py` scoate
`<colgroup>` la ieșirea HTML; perechea din foaia de stil e `width: max-content; min-width: 100%`
pe `table`, în container cu derulare.

**Bara de file apare abia din JavaScript.** Fără el, părțile rămân vizibile una sub alta, în loc
să dispară toate în afară de prima.

## Pagina publicată ca artefact

A doua formă a aceleiași ieșiri, cerută de autor pentru același motiv: se deschide de oriunde, se
dă cuiva printr-un link, și se vede dintr-o privire ce s-a schimbat. Docx-ul rămâne pentru
predare.

Ce produce `pagina_html.py` e deja gata de publicat: fișierul n-are `<!doctype>`, `<html>` sau
`<body>` — exact ce cere unealta de publicare, care le pune ea — și are `<title>`, din care se
face numele din galerie. Fonturile Google trec de politica de conținut a paginii publicate; altă
gazdă de fonturi, nu.

**La republicare se pasează adresa existentă.** Altfel iese un artefact nou, cu alt link, iar
cine avea linkul vechi rămâne pe o versiune care nu se mai schimbă.

### Trei discipline, toate plătite o dată

**Pagina se generează din manuscris, niciodată scrisă de mână.** Măsurat, 21–23 august 2026: o
pagină scrisă de mână a divergat tăcut de manuscris în **două zile** — mai spunea lucruri scoase
între timp din tabel. Testul e simplu: schimbi o frază în manuscris, republici, pagina o poartă.
Dacă generatorul are text propriu în afară de etichetele de structură, ai a doua sursă de adevăr,
iar regula 11 spune ce se întâmplă cu ea.

**Nicio cifră în capul paginii nu se scrie de mână.** Contoarele — câte rânduri, câte poartă o
sursă cu locator, câte sunt încă deschise — se numără din conținut la fiecare rulare. În aceeași
pagină scrisă de mână, contorul spunea „12 cu sursă"; realitatea era 18. O cifră scrisă de mână
nu e greșită azi, e greșită mâine, tăcut.

**Marcajele de stare se văd, nu se citesc.** Într-o hartă de douăzeci și patru de rânduri, un
`*open*` scris în italic printre alte italice trebuie căutat. `pagina_html.py` îl face etichetă,
dacă i se dau marcajele în configurație — vocabularul e al proiectului, nu al uneltei:

```json
"marcaje": ["open", "own", "own specification"]
```

Se înlocuiesc de la cel mai lung la cel mai scurt, altfel `own` ar tăia din `own specification`.
Și spațiul dintre cuvinte se caută ca `\s+`, nu literal: **pandoc rupe rândul unde îi vine,
inclusiv în interiorul marcajului** — măsurat, un marcaj din două cuvinte a ieșit cu linie nouă
între ele, iar potrivirea literală trecea pe lângă exact marcajele lungi, adică pe lângă cele mai
informative.

### Gazda pune un `<base>`, deci nicio adresă relativă nu înseamnă ce pare

Pagina publicată rulează într-un cadru în care gazda injectează în `<head>` un
`<base href="/_f/<hash>/">`. De aici încolo, **orice adresă relativă din pagină se rezolvă față de
base, nu față de documentul curent** — inclusiv o legătură scrisă doar ca fragment. Măsurat:
`#ancoră` se rezolvă la `<base>#ancoră`, adică la alt document.

Simptomul, așa cum îl vede autorul: din cuprins, orice clic pare să te trimită acasă. Ce se
întâmplă de fapt e că pagina chiar navighează la rădăcina învelișului, se reîncarcă, nu găsește
nimic în `location.hash` și cade pe prima filă.

**Nu se vede la nicio probă locală**, fiindcă local nu există `<base>`. Se reproduce împachetând
pagina scrisă într-un `<head>` cu `<base href="/_f/abc123/">` și servind-o peste HTTP; verificarea
e o linie: `document.querySelector('nav.cuprins a').href` arată adresa **rezolvată**, nu cea
scrisă.

Deci pagina nu se mai bizuie pe navigare pentru sărituri interne:

- **cuprinsul își face singur săritura** — prinde clicul, arată fila care conține ținta, derulează
  la ea. Când ancora lipsește, lasă navigarea să se descurce; când documentul e dintr-o bucată, nu
  e nimic de comutat, iar restul rămâne necesar;
- **adresa se scrie întreagă, niciodată ca fragment singur.** `history.replaceState` rezolvă și el
  argumentul față de `<base>`, exact ca un href — deci `replaceState(null, '', '#x')` mută adresa
  la `/_f/<hash>/#x` fără să reîncarce nimic. Pagina pare în regulă până când cineva reîncarcă sau
  dă linkul mai departe. Se scrie `location.pathname + location.search + '#' + id`.

A doua e cea care nu se vede: prima strică pe loc, a doua strică mai târziu, la altcineva.

### Pinul de partajare nu se mută singur

**Un artefact partajat are un pin, iar republicarea nu-l mută.** Cititorul rămâne pe versiunea
veche până când proprietarul mută pinul din meniul de partajare. Măsurat: cineva a citit două zile
o versiune veche fără ca nimeni să bănuiască — pagina se republica, adresa era aceeași, și tot ce
vedea el era vechi.

Deci publicarea unei versiuni pe care o citește altcineva are **doi pași**, nu unul: republici, și
apoi muți pinul. Al doilea nu se vede din partea ta.

### Ce trebuie să aibă orice pagină, oricine o construiește

Patru lucruri, fiecare plătit o dată. Șablonul din skill le are; cine își scrie propriul generator
trebuie să și le pună singur.

**`<meta charset="utf-8">`, chiar dacă platforma pune un `<head>` al ei.** Măsurat, 24 august
2026: fără el, pagina servită de un server care nu trimite antetul de codare se citește ca
`windows-1252`, iar titlul iese `Documentul Ã®ntreg`. Publicată, pagina poate fi în regulă; locală
sau pe alt server, nu. Costă o linie. Alături merge `<meta name="viewport">`, fără de care pe
telefon pagina se randează la lățime de desktop și se micșorează.

**`minmax(0, …)` pe coloanele de grilă, și `min-width: 0` pe copiii ei.** Implicit, un element de
grilă nu se lasă mai îngust decât conținutul lui, deci o adresă lungă fără prilej de rupere împinge
coloana și pagina capătă derulare orizontală. Verificat pe un șir de 240 de caractere: cu
`minmax(0, 1fr)` pe traseu, documentul nu depășește fereastra.

**Pragul la care aranjarea pe coloane se desface.** Un tabel de trei coloane nu se citește pe
telefon, dar o listă de blocuri se citește. Sub prag, capul de tabel dispare — nu mai etichetează
nimic — și fiecare rând devine o coloană.

**Marcajele ies din cursiv.** Ele vin din `*text*`, deci moștenesc `font-style: italic`; ca să
arate a etichetă, au nevoie de `font-style: normal` explicit. Altfel rămân un cursiv colorat, adică
tot text.

### Când documentul are structura lui

O hartă pe coloane, o listă de fișe, un tabel cu blocuri per rând — astea nu încap în șablonul
paginii, și atunci generatorul rămâne al proiectului. Antetul, însă, se repetă de la un proiect la
altul, și merită copiat ca formă: supratitlu, titlu, un paragraf care spune ce e pagina, și
contoarele — fiecare număr citit din conținut la fiecare rulare, niciunul scris de mână.

### Când pagina se construiește de mână, nu prin `pagina_html.py`

Un document cu structură proprie — o hartă pe coloane, o listă de fișe — nu încape în șablonul
paginii, și atunci proiectul își scrie generatorul lui. Trei lucruri de știut, toate măsurate:

- `render(..., standalone=False)` pentru un **fragment**; altfel pandoc pune un antet YAML în
  ieșire, iar conducta care o consumă îl primește ca text;
- `--wrap=none`, cu dialectul de markdown controlat, dacă ieșirea se prelucrează mai departe;
- **autolinkurile lui citeproc**: DOI-urile ies ca `<https://doi.org/...>`. Cine randează direct
  în HTML nu vede nimic — pandoc le face ancore. Cine ia markdownul cu citările rezolvate și își
  construiește pagina primește șirul ca atare, îl scapă la afișare, și ajunge cu
  `&lt;https://doi.org/...&gt;` în bibliografie: text, nu legătură.
  `build_manuscris.linkuri_din_autolink(html_scapat)` le face ancore, de chemat **după**
  `html.escape`.

## Când documentul e un singur fișier

Multe documente trăiesc ca **un** `.md`, cu părțile ca secțiuni de nivel 1: așa se scriu, așa se
citează, așa iese docx-ul. Cerând fișiere separate, unealta l-ar obliga pe om să țină două
structuri sincron cu mâna — a doua sursă de adevăr, exact ce interzice regula 11.

```bash
python pagina_html.py master.md --pe-parti
```

Despărțirea se face **la construire**. Părțile sunt derivate: nu există pe disc, nu se editează,
nu se sincronizează. Etichetele și numele filelor se deduc — „Partea I", „Partea II", iar numele
secțiunii devine numele filei.

Când deducerea nu e bună, se scrie în front-matter-ul masterului, unde stau oricum titlul,
legenda, nota și marcajele. Tot acolo intră și fișiere **din afara** masterului, adică anexele:

```yaml
---
title: "Documentul întreg"
parti:
  - sectiune: "Datele"          # numele secțiunii de nivel 1 din master
    eticheta: "Partea I"
    scurt: "Ce date"
  - fisier: "anexa-tehnica.md"  # fișier separat, inserat aici în ordine
    eticheta: "Anexa"
    scurt: "Anexa tehnică"
---
```

Ordinea din `parti` decide ordinea filelor; secțiunile nepomenite rămân în ordinea lor, după. O
etichetă lipsă se numerotează **după poziția în listă**, nu după câte au fost completate — altfel
o etichetă scrisă de mână și una dedusă ajung același număr, și ies două file cu același nume.

**Un amănunt de nivel, care se vede doar la citit codul:** părțile venite din fișiere trec prin
`citeste_md`, care le coboară titlurile cu un nivel; cele din master **nu** trec pe acolo, iar în
master subsecțiunile sunt deja la nivelul al doilea — adică exact unde trebuie să ajungă. O urcare
„compensatoare" le scoate la nivelul întâi, unde cuprinsul, care citește `h2`, nu le mai vede
deloc.

## Fila de referințe

Convenția casei: referințele sunt ultima filă, ca într-un document. Merge de la sine, fiindcă
citeproc varsă bibliografia la coada documentului combinat — adică în ultima parte. Partea aia se
scrie ca un fișier cu titlu și corp gol; nu e nevoie de nimic în plus.

**Un amănunt care a costat o construire întreagă:** citeproc marchează `unnumbered` antetul
secțiunii în care pune bibliografia. Deci ultima parte a oricărui document cu citări iese cu
`class="unnumbered parte"`, nu cu `class="parte"`. Scriptul caută clasa **ca un cuvânt** în
atribut, tocmai pentru asta — pandoc nu garantează nici ordinea claselor, nici că ale noastre
rămân singure. Cine adaugă altă potrivire pe clase în script are aici motivul să n-o scrie
literal.

Când vrei bibliografia altundeva decât la coadă, pui în corpul părții divul explicit:

```markdown
::: {#refs}
:::
```

Citeproc își varsă intrările acolo și nu mai atinge titlul părții.

## Caracterele de control opresc scrierea

`U+0001` strecurat printr-o editare cu heredoc a ajuns odată **în interiorul etichetei de tabel**:
pagina a ieșit cu `<table` + caracterul + `>`. Browserul nu recunoaște asta ca tabel — îl tratează
ca element necunoscut, iar celulele curg una după alta ca text simplu.

Ce face capcana costisitoare e că **orice verificare prin căutare de text trece**: `grep "<table"`
găsește toate tabelele, fiindcă șirul căutat chiar e acolo. Cauza s-a văzut abia la inspecția
octet cu octet, după patru runde în care autorul a spus că nu poate citi tabelele și i s-a
răspuns că pagina e corectă.

Deci `verifica_fara_control` rulează la intrare și la ieșire, iar ieșirea deja scrisă se șterge
dacă e stricată. `pagina_html.py` verifică **fișierele sursă**, înainte de a le lipi: altfel
mesajul ar numi markdown-ul temporar combinat, cu altă numerotare a liniilor, adică un fișier pe
care omul nu-l poate deschide și nu-l poate repara. Mesajul arată caracterul invizibil în
contextul lui:

```
doi.md contine un caracter de control (U+0001), linia 6.
  ...# Surse<U+0001>le\n\nAlt text...
```
