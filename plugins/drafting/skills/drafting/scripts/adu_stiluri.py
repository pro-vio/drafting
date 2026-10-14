#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compară stilurile CSL ținute în skill cu sursa din care și le ia Zotero, și le înlocuiește
numai la cerere.

De ce există. `build_manuscris.py` folosește implicit `references/chicago-author-date.csl`, o
copie ținută în skill. Zotero își actualizează singur stilurile din `~/Zotero/styles`; copia din
skill nu o actualizează nimeni, iar nimic nu spunea că a rămas în urmă. Pe 2 octombrie 2026 copia
era din 09.02.2025, iar sursa din 14.04.2026, și asta s-a aflat doar comparând de mână.

Sursa e `https://www.zotero.org/styles/<id>`, NU depozitul GitHub `citation-style-language/styles`.
Măsurat pe 2 octombrie 2026: ramura `master` de acolo are versiuni MAI VECHI decât zotero.org
(Chicago: 09.02.2025 față de 14.04.2026; IEEE: 27.03.2024 față de 07.01.2026), deci ca sursă de
actualizare dă un rezultat greșit care pare la zi.

Comparația separă două lucruri: data din `<updated>` și REGULILE, adică tot restul fișierului.
Când diferă doar data, citările ies identic. Când diferă regulile, scriptul arată diferența, iar
înlocuirea se face numai cu `--inlocuieste`, după ce autorul a văzut-o. Instrucțiunea autorului,
2 octombrie 2026: „vreau să avem tot timpul stilurile de citare actualizate acolo. actualizăm la
cerere". Deci scriptul nu rulează singur, din niciun alt script.

Ce scrie pe disc: copia nouă a stilului, numai cu `--inlocuieste`, și `references/stiluri-csl.md`,
la fiecare rulare. Indexul spune ce stil e ținut, de unde vine, ce dată are copia, ce dată are
sursa și când s-a verificat.

Rulare:
    python scripts/adu_stiluri.py                         # compară tot; nu înlocuiește nimic
    python scripts/adu_stiluri.py --arata                 # + diferența de reguli, linie cu linie
    python scripts/adu_stiluri.py --inlocuieste chicago-author-date   # după ce ai văzut-o
"""
import argparse
import datetime
import difflib
import glob
import os
import re
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
AICI = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(os.path.dirname(AICI), "references")
INDEX = os.path.join(REF, "stiluri-csl.md")
SURSA = "https://www.zotero.org/styles/%s"
RE_UPDATED = re.compile(r"<updated>([^<]*)</updated>")
RE_ID = re.compile(r"<id>\s*https?://www\.zotero\.org/styles/([^<\s]+)\s*</id>")


def reguli(text):
    """Fișierul fără data din `<updated>` și fără spațiile de la capăt de linie: ce rămâne decide
    cum ies citările."""
    return [l.rstrip() for l in RE_UPDATED.sub("<updated/>", text).splitlines()]


def data(text):
    m = RE_UPDATED.search(text)
    return m.group(1)[:10] if m else "?"


def adu(id_stil):
    """(text, eroare)."""
    cerere = urllib.request.Request(SURSA % id_stil, headers={"User-Agent": "drafting-adu_stiluri"})
    try:
        with urllib.request.urlopen(cerere, timeout=60) as r:
            corp = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return None, "HTTP %s" % e.code
    except Exception as e:                                   # noqa: BLE001
        return None, str(e)
    if "<style" not in corp:
        return None, "răspunsul nu e un stil CSL"
    return corp, None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arata", action="store_true", help="arată diferența de reguli")
    ap.add_argument("--inlocuieste", default="", metavar="ID",
                    help="înlocuiește copia acestui stil cu versiunea de la sursă")
    a = ap.parse_args()

    azi = datetime.date.today().isoformat()
    randuri, gasit_tinta = [], False
    for cale in sorted(glob.glob(os.path.join(REF, "*.csl"))):
        local = open(cale, encoding="utf-8").read()
        m = RE_ID.search(local)
        id_stil = m.group(1) if m else os.path.splitext(os.path.basename(cale))[0]
        sursa, eroare = adu(id_stil)
        if eroare:
            print("%-32s NU s-a putut aduce: %s" % (id_stil, eroare))
            randuri.append((id_stil, data(local), "?", "neverificat: " + eroare))
            continue
        dif = list(difflib.unified_diff(reguli(local), reguli(sursa), "skill", "zotero.org",
                                        lineterm="", n=1))
        if not dif and data(local) == data(sursa):
            stare = "la zi"
        elif not dif:
            stare = "diferă doar data; regulile sunt identice"
        else:
            n = sum(1 for l in dif if l[:1] in "+-" and l[:3] not in ("+++", "---"))
            stare = "REGULILE diferă (%d linii)" % n
        print("%-32s copie %s | sursă %s | %s" % (id_stil, data(local), data(sursa), stare))
        if dif and a.arata:
            print("\n".join(dif) + "\n")
        if a.inlocuieste == id_stil:
            gasit_tinta = True
            if not dif and data(local) == data(sursa):
                print("  nimic de înlocuit")
            else:
                with open(cale, "w", encoding="utf-8", newline="") as f:
                    f.write(sursa)
                print("  ÎNLOCUIT cu versiunea din %s" % data(sursa))
                local, stare = sursa, "la zi"
        randuri.append((id_stil, data(local), data(sursa), stare))

    if a.inlocuieste and not gasit_tinta:
        raise SystemExit("Nu există în references/ un stil cu id-ul %r." % a.inlocuieste)

    with open(INDEX, "w", encoding="utf-8", newline="\n") as f:
        f.write("# Stilurile CSL ținute în skill\n\n")
        f.write("Verificat cu `scripts/adu_stiluri.py`, ultima rulare **%s**. Sursa e "
                "`https://www.zotero.org/styles/<id>`, nu GitHub. Se înlocuiește numai la cerere, "
                "cu `--inlocuieste <id>`.\n\n" % azi)
        f.write("| Stil | Data copiei | Data sursei | Stare |\n|---|---|---|---|\n")
        for id_stil, d_loc, d_src, stare in randuri:
            f.write("| `%s` | %s | %s | %s |\n" % (id_stil, d_loc, d_src, stare))
    print("\nindex ->", os.path.relpath(INDEX, os.path.dirname(REF)))


if __name__ == "__main__":
    main()
