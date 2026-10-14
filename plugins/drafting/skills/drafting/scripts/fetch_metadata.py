# -*- coding: utf-8 -*-
"""fetch_metadata.py — metadate pentru un item care CHIAR lipsește din bibliotecă.

Protocolul acoperă temeinic cazul în care itemul există: caută întâi, nu crea duplicate,
preferă copia cu PDF. Când chiar lipsește, `zot.add_item` cere câmpuri exacte — autori, an,
revistă, volum, pagini, DOI — iar de unde se iau nu era scris nicăieri.

Se iau din Crossref. Dar căutarea după titlu are o capcană măsurată, care nu se deduce:
**întoarce, înaintea lucrării, notița ei de corectură și preprintul.** Într-o singură rulare
pe cincisprezece titluri, două au ieșit greșit — la una, primul rezultat a fost o notiță
„Corrections: <titlul>", cu DOI propriu și o singură pagină; la alta, preprintul SSRN
(`posted-content`, fără revistă, fără pagini) în locul articolului publicat un an mai târziu.
Amândouă ar fi intrat în bibliotecă arătând plauzibil și s-ar fi randat ca surse inexistente.

De aceea scriptul **filtrează** și **nu alege**: întoarce candidați pentru confirmare,
niciodată unul singur, tăcut.

    python fetch_metadata.py "titlul lucrarii"            # candidati, ordonati
    python fetch_metadata.py "titlul" --json              # campurile pentru zot.add_item

Ca funcție:
    from fetch_metadata import cauta, campuri_add_item
    candidati = cauta("Ranking the Romanian Political Science Departments")
"""
import html, io, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

# Crossref cere o adresă de contact ca să te pună în coada politicoasă; fără ea răspunde,
# dar mai încet și fără garanții.
MAILTO = os.environ.get("CROSSREF_MAILTO", "")

# Titlurile care încep așa NU sunt lucrarea, ci nota despre ea.
RE_CORECTURA = re.compile(
    r"^\s*(corrections?|corrigendum|erratum|retraction|addendum|editorial expression of concern)\b[: ]",
    re.I)

# Tipurile care sunt o versiune anterioară a lucrării, nu lucrarea.
TIPURI_PREPRINT = {"posted-content", "preprint"}


def _normalizeaza(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _interoghează(titlu, rows=8, timeout=20):
    q = {"query.bibliographic": titlu, "rows": str(rows), "select":
         "title,author,issued,container-title,volume,issue,page,DOI,type,publisher,subtitle"}
    if MAILTO:
        q["mailto"] = MAILTO
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(q)
    cerere = urllib.request.Request(url, headers={"User-Agent": "drafting-skill (metadata lookup)"})
    # Crossref raspunde `429 Too Many Requests` la interogari in rafala — masurat, a treia
    # cautare la rand. Conteaza fiindca aici cautarile chiar vin in rafala: o colectie cu patru
    # surse fara DOI produce patru interogari intr-o secunda. Fara reincercare, refuzul de ritm
    # arata identic cu „lucrarea nu exista", iar sursa pleaca pe lista de facut de mana degeaba.
    for incercare in range(4):
        try:
            with urllib.request.urlopen(cerere, timeout=timeout) as r:
                return json.loads(r.read().decode())["message"]["items"]
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or incercare == 3:
                raise
            time.sleep(2 ** incercare)      # 1, 2, 4 secunde
    return []


def cauta(titlu, rows=8):
    """Candidați filtrați și ordonați, cei mai plauzibili întâi. Fiecare are `_motive`, care
    spune de ce a fost retrogradat sau scos — ca decizia să se poată verifica, nu doar accepta."""
    bruti = _interoghează(titlu, rows=rows)
    candidati = []
    for it in bruti:
        t = (it.get("title") or [""])[0]
        c = dict(it)
        c["_titlu"] = t
        c["_motive"] = []
        if RE_CORECTURA.match(t):
            c["_motive"].append("notiță de corectură, nu lucrarea")
        if it.get("type") in TIPURI_PREPRINT:
            c["_motive"].append(f"tip {it.get('type')} — versiune anterioară")
        if not it.get("container-title"):
            c["_motive"].append("fără revistă")
        pg = it.get("page") or ""
        if pg and "-" not in pg and "–" not in pg:
            c["_motive"].append(f"o singură pagină ({pg})")
        candidati.append(c)

    # Un preprint se scoate doar dacă există o variantă PUBLICATĂ a aceluiași titlu în lot;
    # altfel rămâne, fiindcă poate chiar aia e sursa.
    titluri_publicate = {_normalizeaza(c["_titlu"]) for c in candidati
                         if c.get("type") not in TIPURI_PREPRINT and c.get("container-title")}
    for c in candidati:
        if c.get("type") in TIPURI_PREPRINT and _normalizeaza(c["_titlu"]) in titluri_publicate:
            c["_motive"].append("există varianta publicată a aceluiași titlu")

    candidati.sort(key=lambda c: (len(c["_motive"]), -len(c.get("author") or [])))
    return candidati


def campuri_add_item(c):
    """Candidatul, tradus în forma cerută de `zot.add_item` — câmpuri plus creatori."""
    # Anul e al volumului tipărit, nu al apariției online. `issued` din Crossref e data cea mai
    # veche dintre ele, adică de obicei online-first, iar Chicago citează articolul cu anul
    # numărului în care a apărut. Măsurat pe 9 octombrie 2026, la o bibliotecă restaurată: cinci
    # articole citate într-un raport au ieșit cu un an mai devreme decât în raport (2008 în loc
    # de 2009, 2024 în loc de 2025 ș.a.), toate din `issued`. Ordinea: `published-print`, apoi
    # data tipărită a numărului (`journal-issue`), abia apoi `issued`.
    an = ""
    for dp in ((c.get("published-print") or {}).get("date-parts"),
               (((c.get("journal-issue") or {}).get("published-print")) or {}).get("date-parts"),
               (c.get("issued") or {}).get("date-parts")):
        if dp and dp[0] and dp[0][0]:
            an = str(dp[0][0])
            break
    # Crossref dă unele titluri cu entități HTML („Quality &amp; Quantity"), care ajung așa în
    # câmp și în bibliografie.
    campuri = {
        "title": html.unescape(c.get("_titlu", "")),
        "publicationTitle": html.unescape((c.get("container-title") or [""])[0]),
        "volume": c.get("volume", ""),
        "issue": c.get("issue", ""),
        "pages": c.get("page", ""),
        "date": an,
        "DOI": c.get("DOI", ""),
    }
    creatori = []
    for a in c.get("author") or []:
        if a.get("family"):
            creatori.append(("author", a["family"], a.get("given", "")))
        elif a.get("name"):
            creatori.append(("author", a["name"]))
    return {k: v for k, v in campuri.items() if v}, creatori


def _tipareste(candidati):
    for i, c in enumerate(candidati, 1):
        camp, cre = campuri_add_item(c)
        autori = ", ".join(x[1] for x in cre[:3]) + (" et al." if len(cre) > 3 else "")
        semn = "  " if not c["_motive"] else "!!"
        print(f"{semn} [{i}] {c['_titlu'][:88]}")
        print(f"      {autori or '(fără autori)'} · {camp.get('date','?')} · "
              f"{camp.get('publicationTitle') or '(fără revistă)'} · "
              f"{c.get('type')} · pp. {camp.get('pages') or '—'} · {camp.get('DOI','')}")
        for m in c["_motive"]:
            print(f"      ⚠ {m}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    titlu = sys.argv[1]
    cand = cauta(titlu)
    if not cand:
        print("niciun rezultat"); sys.exit(1)
    if "--json" in sys.argv:
        camp, cre = campuri_add_item(cand[0])
        print(json.dumps({"fields": camp, "creators": cre}, ensure_ascii=False, indent=2))
    else:
        _tipareste(cand)
        print("\nNimic nu se inserează automat. Alege candidatul, apoi zot.add_item cu --json.")
