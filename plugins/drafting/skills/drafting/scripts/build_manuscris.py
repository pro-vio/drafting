#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_manuscris.py — render a manuscript with LIVE citations from Zotero.

Pipeline:
  manuscript .md with [@citekey]
    -> pull CSL-JSON for ONLY the cited keys from Zotero
       (local web API, /items?format=csljson — id == the Better BibTeX citekey,
        also reads pinned "Citation Key:" / "Original Date:" from the Extra field)
    -> pandoc --citeproc --csl <style>
    -> HTML (or any pandoc output). The "References" list is generated automatically,
       so it contains exactly what is cited — never stale, never extra.

Source of truth = Zotero (must be OPEN; API on port 23119). NOT a hand-made bib file.
Update workflow: change something in Zotero -> re-run this script. That's it.

Why the bulk csljson endpoint (not Better BibTeX item.export / item.search):
  - item.export is all-or-nothing and serves a STALE cache after direct DB writes.
  - item.search is flaky on multi-word terms.
  The standard API reads the live DB and returns id == citekey, so a dict lookup resolves keys.

Usage:
  python build_manuscris.py <manuscript.md> [--csl <style>] [--out <file>]
                            [--user-id N] [--to <pandoc-format>] [--check]
                            [--collection <nume|cheie>] [--pandoc-args "..."] [--wrap <mod>]
  --csl     style name from ~/Zotero/styles (apa, ieee, ...) or a .csl path.
            Default = the chicago-author-date.csl bundled with this skill.
  --out     output path. Default = <md_dir>/<md_stem>.html
  --to      pandoc output format (html, docx, ...). Inferred from --out extension.
  --user-id Zotero library user id for the API URL. Default = env ZOTERO_USER_ID
            or auto-detect from zotero.sqlite (see zot.detect_user_id).
  --collection  restrânge interogarea la o colecție (nume sau cheie de 8 caractere).
            Interogare mult mai mică — și colecția devine, verificabil, lista surselor pe
            care stă manuscrisul. Cheile nerezolvate acolo se mai caută o dată în toată
            biblioteca, deci colecția îngustează, nu decide ce e valid.
  --pandoc-args  șir trecut mai departe la pandoc, pentru dialecte și opțiuni care nu încap
            în --to (ex. "-raw_attribute-smart-simple_tables").
  --wrap    --wrap al lui pandoc (none, auto, preserve).
  --allow-empty-bib  randează chiar dacă NICIO cheie nu s-a rezolvat. Implicit asta e
            eroare, nu avertisment: un document fără citări și cu bibliografia goală nu e
            rezultat parțial.
  --check   FAST resolution check: extract keys, hit Zotero, print which resolve and
            which are missing, then exit — NO pandoc render, NO file written. Use this
            in the edit loop ("did my new key resolve?"); skips the slow render entirely.
            Exit code 0 if every cited key resolves, 1 if any is missing.
"""
import re, sys, os, io, json, subprocess, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import zot  # sibling helper

# Windows consoles default to cp1252; our status prints carry diacritics/emoji and would
# crash AFTER the output file is already written. Force UTF-8 stdout so runs don't appear to fail.
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

SKILL = os.path.dirname(HERE)
BUNDLED_CSL = os.path.join(SKILL, "references", "chicago-author-date.csl")
ZOTERO_STYLES = os.path.expanduser("~/Zotero/styles")

# pandoc citation keys: @key / [@key, loc] / [-@key] / [@k1; @k2]; keys may be unicode
# Pandoc are DOUĂ forme de cheie: simplă (`@Cheie`) și între acolade (`@{Cheie}`).
# Acolada nu e decorativă: Better BibTeX generează chei cu apostrof tipografic
# (`D’Orazio2019`, U+2019), iar în forma simplă pandoc rupe citarea exact acolo. Deci
# tocmai cheile care AU nevoie de acolade erau cele pe care extractorul le rata — lua cheia
# cu tot cu acolade și căuta în Zotero `@{Cheie}`, care evident nu există nicăieri.
CITE_RE = re.compile(r'(?<![\w@])-?@(?:\{(?P<acolade>[^}]+)\}|(?P<simpla>[^\s\]\[;,.{}]+))')

def parse_args(argv):
    a = {"md": None, "csl": None, "out": None, "user_id": None, "to": None, "check": False,
         "collection": None, "pandoc_args": None, "wrap": None, "allow_empty_bib": False, "exceptii": set()}
    i = 0
    while i < len(argv):
        t = argv[i]
        if t == "--csl": a["csl"] = argv[i+1]; i += 2
        elif t == "--out": a["out"] = argv[i+1]; i += 2
        elif t == "--user-id": a["user_id"] = argv[i+1]; i += 2
        elif t == "--to": a["to"] = argv[i+1]; i += 2
        elif t == "--collection": a["collection"] = argv[i+1]; i += 2
        elif t == "--pandoc-args": a["pandoc_args"] = argv[i+1]; i += 2
        elif t == "--wrap": a["wrap"] = argv[i+1]; i += 2
        elif t == "--allow-empty-bib": a["allow_empty_bib"] = True; i += 1
        elif t == "--exceptie": a["exceptii"].add(argv[i+1]); i += 2
        elif t == "--check": a["check"] = True; i += 1
        elif a["md"] is None: a["md"] = t; i += 1
        else: i += 1
    if not a["md"]:
        sys.exit("Utilizare: build_manuscris.py <manuscript.md> [--csl ..] [--out ..] [--user-id N]")
    return a

def resolve_csl(arg):
    if not arg: return BUNDLED_CSL
    if os.path.isfile(arg): return arg
    name = arg if arg.endswith(".csl") else arg + ".csl"
    for base in (ZOTERO_STYLES, os.path.join(SKILL, "references")):
        p = os.path.join(base, name)
        if os.path.isfile(p): return p
    sys.exit(f"CSL negăsit: {arg!r} (nici cale, nici în ~/Zotero/styles, nici bundled).")

def extract_keys(text):
    # ignore hand-made bibliography sections so we don't harvest junk keys
    body = text.split("\n# References")[0].split("\n# Legislation")[0]
    keys = []
    for m in CITE_RE.finditer(body):
        # forma cu acolade delimitează exact, deci nu se mai taie punctul de la capăt;
        # la cea simplă, punctul de final de frază intră în potrivire și se scoate.
        k = m.group("acolade") or m.group("simpla").rstrip(".")
        if k and k not in keys: keys.append(k)
    return keys

def fetch_csljson(api, collection=None, limit=100):
    """({citekey: intrare}, câte iteme s-au citit). Paginează până se termină.

    Vechea versiune trăgea `?limit=2000` o singură dată. Două defecte, amândouă tăcute: pe o
    bibliotecă mai mare de 2000 de iteme, ce cădea după prag era raportat drept „cheie
    negăsită" — la fel ca o cheie chiar greșită; iar interogarea pe toată biblioteca expira
    („timed out") ori de câte ori Zotero era ocupat cu altceva.

    `collection` (nume sau cheie) îngustează la o colecție: interogare de zeci de ori mai
    mică, și un al doilea sens — colecția devine, verificabil, lista surselor pe care stă
    manuscrisul."""
    baza = api
    if collection:
        cheie = collection
        if len(collection) != 8 or not collection.isalnum() or not collection.isupper():
            gasita = zot.find_collection(collection)
            if not gasita:
                sys.exit(f"Colecție negăsită: {collection!r}")
            cheie = gasita[0]
        baza = api.rsplit("/items", 1)[0] + f"/collections/{cheie}/items"

    by, citite, start = {}, 0, 0
    while True:
        url = f"{baza}?format=csljson&itemType=-attachment&limit={limit}&start={start}"
        pagina = json.loads(urllib.request.urlopen(url, timeout=60).read().decode())
        if not pagina:
            break
        for r in pagina:
            if r.get("id"):
                by[r["id"]] = r
        citite += len(pagina)
        if len(pagina) < limit:
            break
        start += limit
    return by, citite


# Caracterele de control C0 (U+0000-U+001F) minus cele trei care au ce cauta in text:
# tab, linie noua, retur de car. Restul n-au nicio forma vizibila si niciun rost intr-un
# manuscris — daca sunt acolo, au intrat dintr-o greseala de editare.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _arata_locul(text, poz, raza=40):
    """Contextul din jurul unui caracter invizibil, cu el insusi facut vizibil."""
    inceput = max(0, poz - raza)
    bucata = text[inceput:poz + raza]
    vizibil = _CONTROL.sub(lambda m: "<U+%04X>" % ord(m.group()), bucata)
    linie = text.count("\n", 0, poz) + 1
    return linie, vizibil.replace("\n", "\\n")


def verifica_fara_control(text, ce):
    """Ridica exceptie daca textul contine caractere de control C0.

    De ce merita o verificare proprie, cand pare o grija exagerata: masurat, 23.08.2026, un
    `U+0001` strecurat printr-o editare cu heredoc a ajuns **in interiorul etichetei de tabel**,
    iar pagina a iesit cu `<table` + U+0001 + `>`. Browserul nu recunoaste asta ca tabel — il
    trateaza ca element necunoscut, iar celulele curg una dupa alta ca text simplu.

    Ce face capcana costisitoare e ca **orice verificare prin cautare de text trece**: `grep
    "<table"` gaseste toate tabelele, fiindca sirul cautat chiar e acolo. Cauza s-a vazut abia
    la inspectia octet cu octet, dupa patru runde in care autorul a spus ca nu poate citi
    tabelele si i s-a raspuns ca pagina e corecta.

    Deci se verifica la intrare, pe markdown, si la iesire, pe ce s-ar scrie pe disc: la intrare
    ca sa se arate unde e greseala, la iesire fiindca pandoc nu curata nimic — trece caracterul
    mai departe, inclusiv in mijlocul unei etichete pe care el a generat-o."""
    m = _CONTROL.search(text)
    if not m:
        return
    linie, context = _arata_locul(text, m.start())
    raise ValueError(
        "%s contine un caracter de control (U+%04X), linia %d. Nu se poate scrie asa: "
        "e invizibil, trece de orice cautare de text si strica eticheta in care nimereste.\n"
        "  ...%s..." % (ce, ord(m.group()), linie, context))


_AUTOLINK = re.compile(r"&lt;(https?://[^&\s<>]+)&gt;")


def linkuri_din_autolink(html_text):
    """Autolinkurile lui citeproc, transformate in ancore.

    Citeproc scrie DOI-ul si adresele ca autolink markdown — `<https://doi.org/...>`. Cine
    randeaza direct in HTML nu vede nimic: pandoc le face ancore. Dar cine ia **markdownul cu
    citarile rezolvate** si isi construieste singur pagina primeste sirul ca atare, il scapa la
    afisare si ajunge cu `&lt;https://doi.org/...&gt;` in bibliografie — text, nu legatura.

    E de chemat pe HTML-ul deja scapat, adica dupa `html.escape`, nu inainte:

        rand = linkuri_din_autolink(html.escape(text_din_markdown))
    """
    return _AUTOLINK.sub(lambda m: '<a href="%s">%s</a>' % (m.group(1), m.group(1)), html_text)


_COLGROUP = re.compile(r"<colgroup>.*?</colgroup>", re.S)


def _fara_colgroup(html):
    """Scoate `<colgroup>` din iesirea HTML a lui pandoc.

    Pandoc calculeaza latimi in procente si le scrie ca `<colgroup>`. Intr-un tabel lat pus
    intr-un container cu `overflow-x: auto`, procentele se bat cap in cap cu latimea tabelului:
    fiecare derulare cere o reasezare, iar pagina ingheata — masurat in Chrome, 23.08.2026, pe
    un tabel de douasprezece coloane.

    Fara ele, tabelul isi ia latimea din continut. In foaia de stil, perechea care merge cu asta
    e `width: max-content; min-width: 100%` pe `table`, plus containerul cu derulare orizontala.
    `pagina_html.py` o are deja."""
    return _COLGROUP.sub("", html)


LUNI_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]
LUNI_RO = ["ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie", "august", "septembrie", "octombrie",
           "noiembrie", "decembrie"]


def versiune_server(refs, csl):
    """Sursele online fără dată afișată se citează cu „n.d." și cu data accesării; când serverul dă data ultimei
    modificări a fișierului, ea se adaugă după data accesării, ca „versiunea de pe server din …", fără să ia locul
    datei de publicare (regula autorului, 13.10.2026). Data serverului stă în Extra, pe rândul „Versiunea de pe
    server: AAAA-LL-ZZ …"; stilul nu știe de ea, deci data accesării se dă la citeproc ca text compus (`literal`),
    pe care îl afișează în locul ei. Stilul rămâne neatins."""
    try:
        ro = 'default-locale="ro' in open(csl, encoding="utf-8").read(4000)
    except OSError:
        ro = False
    for r in refs:
        m = re.search(r"^Versiunea de pe server: (\d{4})-(\d{2})-(\d{2})", r.get("note") or "", re.M)
        acc = ((r.get("accessed") or {}).get("date-parts") or [[None]])[0]
        if not m or len(acc) < 3:
            continue
        sa, sl, sz = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if ro:
            text = "%d %s %d (versiunea de pe server din %d %s %d)" % (acc[2], LUNI_RO[acc[1] - 1], acc[0],
                                                                      sz, LUNI_RO[sl - 1], sa)
        else:
            text = "%s %d, %d (server version of %s %d, %d)" % (LUNI_EN[acc[1] - 1], acc[2], acc[0],
                                                                LUNI_EN[sl - 1], sz, sa)
        r["accessed"] = {"literal": text}


def render(md, out, to, csl, refs, md_dir=None, extra_args=None, wrap=None,
           standalone=True):
    """Randarea, ca funcție importabilă — nu doar ca pas al lui `main`.

    Proiectele care generează un `.md` și au nevoie de citările randate **în markdown**, cu
    dialect controlat, ajungeau să-și scrie propria dublură a conductei. Acum o cheamă:

        from build_manuscris import fetch_csljson, render
        render(md, out, "markdown-raw_attribute-smart-simple_tables", csl, refs,
               wrap="none", standalone=False)

    `extra_args` e o listă sau un șir trecut mai departe la pandoc, ca dialectele și opțiunile
    care nu încap în `--to` să fie totuși accesibile."""
    md_dir = md_dir or os.path.dirname(os.path.abspath(md))
    verifica_fara_control(io.open(md, encoding="utf-8").read(), "fisierul de intrare " + md)
    # standalone=False pentru un FRAGMENT: altfel pandoc pune un antet YAML în ieșire, iar
    # conducta care consumă fișierul mai departe îl primește ca text.
    cmd = ["pandoc", md, "-o", out, "--citeproc"] + (["--standalone"] if standalone else []) + [
           "--csl", csl, "--bibliography", refs,
           f"--resource-path={md_dir}{os.pathsep}{md_dir}/_media",
           "--metadata", "lang=en", "--to", to]
    if to == "html":
        cmd += ["--embed-resources", "--mathjax"]
    if wrap:
        cmd += ["--wrap", wrap]
    if extra_args:
        cmd += extra_args.split() if isinstance(extra_args, str) else list(extra_args)
    env = dict(os.environ, PYTHONUTF8="1")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError("pandoc: " + r.stderr[:1000])

    # Iesirea binara (docx, pdf) nu se verifica: acolo octetii de control sunt structura.
    if to in ("html", "html5", "html4", "markdown", "plain", "latex") or \
            os.path.splitext(out)[1].lower() in (".html", ".htm", ".md", ".txt", ".tex"):
        continut = io.open(out, encoding="utf-8", errors="replace").read()
        try:
            verifica_fara_control(continut, "iesirea " + out)
        except ValueError:
            os.remove(out)   # nu lasam pe disc un fisier despre care tocmai am spus ca e stricat
            raise
        if to.startswith("html") or os.path.splitext(out)[1].lower() in (".html", ".htm"):
            curatat = _fara_colgroup(continut)
            if curatat != continut:
                io.open(out, "w", encoding="utf-8", newline="").write(curatat)
    return out


def main():
    a = parse_args(sys.argv[1:])
    md = a["md"]
    md_dir = os.path.dirname(os.path.abspath(md))
    stem = os.path.splitext(os.path.basename(md))[0]
    out = a["out"] or os.path.join(md_dir, stem + ".html")
    to = a["to"] or os.path.splitext(out)[1].lstrip(".") or "html"
    refs = os.path.join(md_dir, f".{stem}.refs.json")

    uid = a["user_id"] or zot.detect_user_id()
    if not uid:
        sys.exit("user id Zotero nedetectat. Dă --user-id N sau setează ZOTERO_USER_ID.")
    api = f"http://127.0.0.1:23119/api/users/{uid}/items"

    text = open(md, encoding="utf-8").read()
    keys = extract_keys(text)
    print(f"[1] {len(keys)} chei citate în corp.")

    # Zero chei => nu se atinge Zotero deloc. Pare evident scris așa, dar nu era: scriptul
    # număra corect zero, apoi trăgea biblioteca oricum, iar verificarea venea abia după —
    # deci un document fără nicio citare nu se putea randa cu Zotero închis, și nici
    # `--allow-empty-bib` nu ajuta, fiindcă se citea prea târziu. Aceeași formă de greșeală
    # ca la cache: verificarea ieftină trebuie să stea ÎNAINTEA pasului scump, nu după el.
    if not keys:
        print("    (niciun marcaj de citare — nu interoghez Zotero)")
        by, citite, refs_used, missing = {}, 0, [], []
    else:
        unde = f"colecția {a['collection']!r}" if a["collection"] else "toată biblioteca"
        print(f"[2] trag CSL-JSON din Zotero ({unde})...")
        try:
            by, citite = fetch_csljson(api, a["collection"])
        except Exception as e:
            sys.exit(f"Nu pot citi din Zotero (deschis? port 23119?). Eroare: {e}")
        refs_used = [by[k] for k in keys if k in by]
        got = {r["id"] for r in refs_used}
        missing = [k for k in keys if k not in got]

        # Cheile nerezolvate într-o colecție se mai caută o dată în toată biblioteca:
        # colecția îngustează interogarea, nu definește ce e valid.
        if missing and a["collection"]:
            print(f"    {len(missing)} chei nerezolvate în colecție — reîncerc pe toată biblioteca...")
            by_all, citite_all = fetch_csljson(api, None)
            recuperate = [k for k in missing if k in by_all]
            refs_used += [by_all[k] for k in recuperate]
            missing = [k for k in missing if k not in by_all]
            citite += citite_all
            if recuperate:
                print(f"    {len(recuperate)} găsite în afara colecției: " + ", ".join("@" + k for k in recuperate))

    if keys:
        print(f"    {len(keys) - len(missing)}/{len(keys)} rezolvate, din {citite} iteme citite.")
    if missing:
        # Distincția care lipsea: dacă am citit toată biblioteca, „lipsește" chiar înseamnă
        # că nu e acolo. Vechea versiune trăgea 2000 de iteme fără paginare și raporta la fel
        # o cheie greșită și una care cădea după prag.
        print(f"    ⚠️  CHEI NEGĂSITE ÎN ZOTERO (căutate în {citite} iteme):")
        for k in missing: print(f"        @{k}")

    # Metadatele citate trebuie verificate pe text (regula autorului, 12.10.2026): itemul poartă în
    # Extra rândul „Metadate verificate pe text: …", pus de `verifica_metadate.py` (sau de
    # `completeaza_forme.py`, la actele normative). Un item neverificat se citează numai ca excepție
    # aprobată de autor, dată explicit cu `--exceptie CHEIE`.
    neverificate = [r.get("id") for r in refs_used
                    if "Metadate verificate pe text:" not in (r.get("note") or "")
                    and r.get("id") not in a["exceptii"]]
    if neverificate:
        print(f"    ⚠️  {len(neverificate)} item(i) citat(i) fără metadate verificate pe text:")
        for k in neverificate: print(f"        @{k}")
        print("       Verifică-i cu `verifica_metadate.py --chei …` (actele normative: `completeaza_forme.py`);\n"
              "       un item neverificat se citează doar ca excepție aprobată de autor: `--exceptie CHEIE`.")

    if a["check"]:
        # Fast path: no render, no file written — just report and exit.
        print("\n✅ Niciun marcaj de citare în document — nimic de rezolvat." if not keys
              else "\n✅ Toate cheile rezolvă." if not missing and not neverificate
              else f"\n⚠️  {len(missing)} chei lipsesc, {len(neverificate)} neverificate (vezi mai sus).")
        sys.exit(0 if not missing and not neverificate else 1)
    if neverificate:
        print("\n❌ Nu randez cu citări neverificate. Verifică-le sau dă excepțiile aprobate de autor.")
        sys.exit(1)

    # O bibliografie complet goală nu e rezultat parțial, e eșec: randarea ar produce un
    # document fără nicio citare și fără listă de referințe, semnalat doar printr-un
    # avertisment la coadă — ușor de ratat într-o rulare lungă. Cel mai frecvent nu înseamnă
    # „chei greșite", ci o sintaxă neînțeleasă sau o colecție greșită, iar diferența nu se
    # vede din raport.
    if keys and not refs_used and not a["allow_empty_bib"]:
        print("\n❌ NICIO cheie nu s-a rezolvat. Nu randez: ar ieși un document fără citări\n"
              "   și cu bibliografia goală. Verifică sintaxa cheilor și colecția.\n"
              "   Dacă chiar asta vrei, reia cu --allow-empty-bib.")
        sys.exit(1)

    csl = resolve_csl(a["csl"])
    versiune_server(refs_used, csl)
    json.dump(refs_used, open(refs, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[3] pandoc --citeproc (stil: {os.path.basename(csl)}, -> {to}) ...")
    try:
        render(md, out, to, csl, refs, md_dir=md_dir,
               extra_args=a["pandoc_args"], wrap=a["wrap"])
    except RuntimeError as e:
        print("    " + str(e)); sys.exit(1)
    except ValueError as e:
        # Caracterul de control nu e o eroare de program, e o eroare in text: se spune ce si
        # unde, fara urma de stiva, fiindca cine o citeste are de reparat un fisier.
        print("    " + str(e)); sys.exit(1)
    print(f"    OK -> {out}")
    print("\n\u2705 Randat. Documentul nu are marcaje de citare." if not keys
          else "\n\u2705 Toate cit\u0103rile rezolvate." if not missing
          else "\n\u26a0\ufe0f  Render f\u0103cut, dar lipsesc cit\u0103ri (vezi mai sus).")

if __name__ == "__main__":
    # Caracterul de control nu e o eroare de program, e o eroare in text: se spune ce si
    # unde, fara urma de stiva, fiindca cine o citeste are de reparat un fisier.
    main()
