# -*- coding: utf-8 -*-
"""pagina_html.py — manuscrisul ca pagina de citit, nu ca fisier de deschis.

Cererea din care s-a nascut, 23 august 2026: *„mi-e mai comod cu un html decat cu un docx"*.
Pagina web e ieșire de sine statatoare; docx-ul ramane pentru ce pleaca spre institutie.

`build_manuscris.py` face corpul — rezolva citarile prin Zotero si pandoc. Scriptul asta face
**invelisul**: tipografia, cuprinsul pe margine, bara de file cand documentul are parti, si
marcarea textului nefinalizat. Sunt doua lucruri diferite si stau in doua scripturi diferite,
ca sa se poata schimba unul fara celalalt.

Inainte, fiecare proiect isi scria invelisul lui — adica avea o a doua sursa de adevar pentru
ceva ce e al skill-ului. Proiectul pastreaza acum doar **ce documente intra si in ce ordine**.

## Folosire

    python pagina_html.py --config pagina.json [--out pagina.html]
    python pagina_html.py master.md --pe-parti           # un singur .md, partile = sectiunile lui
    python pagina_html.py doc.md --out doc.html          # o singura bucata, fara file

Fisierul de configurare:

    {
      "titlu": "Planul lucrarii",
      "parti": [
        {"eticheta": "Partea I",  "scurt": "Ontologia", "fisier": "ontologie.md"},
        {"eticheta": "Partea II", "scurt": "Datele",    "fisier": "date.md"}
      ],
      "legenda": "Textul in gri si italic nu e finalizat.",
      "nota": "Pagina se construieste din surse; nu se editeaza in pagina."
    }

`titlu` si `parti` sunt obligatorii; `legenda` si `nota` se scriu doar daca exista. Caile
fisierelor se citesc fata de dosarul configuratiei.

## Ce face pagina, si de ce asa

- **Bara de file** apare doar cand sunt mai multe parti, si abia din JavaScript: fara el, partile
  raman vizibile una sub alta, in loc sa dispara.
- **Cuprinsul** se face din titlurile de nivel 2 ale fiecarei parti, cate unul per fila.
- **Masura de lectura sta pe copiii sectiunii**, nu pe `main`. Pusa pe `main`, strange un tabel
  de douasprezece coloane pana devine ilizibil; pusa pe `main > p`, nu prinde nimic, fiindca
  textul e in sectiuni.
- **Tabelele** stau intr-un container cu derulare orizontala si isi iau latimea din continut.
  `<colgroup>`-ul cu procente pus de pandoc il scoate `build_manuscris.py`; aici e perechea lui
  din foaia de stil.
- **Textul nefinalizat** se marcheaza in markdown cu clasa `nefinalizat` si se randeaza in gri
  si italic, ca sa se vada dintr-o privire ce e stabilit si ce nu.
"""

import html
import io
import json
import os
import re
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
    # Si stderr: altfel mesajul de eroare iese cu diacriticele scrise ca ș, fiindca
    # tracebackul scapa ce nu incape in codul de pagina mostenit al consolei Windows.
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

AICI = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(AICI, "build_manuscris.py")


def citeste_config(cale):
    """Configuratia, cu caile facute absolute fata de dosarul ei."""
    d = json.load(io.open(cale, encoding="utf-8"))
    baza = os.path.dirname(os.path.abspath(cale))
    if not d.get("parti"):
        raise SystemExit("configuratia n-are `parti`")
    for p in d["parti"]:
        if not p.get("fisier"):
            raise SystemExit("o parte n-are `fisier`")
        p["cale"] = os.path.join(baza, p["fisier"])
        if not os.path.exists(p["cale"]):
            raise SystemExit("lipseste fisierul: " + p["cale"])
    return d


ROMANE = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
          "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX"]


def _frontmatter(text):
    """(dict, corp) — front-matter-ul YAML si restul documentului."""
    if not text.startswith("---"):
        return {}, text
    _, fm, corp = text.split("---", 2)
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        raise SystemExit("pentru --pe-parti e nevoie de pyyaml: pip install pyyaml")
    return (yaml.safe_load(fm) or {}), corp


def parti_din_master(cale):
    """Configuratia, dintr-un singur `.md` cu partile ca sectiuni de nivel 1.

    Documentul traieste ca UN fisier: asa se scrie, asa se citeaza, asa iese docx-ul. Cerand
    fisiere separate, unealta il obliga pe om sa tina doua structuri sincron cu mana — a doua
    sursa de adevar, exact ce interzice regula 11. Aici despartirea se face la construire, iar
    partile sunt derivate: nu exista pe disc si nu se editeaza niciodata.

    Subsectiunile se URCA un nivel la despartire (`## -> #`), fiindca alipirea le coboara la loc.
    Fara urcare ar ajunge la al treilea nivel, iar cuprinsul — care citeste `h2` — ar iesi gol.

    Etichetele si scurtaturile filelor se deduc: „Partea I", „Partea II", si titlul sectiunii ca
    nume de fila. Cand nu e bine asa, se scriu in front-matter-ul masterului, si tot acolo pot
    intra si fisiere din afara lui — anexe:

        parti:
          - sectiune: "Datele"        # numele sectiunii din master
            eticheta: "Partea I"
            scurt: "Datele"
          - fisier: "anexa-tehnica.md"
            eticheta: "Anexa"
            scurt: "Anexa tehnica"

    Ordinea din `parti` decide ordinea filelor; sectiunile nepomenite raman in ordinea lor, dupa.
    `titlu`, `legenda`, `nota` si `marcaje` se iau tot din front-matter."""
    fm, corp = _frontmatter(io.open(cale, encoding="utf-8").read())
    baza = os.path.dirname(os.path.abspath(cale))

    # sectiunile de nivel 1, in ordinea din document
    bucati = re.split(r"^# (?!#)", corp, flags=re.M)[1:]
    sectiuni = []
    for b in bucati:
        linii = b.split("\n")
        titlu = linii[0].strip()
        # atributele pandoc de pe titlu, daca exista, nu intra in numele filei
        titlu = re.sub(r"\s*\{[^}]*\}\s*$", "", titlu).strip()
        trup = "\n".join(linii[1:])
        # Nicio urcare de nivel aici. Partile venite din fisiere trec prin `citeste_md`, care
        # le coboara cu un nivel; cele din master NU trec pe acolo, iar in master subsectiunile
        # sunt deja la nivelul al doilea — adica exact acolo unde trebuie sa ajunga. Urcarea le
        # scotea la nivelul intai, unde cuprinsul (care citeste h2) nu le mai vedea deloc.
        sectiuni.append({"titlu": titlu, "corp": trup})

    cerute = fm.get("parti") or []
    parti, folosite = [], set()
    for i, c in enumerate(cerute):
        if c.get("fisier"):
            p = dict(c)
            p["cale"] = os.path.join(baza, c["fisier"])
            if not os.path.exists(p["cale"]):
                raise SystemExit("lipseste fisierul: " + p["cale"])
            parti.append(p)
            continue
        nume = c.get("sectiune")
        gasita = next((s for s in sectiuni if s["titlu"] == nume), None)
        if gasita is None:
            raise SystemExit("in master nu exista sectiunea de nivel 1: " + str(nume))
        folosite.add(nume)
        parti.append(dict(gasita, eticheta=c.get("eticheta", ""), scurt=c.get("scurt") or nume))
    for s in sectiuni:
        if s["titlu"] not in folosite:
            parti.append(dict(s, eticheta="", scurt=s["titlu"]))

    # Etichetele lipsa se numeroteaza dupa POZITIA in lista, nu dupa cate au fost completate:
    # altfel o eticheta scrisa de mana („Partea I") si una dedusa ajung acelasi numar, si ies
    # doua file cu acelasi nume.
    for i, p in enumerate(parti):
        if not p.get("eticheta"):
            p["eticheta"] = "Partea " + (ROMANE[i] if i < len(ROMANE) else str(i + 1))

    config = {k: fm[k] for k in ("titlu", "legenda", "nota", "marcaje") if k in fm}
    config.setdefault("titlu", fm.get("title", ""))
    for p in parti:
        p.setdefault("_master", os.path.abspath(cale))
    config["parti"] = parti
    return config


def citeste_md(cale):
    """(titlu, subtitlu, corp) — corpul fara front-matter, cu titlurile coborate un nivel.

    Coborarea e ce permite alipirea: fiecare document isi are `#` al lui, iar in pagina toate
    devin `##`, sub titlul partii."""
    s = io.open(cale, encoding="utf-8").read()
    titlu = subtitlu = ""
    if s.startswith("---"):
        _, fm, s = s.split("---", 2)
        m = re.search(r'^title:\s*"(.*)"', fm, re.M)
        titlu = m.group(1) if m else ""
        m = re.search(r'^subtitle:\s*"(.*)"', fm, re.M)
        subtitlu = m.group(1) if m else ""
    corp = re.sub(r"^(#{1,5})\s", r"#\1 ", s, flags=re.M)
    return titlu, subtitlu, corp


def verifica_sursele(parti):
    """Caracterele de control se cauta in fisierele SURSA, inainte de a le lipi.

    `build_manuscris.py` face si el verificarea, dar pe markdown-ul combinat — iar acela e un
    fisier temporar, cu alta numerotare a liniilor. Mesajul lui ar trimite omul sa caute intr-un
    fisier pe care nu-l poate deschide si nu-l poate repara. Aici se numeste fisierul lui, cu
    linia lui."""
    sys.path.insert(0, AICI)
    from build_manuscris import verifica_fara_control  # noqa: PLC0415

    vazute = set()
    for p in parti:
        cale = p.get("cale") or p.get("_master")
        if cale and cale not in vazute:
            vazute.add(cale)
            verifica_fara_control(io.open(cale, encoding="utf-8").read(), cale)


def combina(parti):
    """Un singur markdown, cu antet de parte inaintea fiecarui document."""
    bloc = []
    for i, p in enumerate(parti):
        if p.get("corp") is not None:          # parte venita din master, nu de pe disc
            titlu, subtitlu, corp = p.get("titlu", ""), p.get("subtitlu", ""), p["corp"]
        else:
            titlu, subtitlu, corp = citeste_md(p["cale"])
        titlu = p.get("titlu") or titlu or p.get("scurt") or ""
        bloc.append(f'\n\n# {titlu} {{#parte-{i + 1} .parte '
                    f'data-eticheta="{html.escape(p.get("eticheta", ""))}" '
                    f'data-subtitlu="{html.escape(subtitlu)}"}}\n\n{corp}')
    return "\n".join(bloc)


def randeaza(md_text):
    """Corpul, cu citarile rezolvate. Cere Zotero deschis, ca orice randare cu citari."""
    # Fișierele de lucru stau pe D:, nu în Temp de pe C: (decizia autorului, 12.10.2026; `zot.LUCRU`).
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import zot  # noqa: PLC0415
    os.makedirs(os.path.join(zot.LUCRU, "tmp"), exist_ok=True)
    d = tempfile.mkdtemp(prefix="pagina-html-", dir=os.path.join(zot.LUCRU, "tmp"))
    md = os.path.join(d, "combinat.md")
    io.open(md, "w", encoding="utf-8").write(md_text)
    out = os.path.join(d, "combinat.html")
    r = subprocess.run([sys.executable, BUILD, md, "--out", out, "--to", "html"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    sys.stdout.write(r.stdout or "")
    if r.returncode != 0:
        sys.stderr.write(r.stderr or "")
        # Cauza se repeta pe ULTIMA linie, lipita de esec. `build_manuscris` o tipareste la
        # inceput, iar intre ea si esec incape tot progresul — deci cine citeste rularea cu
        # `tail` vede „randarea a cazut" si nimic altceva. Cauza exista si e buna; problema e
        # unde ajunge.
        motiv = ""
        for linie in reversed((r.stdout or "").splitlines()):
            if linie.strip().startswith(("Nu pot", "ABORT", "EROARE", "eroare")):
                motiv = linie.strip()
                break
        raise SystemExit("randarea a cazut" + (": " + motiv if motiv else ""))
    s = io.open(out, encoding="utf-8").read()
    m = re.search(r"<body[^>]*>(.*)</body>", s, re.S)
    corp = m.group(1) if m else s
    return re.sub(r'<header id="title-block-header">.*?</header>', "", corp, flags=re.S)


def antet_de_parte(m):
    attrs, text = m.group(1), m.group(2)
    et = re.search(r'data-eticheta="([^"]*)"', attrs)
    sub = re.search(r'data-subtitlu="([^"]*)"', attrs)
    idm = re.search(r'id="([^"]*)"', attrs)
    return (f'<header class="parte" id="{idm.group(1) if idm else ""}">'
            + (f'<p class="eticheta">{et.group(1)}</p>' if et and et.group(1) else "")
            + f'<h1>{text}</h1>'
            + (f'<p class="subtitlu">{sub.group(1)}</p>' if sub and sub.group(1) else "")
            + "</header>")


# Clasa se cauta ca CUVANT in atributul `class`, nu ca sir intreg. Pandoc nu garanteaza nici
# ordinea claselor, nici ca ale noastre raman singure — iar cazul nu e marginal, e chiar cel
# obisnuit: citeproc marcheaza `unnumbered` antetul sectiunii in care varsa bibliografia, deci
# ultima parte a oricarui document cu citari iese `class="unnumbered parte"`. Cu potrivire
# literala, partea aia nu mai e recunoscuta si construirea cade numarand o parte in minus.
_ANTET_PARTE = re.compile(r'<h1([^>]*\bclass="[^"]*\bparte\b[^"]*"[^>]*)>(.*?)</h1>', re.S)


def in_file(corp):
    """Taie corpul la antetele de parte: [(id, antet_html, continut_html)]."""
    corp = _ANTET_PARTE.sub(antet_de_parte, corp)
    file = []
    for b in re.split(r'(?=<header class="parte")', corp):
        m = re.match(r'<header class="parte" id="([^"]+)">.*?</header>', b, re.S)
        if m:
            file.append((m.group(1), m.group(0), b[m.end():]))
    return file


def cuprins(continut):
    out = []
    for m in re.finditer(r'<h2 id="([^"]+)"[^>]*>(.*?)</h2>', continut, re.S):
        text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        out.append(f'<li><a href="#{m.group(1)}">{text}</a></li>')
    return "\n".join(out)


SABLON = """<title>__TITLU__</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {
  --fundal: #F6F7F5; --suprafata: #FFFFFF; --cerneala: #1C232B; --mut: #5B6670;
  --linie: #D9DEDC; --accent: #1F5F7A; --accent-moale: #E3EEF3;
  --serif: "Newsreader", "Georgia", "Times New Roman", serif;
  --sans: "IBM Plex Sans", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  --mono: "IBM Plex Mono", "Consolas", "Menlo", monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --fundal: #121719; --suprafata: #1A2125; --cerneala: #E4E9EA; --mut: #98A4AB;
    --linie: #2B353B; --accent: #7CBBD3; --accent-moale: #1E2F38;
  }
}
:root[data-theme="dark"] {
  --fundal: #121719; --suprafata: #1A2125; --cerneala: #E4E9EA; --mut: #98A4AB;
  --linie: #2B353B; --accent: #7CBBD3; --accent-moale: #1E2F38;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--fundal); color: var(--cerneala); font-family: var(--sans);
       font-size: 17px; line-height: 1.6; -webkit-font-smoothing: antialiased; }
a { color: var(--accent); }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (prefers-reduced-motion: no-preference) { html { scroll-behavior: smooth; } }

/* bara de file */
.file[hidden] { display: none; }
.file { position: sticky; top: 0; z-index: 2; background: var(--fundal); border-bottom: 1px solid var(--linie); }
.file-int { max-width: 1180px; margin: 0 auto; padding: 0 1.25rem; display: flex; gap: 0.25rem; align-items: stretch; overflow-x: auto; }
.file button { appearance: none; background: none; border: 0; border-bottom: 3px solid transparent; cursor: pointer;
               color: var(--mut); font-family: var(--sans); font-size: 0.92rem; font-weight: 500; padding: 0.95rem 0.9rem 0.75rem; white-space: nowrap; }
.file button .et { font-family: var(--mono); font-size: 0.68rem; letter-spacing: 0.08em; text-transform: uppercase; margin-right: 0.55rem; opacity: 0.8; }
.file button:hover { color: var(--cerneala); }
.file button[aria-selected="true"] { color: var(--cerneala); border-bottom-color: var(--accent); }
.file .titlu-doc { margin-left: auto; align-self: center; font-family: var(--serif); font-size: 0.95rem; color: var(--mut); white-space: nowrap; padding-left: 1rem; }

/* corpul: cuprins pe margine + text */
.pagina { display: grid; grid-template-columns: minmax(0, 1fr); max-width: 1180px; margin: 0 auto; padding: 0 1.25rem 5rem; }
@media (min-width: 1080px) { .pagina { grid-template-columns: 250px minmax(0, 1fr); gap: 3.5rem; } }
nav.cuprins { display: none; }
@media (min-width: 1080px) {
  nav.cuprins { display: block; position: sticky; top: 3.6rem; align-self: start; max-height: calc(100vh - 3.6rem); overflow-y: auto;
                padding: 2.5rem 0 2rem; font-size: 0.84rem; line-height: 1.45; }
}
nav.cuprins ol { list-style: none; margin: 0; padding: 0; }
nav.cuprins ol[hidden] { display: none; }
nav.cuprins li { padding-left: 0.75rem; border-left: 2px solid var(--linie); margin-top: 0.35rem; }
nav.cuprins a { color: var(--mut); text-decoration: none; }
nav.cuprins a:hover, nav.cuprins a:focus-visible { color: var(--accent); outline: none; }
nav.cuprins .titlu-nav { font-family: var(--mono); font-size: 0.7rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--mut); margin: 0 0 0.75rem; }
main { max-width: none; padding-top: 2.5rem; }
/* măsura de lectură stă pe text; tabelele largi primesc toată coloana.
   Conținutul e în section.fila, nu direct în main. */
.fila > * { max-width: 72ch; }
.fila > .tabel { max-width: 100%; }
section.fila[hidden] { display: none; }

header.parte { margin: 0 0 2rem; padding: 0 0 1.25rem; border-bottom: 1px solid var(--linie); }
header.parte .eticheta { font-family: var(--mono); font-size: 0.75rem; letter-spacing: 0.1em; text-transform: uppercase; color: var(--accent); margin: 0 0 0.5rem; }
header.parte h1 { font-family: var(--serif); font-weight: 600; font-size: 2.15rem; line-height: 1.12; margin: 0; text-wrap: balance; }
header.parte .subtitlu { font-family: var(--serif); font-size: 1.1rem; color: var(--mut); margin: 0.6rem 0 0; line-height: 1.35; text-wrap: balance; }
h2 { font-family: var(--serif); font-weight: 600; font-size: 1.5rem; line-height: 1.2; margin: 3rem 0 0.9rem; text-wrap: balance; scroll-margin-top: 4.5rem; }
h3 { font-family: var(--serif); font-weight: 500; font-size: 1.18rem; margin: 2rem 0 0.6rem; text-wrap: balance; scroll-margin-top: 4.5rem; }
h4 { font-family: var(--sans); font-weight: 600; font-size: 1rem; margin: 1.5rem 0 0.4rem; }
p { margin: 0 0 1.05rem; }
strong { font-weight: 600; }
ul, ol { padding-left: 1.3rem; margin: 0 0 1.05rem; }
li { margin: 0.25rem 0; }
code { font-family: var(--mono); font-size: 0.86em; background: var(--accent-moale); padding: 0.08em 0.3em; border-radius: 3px; }
.tabel { overflow-x: auto; margin: 1.2rem 0 1.6rem; border: 1px solid var(--linie); border-radius: 4px; background: var(--suprafata); }
table { border-collapse: collapse; width: max-content; min-width: 100%; font-size: 0.86rem; line-height: 1.35; }
th, td { padding: 0.5rem 0.7rem; text-align: left; vertical-align: top; border-bottom: 1px solid var(--linie); }
th { font-family: var(--sans); font-weight: 600; font-size: 0.8rem; letter-spacing: 0.02em; color: var(--mut); background: var(--fundal); }
tr:last-child td { border-bottom: none; }
td[style*="right"], th[style*="right"] { font-family: var(--mono); font-variant-numeric: tabular-nums;
  white-space: nowrap; }
/* prima coloană e eticheta rândului: nu se rupe, ca tabelul să rămână citibil pe orizontală */
tbody td:first-child, thead th:first-child { white-space: nowrap; }
table code { white-space: nowrap; }
blockquote { margin: 1.2rem 0; padding: 0.1rem 0 0.1rem 1rem; border-left: 3px solid var(--accent); color: var(--mut); }
#refs, .references { margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--linie); font-size: 0.92rem; }
.csl-entry { margin: 0 0 0.7rem; padding-left: 1.5rem; text-indent: -1.5rem; }
hr { border: none; border-top: 1px solid var(--linie); margin: 2.5rem 0; }
.nota-generata { font-family: var(--mono); font-size: 0.74rem; color: var(--mut); margin-top: 4rem; padding-top: 1rem; border-top: 1px solid var(--linie); }
.nefinalizat, .nefinalizat p, .nefinalizat li, .nefinalizat strong { color: var(--mut); font-style: italic; }
div.nefinalizat { border-left: 3px solid var(--linie); padding-left: 1rem; margin: 1.2rem 0; }
.legenda { font-family: var(--sans); font-size: 0.82rem; color: var(--mut); margin: 0 0 2rem; padding: 0.5rem 0.8rem; border: 1px solid var(--linie); border-radius: 4px; background: var(--suprafata); }
.legenda .nefinalizat { font-style: italic; }
/* marcajele de stare se vad, nu se citesc */
.marcaj { font-family: var(--sans); font-size: 0.72rem; font-weight: 600; letter-spacing: 0.04em;
  text-transform: uppercase; padding: 0.1em 0.45em; border-radius: 3px; white-space: nowrap;
  background: var(--accent-moale); color: var(--accent); font-style: normal; }
</style>
<div class="file" role="tablist" aria-label="Părțile documentului" hidden>
<div class="file-int">
__FILE__
<span class="titlu-doc">__TITLU__</span>
</div>
</div>
<div class="pagina">
<nav class="cuprins" aria-label="Cuprins">
<p class="titlu-nav">Cuprins</p>
__CUPRINS__
</nav>
<main>
__LEGENDA__
__CORP__
__NOTA__
</main>
</div>
<script>
(function () {
  var file = Array.prototype.slice.call(document.querySelectorAll('section.fila'));
  var butoane = Array.prototype.slice.call(document.querySelectorAll('.file button'));
  var cuprinsuri = Array.prototype.slice.call(document.querySelectorAll('nav.cuprins ol'));
  // Adresa se scrie INTREAGA, niciodata ca fragment singur. `history.replaceState` rezolva si
  // el argumentul fata de <base>, exact ca un href — deci `replaceState(null,'','#x')` sub o
  // gazda cu <base href="/_f/hash/"> muta adresa la /_f/hash/#x, fara sa reincarce nimic.
  // Pagina pare in regula pana cand cineva reincarca sau da linkul mai departe.
  function scrieHash(id) {
    if (!history.replaceState) return;
    history.replaceState(null, '', location.pathname + location.search + '#' + id);
  }
  function arata(id, pastreazaHash) {
    file.forEach(function (s) { s.hidden = s.id !== id; });
    butoane.forEach(function (b) { b.setAttribute('aria-selected', b.dataset.fila === id ? 'true' : 'false'); });
    cuprinsuri.forEach(function (o) { o.hidden = o.dataset.fila !== id; });
    if (!pastreazaHash) scrieHash(id);
  }
  function filaPentru(hash) {
    if (!hash) return null;
    var el = document.getElementById(hash.replace(/^#/, ''));
    if (!el) return null;
    var s = el.closest ? el.closest('section.fila') : null;
    return s ? s.id : null;
  }
  butoane.forEach(function (b) {
    b.addEventListener('click', function () { arata(b.dataset.fila, false); window.scrollTo({ top: 0 }); });
  });
  // Cuprinsul isi face singur saritura, in loc s-o lase pe seama navigarii. Motivul nu se vede
  // local: gazda care publica pagina injecteaza in <head> un <base href="/_f/<hash>/">, iar o
  // legatura scrisa numai ca fragment se rezolva FATA DE BASE, nu fata de documentul curent.
  // Clicul pleaca deci la radacina invelisului, pagina se reincarca, `location.hash` e gol si
  // se cade pe prima fila — de unde impresia ca orice clic te trimite acasa. Masurat: `#ancora`
  // se rezolva la `<base>#ancora`.
  document.querySelectorAll('nav.cuprins a[href^="#"]').forEach(function (a) {
    a.addEventListener('click', function (ev) {
      var id = a.getAttribute('href').slice(1);
      var tinta = document.getElementById(id);
      if (!tinta) return;                 // ancora lipsa: lasa navigarea sa se descurce
      ev.preventDefault();
      var s = tinta.closest ? tinta.closest('section.fila') : null;
      if (s) arata(s.id, true);           // la documentul dintr-o bucata nu e nimic de comutat
      tinta.scrollIntoView();
      scrieHash(id);
    });
  });
  function dinHash() {
    var id = filaPentru(location.hash) || file[0].id;
    arata(id, true);
    if (location.hash && location.hash !== '#' + id) {
      var tinta = document.getElementById(location.hash.slice(1));
      if (tinta) tinta.scrollIntoView();
    }
  }
  window.addEventListener('hashchange', dinHash);
  // bara de file apare abia acum: fără JavaScript, cele trei părți rămân vizibile una sub alta
  var bara = document.querySelector('.file');
  if (bara) bara.hidden = false;
  dinHash();
})();
</script>
"""


def _marcaje(corp, marcaje):
    """Cuvintele de stare scrise in italic devin etichete colorate.

    Intr-o harta cu douazeci si patru de randuri, `*open*` scris in italic printre alte italice
    nu se vede — trebuie citit. Ca eticheta, se vede din capul paginii care randuri sunt inca
    deschise. Marcajele se dau in configuratie, fiindca vocabularul e al proiectului:

        "marcaje": ["open", "own", "own specification"]

    Se inlocuiesc de la cel mai lung la cel mai scurt: altfel `own` ar taia din
    `own specification` si ar ramane o eticheta plus un cuvant orfan."""
    for m in sorted(marcaje, key=len, reverse=True):
        # Spatiul dintre cuvinte se scrie ca `\\s+`, nu literal: pandoc rupe randul unde ii
        # vine, inclusiv IN INTERIORUL marcajului — masurat, un marcaj din doua cuvinte a
        # iesit cu linie noua intre ele. O potrivire literala trece pe langa exact marcajele
        # lungi, adica pe langa cele mai informative.
        tipar = re.compile(r"<em>" + r"\s+".join(re.escape(c) for c in m.split()) + r"</em>")
        clasa = re.sub(r"[^a-z0-9]+", "-", m.lower())
        corp = tipar.sub('<span class="marcaj marcaj-%s">%s</span>' % (clasa, m), corp)
    return corp


def _tabele(corp):
    """Fiecare tabel intr-un container cu derulare orizontala.

    `<colgroup>`-ul cu procente il scoate `build_manuscris.py` la randare; aici doar se
    verifica, fiindca o pagina construita cu un pandoc chemat altfel l-ar putea avea inca."""
    corp = re.sub(r"<colgroup>.*?</colgroup>", "", corp, flags=re.S)
    corp = re.sub(r"<table[^>]*>", '<div class="tabel"><table>', corp)
    return re.sub(r"</table>", "</table></div>", corp)


def construieste(config, out):
    parti = config["parti"]
    verifica_sursele(parti)
    corp = _tabele(randeaza(combina(parti)))
    if config.get("marcaje"):
        corp = _marcaje(corp, config["marcaje"])

    file = in_file(corp)
    if len(file) != len(parti):
        raise SystemExit("astept %d parti, am gasit %d — verifica antetele de nivel 1"
                         % (len(parti), len(file)))

    butoane, nav, sectiuni = [], [], []
    for p, (idx, antet, continut) in zip(parti, file):
        scurt = p.get("scurt") or p.get("eticheta") or idx
        et = (p.get("eticheta") or "").replace("Partea ", "")
        butoane.append(f'<button type="button" role="tab" data-fila="{idx}" aria-selected="false">'
                       + (f'<span class="et">{html.escape(et)}</span>' if et else "")
                       + f'{html.escape(scurt)}</button>')
        nav.append(f'<ol data-fila="{idx}">\n{cuprins(continut)}\n</ol>')
        sectiuni.append(f'<section class="fila" id="{idx}">\n{antet}\n{continut}\n</section>')

    legenda = config.get("legenda")
    nota = config.get("nota")
    pagina = (SABLON
              .replace("__TITLU__", html.escape(config.get("titlu", "")))
              .replace("__FILE__", "\n".join(butoane) if len(parti) > 1 else "")
              .replace("__CUPRINS__", "\n".join(nav))
              .replace("__CORP__", "\n".join(sectiuni))
              .replace("__LEGENDA__", f'<p class="legenda">{legenda}</p>' if legenda else "")
              .replace("__NOTA__", f'<p class="nota-generata">{nota}</p>' if nota else ""))
    # Bara de file n-are ce cauta la un document dintr-o bucata: n-ai intre ce sa comuti.
    if len(parti) < 2:
        pagina = re.sub(r'<div class="file" role="tablist".*?</div>\s*</div>', "", pagina, flags=re.S)

    io.open(out, "w", encoding="utf-8", newline="").write(pagina)
    print("pagina scrisa: %s (%s caractere, %d %s)"
          % (out, format(len(pagina), ","), len(parti),
             "parti" if len(parti) > 1 else "parte"))
    return out


def main(argv):
    argv = list(argv)
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        del argv[i:i + 2]

    if "--pe-parti" in argv:
        argv = [a for a in argv if a != "--pe-parti"]
        if not argv or not argv[0].endswith(".md"):
            raise SystemExit("--pe-parti cere un master .md")
        cale = os.path.abspath(argv[0])
        config = parti_din_master(cale)
        implicit = os.path.splitext(cale)[0] + ".html"
    elif "--config" in argv:
        i = argv.index("--config")
        config = citeste_config(argv[i + 1])
        implicit = os.path.splitext(argv[i + 1])[0] + ".html"
    elif argv and argv[0].endswith(".md"):
        cale = os.path.abspath(argv[0])
        titlu, _sub, _corp = citeste_md(cale)
        config = {"titlu": titlu, "parti": [{"fisier": os.path.basename(cale), "cale": cale,
                                             "titlu": titlu, "eticheta": "", "scurt": titlu}]}
        implicit = os.path.splitext(cale)[0] + ".html"
    else:
        print(__doc__.split("## Folosire")[1].split("##")[0].strip())
        return 2

    try:
        construieste(config, out or implicit)
    except ValueError as e:
        # Caracterul de control e o eroare in text, nu in program: se spune ce si unde, fara
        # urma de stiva, fiindca cine citeste are de reparat un fisier.
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
