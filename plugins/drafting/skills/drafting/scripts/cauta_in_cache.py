# -*- coding: utf-8 -*-
"""Caută un șir sau o expresie în TOATE textele memoizate deodată.

**De ce există.** `get_cached` cere să știi deja în ce fișier cauți, deci fluxul real era
„ghicesc sursa → o deschid → citesc o bucată → decid dacă e acolo". Pasul al treilea înșală: pe
11 septembrie 2026, într-un text de peste două milioane de caractere, primele pagini erau despre
altceva, de acolo a ieșit concluzia că actul nu conține ce se caută, iar răspunsul a fost găsit pe
internet — și confirmat apoi în cache, unde stătea de la început.

Cu unealta asta regula 21 devine verificabilă, nu o chestiune de disciplină: **înainte de orice
căutare pe web despre o sursă, se rulează asta; internetul intră în discuție numai după ce ea
întoarce zero.**

    python scripts/cauta_in_cache.py "300 de lei"
    python scripts/cauta_in_cache.py "contribuția de asigurări" --colectie "proiect-exemplu/metodologie"
    python scripts/cauta_in_cache.py "art\\. II" --regex --context 400

**Ce se tipărește la final, și e jumătatea care lipsea din propunere:** câte texte s-au căutat și
câte iteme n-au text memoizat. Fără cifrele astea, „n-am găsit nimic" nu se deosebește de „n-am
căutat unde trebuia" — exact confuzia care a trimis omul pe internet.
"""
import argparse
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zot                      # noqa: E402
import pdf_text_cache as ptc    # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def _fara_diacritice(s):
    """Text fără semne diacritice, pentru potrivirea îngăduitoare.

    Motivul e măsurat în altă parte a skill-ului: extragerea din PDF-uri românești dă uneori
    „ş" cu sedilă în loc de „ș" cu virgulă, iar cele două sunt caractere diferite. O căutare
    exactă ratează atunci tocmai sursele scanate."""
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if not unicodedata.combining(c))


def texte_memoizate(colectie=None):
    """[(cheie, titlu, text)] pentru tot ce e memoizat, plus atasamentele FARA text.

    **Se citesc notele de cache, intr-o singura interogare.** Varianta care mergea atasament cu
    atasament, prin `get_cached`, n-a terminat in doua minute pe biblioteca reala: ea verifica
    prospetimea fiecarei note, iar verificarea rehashuieste fisierul PDF de pe disc. Pentru o
    cautare care trebuie sa fie mai ieftina decat o cautare pe web, aia e prea scump.

    Prospetimea se verifica **doar pentru sursele care au potrivit** — cateva, nu mii. Vezi
    `cauta`."""
    ro = zot._ro()

    chei_colectie = None
    if colectie:
        gasita = zot.find_collection(colectie)
        if not gasita:
            raise SystemExit("Colectia %r nu exista. Da-i calea sau cheia." % colectie)
        chei_colectie = {r[0] for r in ro.execute(
            "SELECT a.key FROM collectionItems ci "
            "JOIN items p ON p.itemID = ci.itemID "
            "JOIN itemAttachments ia ON ia.parentItemID = p.itemID "
            "JOIN items a ON a.itemID = ia.itemID "
            "WHERE ci.collectionID = ?", (gasita[1],))}

    # titlul si cheia fiecarui atasament PDF, intr-o interogare
    despre = {}
    for cheie, titlu in ro.execute(
            "SELECT a.key, COALESCE(v.value, '(fara titlu)') "
            "FROM itemAttachments ia "
            "JOIN items a ON a.itemID = ia.itemID "
            "LEFT JOIN itemData d ON d.itemID = ia.parentItemID "
            "LEFT JOIN fields f ON f.fieldID = d.fieldID "
            "     AND f.fieldName IN ('title','nameOfAct') "
            "LEFT JOIN itemDataValues v ON v.valueID = d.valueID "
            "WHERE ia.contentType = 'application/pdf'"):
        despre.setdefault(cheie, titlu)

    # Nota de cache se leaga de atasament pe DOUA cai, fiindca asa au ajuns sa stea in baza:
    #   1. antetul `attachment: <cheie>` — forma noua, nota sta sub itemul-parinte;
    #   2. nota sta chiar sub atasament — forma veche; atunci cheia e a parintelui notei.
    # Cele desprinse de Zotero n-au niciuna: raman cautabile, dar fara nume. Vezi `_resolve_note`.
    parinte_al_notei = {}
    for note_id, parent_key in ro.execute(
            "SELECT n.itemID, p.key FROM itemNotes n "
            "LEFT JOIN items p ON p.itemID = n.parentItemID"):
        parinte_al_notei[note_id] = parent_key

    # A TREIA cale, pentru notele pe care Zotero le-a desprins si care n-au nici antet, nici
    # parinte: hash-ul continutului, scris in antetul fiecarei note. `get_cached` o avea de la
    # inceput; fara ea, aici lipseau 22 de texte din 849 — masurat comparand cu rularea veche.
    # Atasamentele LEGATE au `storageHash` NULL in baza, deci nu se regasesc pe calea asta.
    dupa_hash = {}
    for cheie, h in ro.execute(
            "SELECT a.key, ia.storageHash FROM itemAttachments ia "
            "JOIN items a ON a.itemID = ia.itemID "
            "WHERE ia.storageHash IS NOT NULL"):
        dupa_hash.setdefault(h, cheie)

    cu_text = []
    memoizate = set()
    for note_id, nota in ptc._toate_notele_de_cache(ro.execute):
        antet = ptc._parse_header(nota)
        bucati = ptc._parse_cache_note(nota)
        if not antet or not bucati:
            continue
        cheie = antet.get("attachment") or ""
        if not cheie:
            p_cheie = parinte_al_notei.get(note_id)
            if p_cheie in despre:
                cheie = p_cheie
        if not cheie:
            cheie = dupa_hash.get(antet.get("storageHash") or "", "")
        if chei_colectie is not None and (not cheie or cheie not in chei_colectie):
            continue
        if cheie:
            memoizate.add(cheie)
        cu_text.append((cheie or "?",
                        despre.get(cheie, "(notă desprinsă — sursa nu se mai poate numi)"),
                        bucati[1]))

    fara_text = [(k, t) for k, t in despre.items()
                 if k not in memoizate and (chei_colectie is None or k in chei_colectie)]
    return cu_text, fara_text


def cauta(sablon, colectie=None, regex=False, context=300, cate=5, exact=False):
    cu_text, fara_text = texte_memoizate(colectie)
    if regex:
        rx = re.compile(sablon, re.I)
    else:
        ac = sablon if exact else _fara_diacritice(sablon)
        rx = re.compile(re.escape(ac), re.I)

    gasite = 0
    for cheie, titlu, text in cu_text:
        cautat_in = text if (regex or exact) else _fara_diacritice(text)
        potriviri = list(rx.finditer(cautat_in))
        if not potriviri:
            continue
        gasite += 1
        # Prospetimea se verifica DOAR aici, pe sursele care au potrivit: e scumpa, fiindca
        # rehashuieste fisierul, si n-are rost platita pentru mii de surse care nu contin nimic.
        try:
            proaspat = cheie != "?" and ptc.get_cached(cheie) is not None
        except Exception:  # noqa: BLE001
            proaspat = False
        semn = "" if proaspat else "   [ATENTIE: textul memoizat nu mai corespunde fisierului]"
        print("\n%s  [%s]  %d potrivire(i)%s"
              % (titlu[:70], cheie, len(potriviri), semn))
        for m in potriviri[:cate]:
            i, j = max(0, m.start() - context // 2), m.end() + context // 2
            bucata = " ".join(text[i:j].split())
            print("    …%s…" % bucata)
            print("      (poziția %d din %d caractere)" % (m.start(), len(text)))
        if len(potriviri) > cate:
            print("    … încă %d potriviri, netipărite" % (len(potriviri) - cate))

    # Cifrele astea sunt partea care desparte „nu există" de „n-am căutat acolo".
    print("\n%d text(e) memoizate căutate%s; potriviri în %d."
          % (len(cu_text), (" în colecția %r" % colectie) if colectie else "", gasite))
    if fara_text:
        print("%d atașament(e) FĂRĂ text memoizat — acolo căutarea n-avea ce găsi:"
              % len(fara_text))
        for cheie, titlu in fara_text[:10]:
            print("    %s  [%s]" % (titlu[:66], cheie))
        if len(fara_text) > 10:
            print("    … încă %d" % (len(fara_text) - 10))
        print("Se memoizează cu `prefetch_collection.py`, într-un singur ciclu de scriere.")
    if not gasite:
        print("\nZero potriviri. Abia acum are rost căutarea pe web — și numai dacă lista de "
              "mai sus\nnu arată exact sursa în care ar fi trebuit să cauți.")
    return 0 if gasite else 1


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("sablon", help="ce se caută")
    p.add_argument("--colectie", default="", help="restrânge la o colecție (nume, cale sau cheie)")
    p.add_argument("--regex", action="store_true", help="șablonul e expresie regulată")
    p.add_argument("--exact", action="store_true",
                   help="potrivire pe diacriticele exacte (implicit se ignoră)")
    p.add_argument("--context", type=int, default=300, help="câte caractere în jurul potrivirii")
    p.add_argument("--cate", type=int, default=5, help="câte potriviri se tipăresc per sursă")
    a = p.parse_args()
    return cauta(a.sablon, a.colectie or None, a.regex, a.context, a.cate, a.exact)


if __name__ == "__main__":
    sys.exit(main())
