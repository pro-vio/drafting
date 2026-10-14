# -*- coding: utf-8 -*-
"""verifica_metadate.py — metadatele unui item, verificate pe textul fișierului lui.

Regula (autorul, 12.10.2026): la citire, primul lucru e verificarea metadatelor pe text și
semnalarea problemelor. Nu se citează obiecte ale căror metadate n-au fost verificate pe text decât
ca excepție aprobată de autor. Cazurile care au cerut-o, toate din aceeași săptămână: un fișier al
Codului fiscal etichetat cu altă formă decât cea pe care o conține; ani de apariție online în locul
anului volumului; un autor scris „Baumgärtner"; capitole ale căror PDF-uri stăteau sub alt item.

Ce se verifică, pe primele pagini ale textului (unde stau titlul, autorii, revista și anul):

- **titlul**: cuvintele lui semnificative se regăsesc în text;
- **autorii** (și editorii, la capitole): numele de familie se regăsesc. **Autorul-organizație** se
  verifică pe pagina care publică documentul (decizia autorului, 12.10.2026; Chicago 18, 13.86):
  domeniul din `url` trebuie să fie al organizației, după `references/organizatii-domenii.json`;
  dacă organizația nu e acolo, numele ei trebuie să apară în text;
- **anul**: apare în primele pagini;
- **DOI**, când itemul îl are: apare în text;
- **revista / volumul**, când itemul le are: se regăsesc.

Actele normative (`statute`) se verifică altfel — după antetul fișierului și notele de modificare —,
cu `completeaza_forme.py`; aici se trimit acolo.

La un item cu mai multe PDF-uri se citesc toate: trece dacă unul confirmă tot. Textul se ia din cache întâi (`pdf_text_cache.get_cached`); la miss se extrage din PDF, iar
memoizarea se lasă în coadă. Read-only: merge cu Zotero deschis.

    python verifica_metadate.py --colectie "proiect/surse"          # raportul
    python verifica_metadate.py --chei ABCD1234 EFGH5678             # itemi anume
    python verifica_metadate.py --colectie "proiect/surse" --amana   # marchează ce a trecut + memoizează

`--amana` lasă în coadă, pentru itemii care trec, rândul „Metadate verificate pe text: AAAA-LL-ZZ"
în `extra` — după el se recunoaște, la citare, un item verificat —, plus memoizarea textelor
extrase. Itemii cu probleme nu se ating: ce se corectează decide autorul.
"""
import argparse, datetime, os, re, sys, unicodedata

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
AICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AICI)
import zot  # noqa: E402
import completeaza_forme as cf  # noqa: E402

from completeaza_forme import MARCAJ  # o singură sursă
PAGINI = 4          # câte pagini de la început se citesc pentru titlu, autori, an
DOMENII = os.path.join(os.path.dirname(AICI), "references", "organizatii-domenii.json")


def _domenii():
    import json
    try:
        d = json.load(open(DOMENII, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {_fold(k): v for k, v in d.items() if not k.startswith("_")}


def _organizatia_pe_pagina(nume, url):
    """True / False / None (organizația nu e în listă). Autorul-organizație se verifică pe pagina care
    publică documentul, nu în text (decizia autorului, 12.10.2026; Chicago 18, 13.86): numele unei
    instituții lipsește des din fișier — un comunicat are în antet doar domeniul statistic, o anexă
    tehnică numește instituția doar prescurtat."""
    dom = _domenii().get(_fold(nume))
    if dom is None:
        return None
    gazda = re.sub(r"^www\.", "", (re.match(r"https?://([^/:]+)", url or "") or [None, ""])[1].lower())
    return any(gazda == d or gazda.endswith("." + d) for d in dom)


def _texte(cheie_item):
    """[(text, de_unde, memo)] pentru TOATE PDF-urile itemului. Un item cu mai multe fișiere (normele
    agricole pe județe, 41 de PDF-uri) nu se judecă după primul: primul putea să n-aibă anul, deși 26
    dintre celelalte îl au (12.10.2026)."""
    con = zot._ro()
    try:
        rows = con.execute("SELECT a.key FROM itemAttachments ia JOIN items a ON a.itemID=ia.itemID JOIN items p "
                           "ON p.itemID=ia.parentItemID WHERE p.key=? AND ia.contentType='application/pdf' "
                           "AND a.itemID NOT IN (SELECT itemID FROM deletedItems)", (cheie_item,)).fetchall()
    finally:
        con.close()
    out = []
    for (k,) in rows:
        cale = zot.attachment_path(k)
        if not cale or not os.path.exists(cale):
            continue
        t = cf.ptc.get_cached(k)
        out.append((t, "cache", None) if t else
                   (cf.ptc.extract_pdf_text(cale), "PDF, memoizare lăsată în coadă", (k, cale)))
    return out


def _fold(s):
    s = unicodedata.normalize("NFKD", (s or "").replace("­", "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _inceput(text):
    """Primele PAGINI pagini: după marcajele de pagină din cache, altfel primele ~12 000 de caractere."""
    parti = re.split(r"-{3} page \d+ -{3}", text or "")
    parti = [p for p in parti if p.strip()]
    return " ".join(parti[:PAGINI]) if len(parti) > 1 else (text or "")[:12000]


def _fara_spatii(s):
    return re.sub(r"[\s­]+", "", s or "")


_CR = {}


def _crossref(doi):
    """{"titlu", "an"} din Crossref pentru DOI, sau None. Anul e al volumului tipărit, ca în
    `fetch_metadata` (2.38.2): published-print, altfel issued."""
    if doi in _CR:
        return _CR[doi]
    import json, urllib.parse, urllib.request
    try:
        m = json.load(urllib.request.urlopen(urllib.request.Request(
            "https://api.crossref.org/works/" + urllib.parse.quote(doi),
            headers={"User-Agent": "drafting-skill (verifica_metadate)"}), timeout=30))["message"]
        dp = ((m.get("published-print") or {}).get("date-parts") or (m.get("issued") or {}).get("date-parts") or [[None]])
        # Subtitlul stă separat la Crossref („Student Politics in Africa" + „Representation and Activism");
        # fără el, titlul itemului părea al altei lucrări (13.10.2026).
        _CR[doi] = {"titlu": " ".join([(m.get("title") or [""])[0]] + (m.get("subtitle") or [])), "an": dp[0][0],
                    "doar_online": not m.get("published-print")}
    except Exception:  # noqa: BLE001
        _CR[doi] = None
    return _CR[doi]


def _an_din_catalog(f, tip):
    """(None, ce se face). Fără DOI și fără anul în text, anul se verifică pe pagina editurii sau a revistei, nu într-un
    agregator, care poate și el să greșească (decizia autorului, 13.10.2026): Open Library, Google Books și DBLP au
    fost scoase. Verificarea se face în browser, iar rezultatul se notează în Extra ca „Anul verificat pe pagina
    editorului (…): …", rând pe care `_probleme` îl recunoaște. Crossref rămâne numai pentru lucrările cu DOI."""
    return None, "de verificat pe pagina editurii sau a revistei (în browser), apoi notat în Extra"


def itemi(colectie=None, chei=None):
    con = zot._ro()
    try:
        if chei:
            rows = [(zot.item_id_for_key(con, k), k) for k in chei]
        else:
            g = zot.find_collection(colectie, verifica_api=False)
            if not g:
                raise SystemExit("Colecția %r nu există." % colectie)
            rows = con.execute(
                "SELECT i.itemID, i.key FROM collectionItems ci JOIN items i USING(itemID) WHERE ci.collectionID=? "
                "AND i.itemID NOT IN (SELECT itemID FROM deletedItems) AND i.itemID NOT IN "
                "(SELECT itemID FROM itemAttachments) AND i.itemID NOT IN (SELECT itemID FROM itemNotes)",
                (g[1],)).fetchall()
        out = []
        for iid, k in rows:
            if iid is None:
                continue
            tip = con.execute("SELECT t.typeName FROM items i JOIN itemTypes t USING(itemTypeID) WHERE i.itemID=?",
                              (iid,)).fetchone()[0]
            f = dict(con.execute("SELECT f.fieldName, v.value FROM itemData d JOIN fields f USING(fieldID) "
                                 "JOIN itemDataValues v USING(valueID) WHERE d.itemID=?", (iid,)).fetchall())
            cr = con.execute("SELECT ct.creatorType, c.lastName, c.fieldMode FROM itemCreators ic JOIN creators c USING(creatorID) "
                             "JOIN creatorTypes ct USING(creatorTypeID) WHERE ic.itemID=? ORDER BY orderIndex",
                             (iid,)).fetchall()
            out.append({"cheie": k, "tip": tip, "campuri": f, "creatori": cr})
        return out
    finally:
        con.close()


def verifica_item(x):
    """(stare, probleme, memo, de_unde). stare: „ok", „probleme", „fără text", „act normativ".
    `memo` e lista memoizărilor de lăsat în coadă. La mai multe PDF-uri, itemul trece dacă unul dintre
    ele confirmă tot; altfel se raportează fișierul cu cele mai puține probleme."""
    if x["tip"] == "statute":
        return "act normativ", ["se verifică cu completeaza_forme.py (antetul și notele de modificare)"], None, ""
    if x["tip"] == "computerProgram":
        return _software(x)
    texte = [t for t in _texte(x["cheie"]) if t[0]]
    if not texte:
        _, de_unde, _ = cf._text(x["cheie"])
        return "fără text", [de_unde], None, de_unde
    memo = [m for _, _, m in texte if m]
    rezultate = sorted(((_probleme(x, t), d) for t, d, _ in texte), key=lambda r: len(r[0]))
    probleme, de_unde = rezultate[0]
    if len(texte) > 1:
        de_unde = "%s; %d PDF-uri, %d fără probleme" % (de_unde, len(texte), sum(1 for r, _ in rezultate if not r))
    return ("ok" if not probleme else "probleme"), probleme, memo, de_unde


PROIECTE = []      # dosarele proiectelor, din --proiect


def _software(x):
    """Un program n-are text de citit: corespondentul lui e mediul proiectului care l-a folosit (regula autorului,
    13.10.2026). Se caută pachetul în `renv.lock` și în DESCRIPTION-ul pachetului instalat în `renv/library`; versiunea
    și data publicării trebuie să fie ale itemului (CMOS 18, 14.169: producătorul, versiunea, data, adresa).
    Deocamdată pachete R; alt mediu se adaugă când apare."""
    import glob, json
    f = x["campuri"]
    nume = (f.get("title") or "").split(":")[0].strip()
    if not PROIECTE:
        return "probleme", ["software: dă dosarul proiectului care îl folosește (--proiect), unde se verifică versiunea"], None, ""
    gasite = []
    for d in PROIECTE:
        for lock in glob.glob(os.path.join(d, "renv.lock")):
            try:
                v = json.load(open(lock, encoding="utf-8"))["Packages"].get(nume, {}).get("Version")
            except (OSError, ValueError, KeyError):
                v = None
            if v:
                gasite.append(("renv.lock", v, None, lock))
        for desc in glob.glob(os.path.join(d, "renv", "library", "*", "*", "*", nume, "DESCRIPTION")):
            t = open(desc, encoding="utf-8", errors="replace").read()
            v = re.search(r"^Version:\s*(\S+)", t, re.M)
            pub = re.search(r"^Date/Publication:\s*(\d{4}-\d{2}-\d{2})", t, re.M)
            gasite.append(("DESCRIPTION", v.group(1) if v else None, pub.group(1) if pub else None, desc))
    if not gasite:
        return "probleme", ["software %r: n-apare în mediul proiectului (%s)" % (nume, ", ".join(PROIECTE))], None, ""
    probleme = []
    ver = (f.get("versionNumber") or "").strip()
    if ver and not any(g[1] == ver for g in gasite):
        probleme.append("versiunea %s; proiectul folosește %s" % (ver, ", ".join(sorted({g[1] for g in gasite if g[1]}))))
    data = (f.get("date") or "")[:10]
    pub = [g[2] for g in gasite if g[2]]
    if pub and data and data not in pub:
        probleme.append("data %s; pachetul instalat e publicat la %s" % (data, pub[0]))
    return ("ok" if not probleme else "probleme"), probleme, None, "mediul proiectului: " + os.path.relpath(gasite[0][3], PROIECTE[0])


def _probleme(x, text):
    f, inc, tot = x["campuri"], _fold(_inceput(text)), _fold(text)
    probleme = []
    titlu = f.get("title") or f.get("caseName") or ""
    cuv = [w for w in _fold(titlu).split() if len(w) >= 4]
    if cuv:
        gasite = sum(1 for w in cuv if w in inc)
        if gasite / len(cuv) < 0.7:
            probleme.append("titlul: doar %d din %d cuvinte se găsesc la început (%r)" % (gasite, len(cuv), titlu[:70]))
    for rol, nume, mod in x["creatori"]:
        n = _fold(nume)
        if not n:
            continue
        if mod == 1:
            # Autor-organizație: dovada e pagina care publică documentul (domeniul din url).
            pe_pagina = _organizatia_pe_pagina(nume, f.get("url"))
            if pe_pagina:
                continue
            if n in tot:
                continue
            probleme.append("%s %r: %s" % (rol, nume, "domeniul din url nu e al organizației" if pe_pagina is False
                            else "organizația nu e în references/organizatii-domenii.json și nici în text; "
                                 "adaug-o acolo cu domeniul pe care publică"))
            continue
        if rol == "editor":
            # Editorii stau pe pagina de titlu a volumului, pe care PDF-ul unui capitol de obicei n-o
            # are; se semnalează doar dacă lipsesc din tot textul, și doar ca observație.
            continue
        # „A R T H U R L U P I A": unele reviste culeg numele cu litere spațiate (13.10.2026).
        if n not in inc and _fara_spatii(n) not in _fara_spatii(inc):
            probleme.append("%s %r nu apare la începutul textului%s" % (rol, nume, "" if n in tot else ", nici în rest"))
    an = re.search(r"\d{4}", f.get("date") or "")
    doi = (f.get("DOI") or "").strip().lower()
    doi_in_text = bool(doi) and doi in _fara_spatii((text or "").lower())
    # DOI-ul și anul lipsesc des din PDF fără să fie greșite: scanările vechi n-au DOI, capitolele
    # Bloomsbury n-au an. Atunci se verifică la sursă: înregistrarea Crossref a DOI-ului trebuie să aibă
    # același titlu, iar anul volumului (published-print, altfel issued) trebuie să fie anul itemului.
    if doi and doi_in_text and x["tip"] == "journalArticle" and an:
        # Anul online apare și el în text („Published online: 20 August 2012"), deci prezența anului în text nu
        # dovedește anul volumului. La articole se compară cu `published-print` de la Crossref (Singer, BJPolS 43 (1),
        # ianuarie 2013, era în bibliotecă cu 2012; 13.10.2026).
        cr = _crossref(doi)
        if cr and not cr["doar_online"] and cr["an"] and str(cr["an"]) != an.group(0):
            probleme.append("anul %s; anul volumului tipărit (Crossref) e %s" % (an.group(0), cr["an"]))
    arxiv = re.match(r"10\.48550/arxiv\.(.+)$", doi)
    if arxiv and not doi_in_text:
        # DOI-urile arXiv sunt DataCite, nu Crossref: se verifică identificatorul arXiv în text.
        if arxiv.group(1) not in (text or "").lower():
            probleme.append("identificatorul arXiv %s nu apare în text" % arxiv.group(1))
    elif doi and not doi_in_text:
        cr = _crossref(doi)
        if cr is None:
            probleme.append("DOI-ul %s nu apare în text, iar Crossref nu-l cunoaște" % doi)
        else:
            tc = [w for w in _fold(cr["titlu"]).split() if len(w) >= 4]
            if cuv and tc and sum(1 for w in cuv if w in tc) / len(cuv) < 0.7:
                probleme.append("DOI-ul %s e, la Crossref, al altei lucrări: %r" % (doi, cr["titlu"][:80]))
            elif an and cr["an"] and str(cr["an"]) != an.group(0) and not (cr["doar_online"] and an.group(0) in tot):
                # Fără `published-print`, Crossref dă data înregistrării online; când textul poartă anul
                # itemului (antetul revistei, „Vol. 10/2, December 2018"), acela e anul volumului.
                probleme.append("anul %s; Crossref dă anul volumului %s" % (an.group(0), cr["an"]))
    elif an and an.group(0) not in tot and re.search(r"verificat pe pagina editorului[^\n]*\b%s\b" % an.group(0),
                                                     f.get("extra") or ""):
        # Anul confirmat de mână pe pagina editorului, cu sursa notată în Extra (ACM, Consiliul Europei, 13.10.2026),
        # când niciun catalog accesibil automat n-are lucrarea.
        pass
    elif an and an.group(0) not in tot:
        an_c, sursa = _an_din_catalog(f, x["tip"])
        if an_c is None:
            probleme.append("anul %s nu apare în text și nu există DOI: %s" % (an.group(0), sursa))
        elif str(an_c) != an.group(0):
            probleme.append("anul %s nu apare în text; %s dă %s" % (an.group(0), sursa, an_c))
    for camp in ("publicationTitle", "bookTitle"):
        c = [w for w in _fold(f.get(camp)).split() if len(w) >= 4]
        if c and sum(1 for w in c if w in tot) / len(c) < 0.6:
            probleme.append("%s %r nu se regăsește în text" % (camp, f.get(camp)[:60]))
    return probleme


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--colectie", default=None)
    p.add_argument("--chei", nargs="*", default=None)
    p.add_argument("--proiect", action="append", default=[],
                   help="dosarul proiectului; la software, versiunea se verifică în mediul lui (renv.lock, renv/library)")
    p.add_argument("--amana", action="store_true",
                   help="marchează în coadă itemii care trec și memoizează textele extrase")
    a = p.parse_args()
    PROIECTE.extend(os.path.abspath(d) for d in a.proiect)
    if not a.colectie and not a.chei:
        raise SystemExit("Dă --colectie sau --chei.")
    azi = datetime.date.today().isoformat()
    ops, stari = [], {}
    for x in itemi(a.colectie, a.chei):
        stare, probleme, memo, de_unde = verifica_item(x)
        stari[stare] = stari.get(stare, 0) + 1
        nume = (x["campuri"].get("title") or x["campuri"].get("nameOfAct") or "")[:55]
        print("  %-12s %s %-55s %s" % (stare.upper(), x["cheie"], nume, ("(text: %s)" % de_unde) if de_unde else ""))
        for pr in probleme:
            print("      ! %s" % pr)
        for m in memo or []:
            ops.append({"op": "ensure_cached", "atasament": m[0], "cale": m[1]})
        extra = x["campuri"].get("extra") or ""
        if stare == "ok" and MARCAJ not in extra:
            if x["tip"] == "computerProgram":
                # Software: verificarea s-a făcut pe mediul proiectului, iar Extra spune care (CMOS 18, 14.169).
                ops.append({"op": "adauga_in_extra", "item": x["cheie"], "daca_lipseste": "Versiune verificată în",
                            "rand": "Versiune verificată în %s (%s)" % (de_unde.replace("mediul proiectului: ", ""),
                                                                      PROIECTE[0])})
            ops.append({"op": "adauga_in_extra", "item": x["cheie"], "rand": "%s %s" % (MARCAJ, azi),
                        "daca_lipseste": MARCAJ})
    print("\n" + " | ".join("%s: %d" % kv for kv in sorted(stari.items())))
    if a.amana and ops:
        import scrieri_amanate as sa
        print("Lăsat în scrieri amânate (%d operații): %s" % (len(ops), sa.lasa(
            "verificarea metadatelor pe text (%s)" % (a.colectie or "chei"), ops, cine="verifica_metadate.py")))


if __name__ == "__main__":
    main()
