#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Aduce de la sursă părțile libere ale Chicago Manual of Style și le pune în skill.

De ce există. Manualul e cu abonament: paginile de secțiune (`/book/ed18/...`) răspund cu 302
către ecranul de autentificare. Libere sunt două lucruri, și amândouă spun destul cât să citezi
corect: **Citation Quick Guide**, cu exemplele oficiale pentru fiecare fel de sursă, și
**cuprinsul capitolelor**, care dă numărul și titlul exact al fiecărei secțiuni — deci se poate
trimite la „CMOS 18, 14.107" fără să ghicești.

Fără asta, cine are nevoie de forma unei citări o cere unui motor de căutare și primește un ghid
de bibliotecă universitară care parafrazează manualul. Scriptul aduce sursa, nu parafraza.

Ce scrie pe disc, în `references/chicago/`:
  - `<nume>.html` — pagina, așa cum a venit;
  - `<nume>.txt`  — textul ei, fără marcaj, pentru căutare rapidă și pentru memoizare;
  - `INDEX.md`    — ce s-a adus, de unde, când, și ce a rămas blocat de abonament.

Rulare:
    python scripts/adu_chicago.py            # aduce ce lipsește sau e mai vechi de 90 de zile
    python scripts/adu_chicago.py --forteaza  # aduce tot, oricât de proaspăt ar fi
    python scripts/adu_chicago.py --verifica  # nu descarcă nimic; spune ce e vechi
"""
import argparse
import datetime
import html
import io
import os
import re
import sys
import urllib.error
import urllib.request

AICI = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(os.path.dirname(AICI), "references", "chicago")
ZILE_PROASPAT = 90
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

PAGINI = [
    ("quick-guide-note-bibliografie",
     "https://www.chicagomanualofstyle.org/tools_citationguide/citation-guide-1.html",
     "Citation Quick Guide — sistemul notă–bibliografie, cu exemple oficiale"),
    ("quick-guide-autor-data",
     "https://www.chicagomanualofstyle.org/tools_citationguide/citation-guide-2.html",
     "Citation Quick Guide — sistemul autor–dată, cu exemple oficiale"),
    ("cuprins-cap14",
     "https://www.chicagomanualofstyle.org/book/ed18/part3/ch14/toc.html",
     "Cuprinsul capitolului 14 (ed. 18) — numărul și titlul fiecărei secțiuni de citare"),
    ("cuprins-cap13",
     "https://www.chicagomanualofstyle.org/book/ed18/part3/ch13/toc.html",
     "Cuprinsul capitolului 13 (ed. 18) — sistemul autor–dată"),
]


def text_din_html(h):
    """Textul vizibil, cât să se poată căuta în el. Nu e un randator, e o sită."""
    h = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = html.unescape(h)
    h = re.sub(r"[ \t ]+", " ", h)
    h = re.sub(r"\n\s*\n\s*\n+", "\n\n", h)
    return h.strip()


def adu(url):
    """(html, eroare). Un 302 către ecranul de autentificare NU e o pagină — e un zid."""
    cerere = urllib.request.Request(url, headers={"User-Agent": UA,
                                                  "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(cerere, timeout=60) as r:
            final = r.geturl()
            if "login" in final or "/action/login" in final:
                return None, "abonament: redirectat la %s" % final
            return r.read().decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        return None, "HTTP %s" % e.code
    except Exception as e:                                   # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, e)


def varsta_zile(cale):
    if not os.path.exists(cale):
        return None
    delta = datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(cale))
    return delta.days


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forteaza", action="store_true", help="adu tot, chiar dacă e proaspăt")
    ap.add_argument("--verifica", action="store_true", help="nu descărca nimic; spune ce e vechi")
    a = ap.parse_args()

    if not os.path.isdir(DEST):
        os.makedirs(DEST)

    randuri, adus, sarit, cazut = [], 0, 0, 0
    for nume, url, descriere in PAGINI:
        cale_html = os.path.join(DEST, nume + ".html")
        zile = varsta_zile(cale_html)

        if a.verifica:
            stare = "lipsește" if zile is None else ("%d zile" % zile)
            print("%-32s %-12s %s" % (nume, stare, url))
            continue

        if zile is not None and zile < ZILE_PROASPAT and not a.forteaza:
            print("%-32s proaspăt (%d zile), sărit" % (nume, zile))
            sarit += 1
            randuri.append((nume, url, descriere, "nesch. de %d zile" % zile))
            continue

        h, eroare = adu(url)
        if eroare:
            print("%-32s NU S-A ADUS — %s" % (nume, eroare))
            cazut += 1
            randuri.append((nume, url, descriere, eroare))
            continue

        io.open(cale_html, "w", encoding="utf-8").write(h)
        t = text_din_html(h)
        io.open(os.path.join(DEST, nume + ".txt"), "w", encoding="utf-8").write(t)
        print("%-32s adus (%s caractere de text)" % (nume, format(len(t), ",")))
        adus += 1
        randuri.append((nume, url, descriere, "adus azi"))

    if a.verifica:
        return

    azi = datetime.date.today().isoformat()
    with io.open(os.path.join(DEST, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("# Chicago Manual of Style — ce e adus de la sursă\n\n")
        f.write("Adus cu `scripts/adu_chicago.py`, ultima rulare **%s**. Se reîmprospătează\n"
                "când trece de %d de zile; `--forteaza` aduce oricum.\n\n" % (azi, ZILE_PROASPAT))
        f.write("| Fișier | Ce e | Stare |\n|---|---|---|\n")
        for nume, url, descriere, stare in randuri:
            f.write("| [`%s.txt`](%s.txt) · [`.html`](%s.html) | %s — <%s> | %s |\n"
                    % (nume, nume, nume, descriere, url, stare))
        f.write("\n**Ce rămâne blocat.** Textul secțiunilor (`/book/ed18/...`) cere abonament: "
                "serverul răspunde cu 302 către ecranul de autentificare. Din cuprins avem însă "
                "numărul și titlul exact al fiecărei secțiuni, iar din Quick Guide exemplele "
                "oficiale — împreună ajung pentru a cita corect și pentru a trimite la secțiune.\n")

    print("\nadus: %d · sărit: %d · căzut: %d · index: %s"
          % (adus, sarit, cazut, os.path.join(DEST, "INDEX.md")))
    return 1 if cazut and not adus else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
