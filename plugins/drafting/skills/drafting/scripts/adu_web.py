# -*- coding: utf-8 -*-
"""adu_web.py — o sursă web oficială (comunicat de presă, pagină, document PDF de pe un sit) adusă în Zotero ca item
„webpage" (pagină, comunicat) sau „report" (broșură, fișă, raport al unei instituții, CMOS 18, 14.117), cu
instantaneul atașat și metadatele verificate pe textul ei.

Regula (CMOS 18, 14.104 pentru pagini și conținutul siturilor; 14.98 pentru comunicate): autorul e organizația care
publică, apoi anul, titlul între ghilimele, situl, ziua și luna, adresa. Data accesării intră numai când sursa n-are
nicio dată de publicare sau revizuire; atunci anul e „n.d.". Autorul-organizație se verifică pe pagina care publică
documentul, adică pe domeniul adresei, după `references/organizatii-domenii.json` (decizia autorului, 12.10.2026).

Textul se ia de unde se poate citi: multe situri instituționale întorc un corp gol la o cerere directă și se citesc
doar randate, în browser. De aceea unealta nu descarcă singură pagina: primește textul ei (`--text`, salvat după
citirea în browser) sau PDF-ul (`--pdf`). Din text face un PDF cu antetul de proveniență (adresa, data accesării),
ca instantaneu; un PDF se atașează așa cum e.

Înainte de a lăsa ceva în coadă verifică pe text titlul (70% din cuvinte), data (dacă e dată) și autorul (domeniul
adresei, sau numele în text). Ce nu trece, oprește. Scrierea merge mereu prin `scrieri_amanate`.

    python adu_web.py --url URL --text pagina.txt --autor "Ministerul …" --titlu "…" --data 2024-03-05 \\
        --site "Ministerul Muncii și Solidarității Sociale" --gen "Comunicat de presă" --colectie CHEIE
    python adu_web.py --url URL --pdf fisa.pdf --autor "…" --titlu "…" --gen "Fișă de prezentare" --colectie CHEIE
"""
import argparse, datetime, os, re, sys, unicodedata

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
AICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AICI)
import zot  # noqa: E402

LUNI = ["ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie", "august", "septembrie", "octombrie",
        "noiembrie", "decembrie"]


def _fold(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _text_pdf(cale):
    import fitz  # noqa: PLC0415
    return "\n".join(p.get_text() for p in fitz.open(cale))


def versiune_server(url):
    """„AAAA-LL-ZZ HH:MM GMT" din antetul Last-Modified al fișierului, sau "" dacă serverul nu-l dă."""
    import email.utils, urllib.request  # noqa: PLC0415
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"}),
                                   timeout=30)
        lm = r.headers.get("Last-Modified")
    except Exception:  # noqa: BLE001
        return ""
    if not lm:
        return ""
    return email.utils.parsedate_to_datetime(lm).strftime("%Y-%m-%d %H:%M GMT")


def verifica(a, text, pagina=""):
    """Lista problemelor; goală dacă titlul, data și autorul se regăsesc.

    Titlul și data se caută în document și, dacă e dată, pe pagina care îl listează (`--pagina`). Un fișier tehnic
    — un jurnal de modificări, o schemă — nu-și scrie titlul în el; titlul lui e rândul din pagina sitului
    („Modificări - actualizat în data de 03.11.2025"). Cazul: jurnalul F2202 de pe epatrim, 13.10.2026."""
    import verifica_metadate as vm  # noqa: PLC0415
    probleme = []
    text = text + "\n" + pagina
    t = _fold(text)
    cuv = [w for w in _fold(a.titlu).split() if len(w) >= 4]
    if cuv and sum(1 for w in cuv if w in t.split()) / len(cuv) < 0.7:
        probleme.append("titlul nu se regăsește în text")
    if a.data:
        an, luna, zi = a.data.split("-")
        forme = ["%s.%s.%s" % (zi, luna, an), "%s/%s/%s" % (zi, luna, an), "%d %s %s" % (int(zi), LUNI[int(luna) - 1], an)]
        if not any(f in text for f in forme):
            probleme.append("data %s nu apare în text (%s)" % (a.data, " / ".join(forme)))
    pe_pagina = vm._organizatia_pe_pagina(a.autor, a.url)
    if not pe_pagina and _fold(a.autor) not in t:
        probleme.append("autorul %r: %s" % (a.autor, "domeniul adresei nu e al lui" if pe_pagina is False else
                        "nu e în references/organizatii-domenii.json și nici în text"))
    return probleme


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--text", help="textul paginii, citit (de pildă în browser) și salvat")
    g.add_argument("--pdf", help="documentul PDF de pe sit, descărcat")
    p.add_argument("--autor", required=True, help="organizația care publică, cum semnează")
    p.add_argument("--titlu", required=True)
    p.add_argument("--data", default="", metavar="AAAA-LL-ZZ", help="data publicării; lipsă = n.d., cu data accesării")
    p.add_argument("--site", default="", help="numele sitului (websiteTitle)")
    p.add_argument("--gen", default="", help="genul scris pe document: „Comunicat de presă”, „Fișă electronică de prezentare”")
    p.add_argument("--tip", choices=("webpage", "report"), default="webpage",
                   help="webpage = pagină sau comunicat (CMOS 14.104, 14.98); report = broșură, fișă, raport emis de o "
                        "instituție (CMOS 14.117), cu instituția și genul documentului")
    p.add_argument("--pagina", default="", help="textul paginii care listează documentul, salvat din browser: titlul "
                   "și data se caută și acolo, pentru fișierele care nu le scriu în ele")
    p.add_argument("--accesat", default=datetime.date.today().isoformat())
    p.add_argument("--colectie", required=True)
    p.add_argument("--amana", action="store_true", help="lasă scrierea în coadă (altfel doar verifică)")
    a = p.parse_args()

    # Aceeași adresă deja în bibliotecă = același item; nu se dublează.
    con = zot._ro()
    deja = con.execute("SELECT i.key FROM items i JOIN itemData d USING(itemID) JOIN fields f USING(fieldID) "
                       "JOIN itemDataValues v USING(valueID) WHERE f.fieldName='url' AND v.value=? AND i.itemID NOT IN "
                       "(SELECT itemID FROM deletedItems)", (a.url,)).fetchall()
    if deja:
        raise SystemExit("Adresa e deja în bibliotecă: %s. Nu creez alt item." % ", ".join(k for k, in deja))
    # Adresa nu ajunge: un item adus de mână poate n-o aibă. Se caută și același fișier (amprenta md5, pe care
    # Zotero o ține în `storageHash`) și același titlu. Cazul: anexa PatrimVen din 2025, deja în bibliotecă fără
    # adresă, a fost adusă a doua oară (13.10.2026).
    if a.pdf:
        import hashlib  # noqa: PLC0415
        md5 = hashlib.md5(open(a.pdf, "rb").read()).hexdigest()
        deja = con.execute("SELECT p.key FROM itemAttachments ia JOIN items i USING(itemID) JOIN items p "
                           "ON p.itemID = ia.parentItemID WHERE ia.storageHash=? AND i.itemID NOT IN "
                           "(SELECT itemID FROM deletedItems) AND p.itemID NOT IN (SELECT itemID FROM deletedItems)",
                           (md5,)).fetchall()
        if deja:
            raise SystemExit("Același fișier e deja în bibliotecă, la %s. Nu creez alt item; corectează-l pe acela."
                             % ", ".join(k for k, in deja))
    titluri = con.execute("SELECT i.key, v.value FROM items i JOIN itemData d USING(itemID) JOIN fields f "
                          "USING(fieldID) JOIN itemDataValues v USING(valueID) WHERE f.fieldName='title' AND "
                          "i.itemID NOT IN (SELECT itemID FROM deletedItems)").fetchall()
    deja = [k for k, v in titluri if _fold(v) == _fold(a.titlu)]
    if deja:
        raise SystemExit("Un item cu același titlu e deja în bibliotecă: %s. Verifică dacă e aceeași sursă; nu creez "
                         "alt item." % ", ".join(deja))
    text = open(a.text, encoding="utf-8").read() if a.text else _text_pdf(a.pdf)
    pagina = open(a.pagina, encoding="utf-8").read() if a.pagina else ""
    probleme = verifica(a, text, pagina)
    for pr in probleme:
        print("  ! " + pr)
    if probleme:
        raise SystemExit("OPRIT: metadatele nu se verifică pe text. Nu s-a lăsat nimic în coadă.")
    if a.text:
        # Numele poartă și data: un titlu generic („Modificări") ar suprascrie instantaneul altei surse din `_import`.
        tinta = os.path.join(zot.LUCRU, "_import", re.sub(r"[^A-Za-z0-9]+", "_", _fold(a.titlu))[:70] + "_"
                             + (a.data or a.accesat) + ".pdf")
        os.makedirs(os.path.dirname(tinta), exist_ok=True)
        import adu_lege  # noqa: PLC0415
        corp = "\n".join([a.titlu, "", "Instantaneu al paginii, accesată la %s:" % a.accesat, a.url, "",
                          "=" * 78, "", text])
        print("instantaneu:", tinta, adu_lege.pdf(corp, tinta), "pagini")
    else:
        tinta = a.pdf
    campuri = {"title": a.titlu, "url": a.url, "language": "ro",
               "extra": "Metadate verificate pe text: %s" % datetime.date.today().isoformat()}
    if a.data:
        campuri["date"] = a.data
    else:
        campuri["accessDate"] = a.accesat          # 14.104: fără dată de publicare, data accesării
        # Fără dată afișată, data ultimei modificări de pe server se adaugă după data accesării („versiunea de pe
        # server din …"), fără să ia locul datei de publicare (regula autorului, 13.10.2026). `build_manuscris` o
        # afișează din rândul acesta.
        v = versiune_server(a.url)
        if v:
            campuri["extra"] = "Versiunea de pe server: %s (Last-Modified)\n%s" % (v, campuri["extra"])
            print("versiunea de pe server:", v)
    if a.tip == "report":
        # CMOS 14.117: documentul se citează ca o publicație a instituției; genul e cel scris pe document.
        campuri["institution"] = a.autor
        if a.gen:
            campuri["reportType"] = a.gen
    else:
        if a.site:
            campuri["websiteTitle"] = a.site
        if a.gen:
            campuri["websiteType"] = a.gen
    ops = [{"op": "add_item", "tip": a.tip, "campuri": campuri, "creatori": [["author", a.autor]],
            "colectie": a.colectie, "nume": "web"},
           {"op": "attach_pdf", "parinte": "web", "cale": tinta, "nume": "pdf"},
           {"op": "ensure_cached", "atasament": "pdf", "cale": tinta}]
    print("verificat pe text: titlul, %s, autorul" % ("data" if a.data else "fără dată (n.d., accesat %s)" % a.accesat))
    if a.amana:
        import scrieri_amanate as sa  # noqa: PLC0415
        print(sa.lasa("sursă web: %s" % a.titlu[:60], ops, cine="adu_web.py"))
    else:
        print("(probă: n-am lăsat nimic în coadă; `--amana` o face)")


if __name__ == "__main__":
    main()
