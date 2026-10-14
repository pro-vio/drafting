# -*- coding: utf-8 -*-
"""completeaza_forme.py — completează formele aduse înainte de regula din 12.10.2026 cu decizia care
le-a produs și cu perioada în care s-au aplicat.

Regula (`citari-zotero.md` §G): o formă consolidată se numește prin actele de modificare care au
produs-o, fiecare cu Monitorul Oficial al lui, iar când un text citează mai multe forme ale aceluiași
act, și prin perioada în care forma s-a aplicat. `adu_lege.py` scrie de atunci în `extra` rândurile
„Modificată prin: …" și „Aplicabilă: …"; itemii aduși înainte nu le au, deci lista de legislație le
afișează cu „versiune consolidată la …", iar textul nu le poate cita după actul de modificare.

Pentru fiecare item `statute` din colecție, cu rând de versiune și fără „Modificată prin":

- **data formei**, din rândul de versiune („Versiune consolidată la 01.01.2024" sau „… cu modificări
  până la 19.09.2023"); „Forma publicată" n-are modificări și se sare;
- **actele de modificare**, din notele de modificare ale textului formei care poartă data ei
  (`adu_lege.acte_de_modificare`). Textul se ia din cache întâi; la miss, din PDF — și se spune;
- **perioada**, din istoricul complet al consolidărilor actului de pe Portal
  (`adu_lege.consolidari_complete`), citit o singură dată pe act.

Implicit doar arată ce ar scrie. `--amana` lasă scrierea în `scrieri_amanate` (o face primul ciclu
de închidere); scrierea cere aprobarea autorului, ca orice corecție de câmp.

    python completeaza_forme.py --colectie 2FV5KZUX            # proba: ce ar scrie
    python completeaza_forme.py --colectie 2FV5KZUX --amana    # lasă scrierea în coadă
"""
import argparse, datetime, os, re, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
AICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AICI)
import zot  # noqa: E402
import adu_lege  # noqa: E402
import pdf_text_cache as ptc  # noqa: E402
import verifica_acte  # noqa: E402

VERS = re.compile(r"^(?:Versiune consolidată la|Versiune de pe pagina actului de bază, cu modificări până la) "
                  r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})", re.M)


def _text(cheie_item):
    """(text, de_unde, (atasament, cale) de memoizat sau None) pentru primul PDF al itemului.

    Cache întâi; la miss se extrage din PDF, iar memoizarea se lasă în coadă (regula 21: o extragere
    care nu lasă nimic în urmă lasă următoarea sesiune în același punct)."""
    con = zot._ro()
    try:
        r = con.execute("SELECT a.key FROM itemAttachments ia JOIN items a ON a.itemID=ia.itemID JOIN items p "
                        "ON p.itemID=ia.parentItemID WHERE p.key=? AND ia.contentType='application/pdf' "
                        "AND a.itemID NOT IN (SELECT itemID FROM deletedItems)", (cheie_item,)).fetchone()
    finally:
        con.close()
    if not r:
        return None, "fără PDF", None
    cale = zot.attachment_path(r[0])
    if not cale or not os.path.exists(cale):
        return None, "PDF lipsă pe disc (%s)" % r[0], None
    t = ptc.get_cached(r[0])
    if t:
        return t, "cache", None
    return ptc.extract_pdf_text(cale), "PDF, memoizare lăsată în coadă", (r[0], cale)


NOTA_DATA = re.compile(r"\(la (\d{2})-(\d{2})-(\d{4}),")


def din_text(text):
    """Ce spune fișierul despre el însuși: antetul scris de `adu_lege` (denumirea, „text cu modificări
    până la …", paginile Portalului) și notele de modificare (data cea mai nouă)."""
    t = re.sub(r"\s+", " ", re.sub(r"-{3} page \d+ -{3}", " ", (text or "").replace("­", "-")))
    # Antetul scris de `adu_lege` se încheie cu o linie de „=". Paginile se citesc numai de acolo: în
    # corpul unui act de modificare stau legături spre actele pe care le modifică, iar luate drept
    # paginile fișierului au dat o alarmă falsă (Ordinul 4.440/2024, verificat de mână 12.10.2026).
    sep = t.find("=====")
    cap = t[:sep] if 0 < sep < 1500 else t[:300]
    if "Preluat" not in cap:
        # Fără antetul uneltei (PDF-ul oficial al actului, de pildă) nu există „paginile fișierului".
        cap_pagini = ""
    else:
        cap_pagini = cap
    pana = re.search(r"modificări până la (\d{4})-?(\d{2})-?(\d{2})", cap)
    note = sorted("%s.%s.%s" % (m.group(1), m.group(2), m.group(3)) for m in NOTA_DATA.finditer(t))
    note_max = max(note, key=adu_lege._cheie_data) if note else ""
    return {"titlu": cap.split(" FORMA ")[0][:160].strip(),
            "pana_la": "%s.%s.%s" % (pana.group(3), pana.group(2), pana.group(1)) if pana else "",
            "pagini": re.findall(r"DetaliiDocument(?:Afis)?/(\d+)", cap_pagini),
            "pagini_afis": re.findall(r"DetaliiDocumentAfis/(\d+)", cap_pagini),
            "publicata": bool(re.search(r"PUBLICAT[ĂA] [ÎI]N:? MONITORUL OFICIAL", cap)) and not note,
            "nota_max": note_max, "note": len(note)}


def _baza(f, istorii):
    """Istoricul complet al actului, citit o dată pe tip și cod (o lege și o hotărâre pot avea același
    număr: cu cheia doar pe cod, hotărârea primea istoricul legii, 12.10.2026). Pagina de bază se caută pe
    Portal după tip și cod."""
    cod = f.get("codeNumber") or ""
    tip = adu_lege.tip_act(f.get("nameOfAct")) or ""
    if (tip, cod) in istorii:
        return istorii[(tip, cod)]
    cerere = {"OUG": "OUG", "OG": "OG", "HG": "HG", "Legea": "Legea", "Ordinul": "Ordinul",
              "Decretul": "Decretul"}.get(tip, tip)
    lista = None
    ident = None
    try:
        _, _, _, gasite = adu_lege.cauta_act("%s %s" % (cerere, cod))
        # Căutarea întoarce și republicarea („LEGE (R) 448"), și acte de alt tip cu același număr
        # („HOTARARE 52" alături de „HG 52"): se ține actul de bază, al anului din cod, nerepublicat.
        an = cod.split("/")[-1]
        bune = [g for g in gasite if "(R)" not in g[1] and g[1].strip().endswith(an)]
        if len(bune) > 1 and tip:
            bune = [g for g in bune if adu_lege._ascii(g[1]).startswith(adu_lege._ascii(cerere)[:2])] or bune
        if len(bune) == 1:
            ident = bune[0][0]
    except BaseException as e:  # noqa: BLE001 — `cauta_act` refuză cu SystemExit un tip pe care nu-l știe
        if isinstance(e, KeyboardInterrupt):
            raise
    if ident is None:
        # Pagina formei din item e tot a actului: istoricul ei se poate citi la fel.
        m = re.search(r"DetaliiDocument(?:Afis)?/(\d+)", f.get("url") or "")
        ident = m.group(1) if m else None
    try:
        if ident:
            lista = adu_lege.consolidari_complete(adu_lege.ia(adu_lege.PORTAL % ident), ident)
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, KeyboardInterrupt):
            raise
        print("  %s: istoricul nu s-a putut citi (%s)" % (cod, str(e).split("\n")[0][:120]))
    istorii[(tip, cod)] = lista
    return lista


# Rândul se scoate întreg: fără `.*$`, rămâneau fragmentele de după prefix (rateul din 12.10.2026).
FARA_FORMA = re.compile(r"^(Modificată prin:|Aplicabilă).*$", re.M)
# La corectură se rescriu toate rândurile care descriu forma, nu doar cele adăugate.
FARA_TOT = re.compile(r"^(Modificată prin:|Aplicabilă|Versiune |Forma publicată|Metadate verificate pe text:).*$", re.M)


def _pagina_formei(T, data, lista=None):
    """Pagina de pe Portal a formei din fișier: întâi din istoricul consolidărilor actului (data → pagină),
    apoi dintre paginile din antet (întâi cele `Afis`, de pe care vine textul) — cea care are selectată
    consolidarea cu data `data`. "" dacă niciuna — se spune."""
    for d, ident in (lista or []):
        if d == data:
            return adu_lege.PORTAL % ident
    cap_ids = T.get("pagini_afis", []) + [p for p in T["pagini"] if p not in T.get("pagini_afis", [])]
    for ident in dict.fromkeys(cap_ids):
        try:
            if adu_lege.antet_act(adu_lege.ia(adu_lege.PORTAL % ident)).get("consolidare") == data:
                return adu_lege.PORTAL % ident
        except Exception:  # noqa: BLE001
            continue
    return ""


def corectura(f, extra, text, T, istorii):
    """Itemul rescris după ce conține fișierul (decizia autorului, 12.10.2026: la o nepotrivire se
    schimbă itemul, nu fișierul). Întoarce {"extra", "url", "note"} sau {"note"} când nu se poate."""
    emitent = (extra.split("\n")[0] if extra and not extra.startswith(("Versiune", "Forma", "Modificată")) else "")
    rest = [l for l in FARA_TOT.sub("", "\n".join(extra.split("\n")[1:] if emitent else extra.split("\n"))).split("\n")
            if l.strip()]
    azi = datetime.date.today().isoformat()
    pag = re.search(r"DetaliiDocument(?:Afis)?/(\d+)", f.get("url") or "")
    if pag and T["pagini"] and pag.group(1) not in T["pagini"]:
        # Pagina itemului nu e printre cele din care s-a compus fișierul: fișierul poate fi al altui
        # act sau al altei forme. Nu se rescrie itemul pe o presupunere; se verifică de mână.
        return {"note": "url-ul itemului nu e printre paginile fișierului (%s): fișierul poate fi al altui act; "
                        "de verificat, nu corectez" % ", ".join(dict.fromkeys(T["pagini"]))}
    if not T["note"]:
        # Niciun marcaj de modificare: fișierul poartă textul publicat.
        linii = ["Forma publicată, fără modificări ulterioare, Portal Legislativ (Ministerul Justiției)"]
        return {"extra": "\n".join([x for x in [emitent] + rest + linii if x] + ["%s %s" % (MARCAJ, azi)]),
                "url": None, "note": "fișierul n-are note de modificare: e forma publicată"}
    data_text = T["pana_la"] or T["nota_max"]
    lista = _baza(f, istorii)
    # Data din text e a ultimei modificări încorporate; forma e PRIMA consolidare de la ea încolo.
    # Consolidarea poate întârzia o zi față de aplicarea modificării: măsurat pe Legea 196/2016, forma
    # din 02.04.2023 are ultima notă din 01.04.2023, iar consolidarea dinainte e din 31.03.2023.
    data = forma_textului(lista, data_text) or data_text
    acte = (adu_lege.acte_de_modificare(text, data_text) + [a for a in adu_lege.acte_de_modificare(text, data)
            if a not in adu_lege.acte_de_modificare(text, data_text)]) if data != data_text else \
        adu_lege.acte_de_modificare(text, data)
    pana = adu_lege.sfarsitul_formei(lista, data) if lista else ""
    linii = ["Versiune consolidată la %s, Portal Legislativ (Ministerul Justiției)" % data]
    linii += list(adu_lege.linii_forma({"acte": acte, "data_c": data, "pana_la": pana}))
    url = _pagina_formei(T, data, lista)
    if not url:
        # Fără pagina formei, itemul ar rămâne cu url-ul formei declarate greșit: o contradicție nouă.
        return {"note": "forma din fișier: %s, dar pagina ei de pe Portal nu s-a găsit; nu corectez, "
                        "itemul se aduce din nou cu adu_lege --la-data" % data}
    note = "forma din fișier: %s" % data
    return {"extra": "\n".join([x for x in [emitent] + rest + linii if x] + ["%s %s" % (MARCAJ, azi)]),
            "url": url or None, "note": note}
MARCAJ = "Metadate verificate pe text:"     # același cu `verifica_metadate.MARCAJ`


def forma_textului(lista, data_text):
    """Prima consolidare cu data ≥ ultima notă de modificare din text (`zz.ll.aaaa`), sau ""."""
    k = adu_lege._cheie_data
    dupa = sorted((d for d, _ in (lista or []) if k(d) >= k(data_text)), key=k)
    return dupa[0] if dupa else ""


def declarata_se_potriveste(lista, declarata, data_text):
    """Forma declarată D se potrivește cu textul dacă ultima notă cade după consolidarea dinaintea lui D
    și nu după D: (prev(D), D]. Fără istoric, se cere egalitatea, ca înainte."""
    k = adu_lege._cheie_data
    if declarata == data_text:
        return True
    if not lista or k(data_text) > k(declarata):
        return False
    inainte = [d for d, _ in lista if k(d) < k(declarata)]
    return not inainte or k(data_text) > k(max(inainte, key=k))


def verifica(colectie, chei=None):
    """Verifică metadatele fiecărui act din colecție față de textul fișierului lui și propune ce lipsește.

    Cererea autorului, 12.10.2026: metadatele se verifică din text la TOATE actele; unde lipsesc,
    se citește textul, se memoizează și se extrag de acolo. Fișierul spune ce formă conține — antetul
    lui și notele de modificare —, deci el decide când itemul declară altceva.

    Întoarce [dict]: cheie, stare („ok", „completare", „nepotrivire", „fără text"), constatari,
    extra_nou (sau None), memo ((atasament, cale) sau None)."""
    out, istorii = [], {}
    for x in [y for y in verifica_acte.acte(colectie) if not chei or y["cheie"] in chei]:
        f = x["campuri"]
        extra = f.get("extra") or ""
        text, de_unde, memo = _text(x["cheie"])
        r = {"cheie": x["cheie"], "extra": extra, "nume": f.get("shortTitle") or f.get("nameOfAct", "")[:50], "memo": memo,
             "de_unde": de_unde, "constatari": [], "extra_nou": None, "stare": "ok", "corectura": None}
        out.append(r)
        if not text:
            r["stare"], r["constatari"] = "fără text", [de_unde]
            continue
        T = din_text(text)
        m = VERS.search(extra)
        declarata = adu_lege._zz(m.group(1)) if m else ""
        publicata = "Forma publicată" in extra
        in_fisier = T["pana_la"] or T["nota_max"]
        # Portalul trece în text și modificările cu aplicare ulterioară („la 01-01-2025, … completat de" pe
        # pagina formei din 12.08.2024), deci o notă mai nouă decât forma nu dovedește altă formă. Când pagina
        # din item e în antetul fișierului și are selectată chiar forma declarată, fișierul e forma declarată.
        pag_item = re.search(r"DetaliiDocument(?:Afis)?/(\d+)", f.get("url") or "")
        if declarata and in_fisier and adu_lege._cheie_data(in_fisier) > adu_lege._cheie_data(declarata) \
                and pag_item and pag_item.group(1) in T["pagini"]:
            try:
                if adu_lege.antet_act(adu_lege.ia(f["url"])).get("consolidare") == declarata:
                    in_fisier = declarata
            except Exception:  # noqa: BLE001
                pass
        # 1. forma declarată față de forma din fișier
        if declarata and in_fisier and declarata != in_fisier and \
                not declarata_se_potriveste(_baza(f, istorii), declarata, in_fisier):
            r["constatari"].append("itemul declară forma din %s, fișierul conține forma cu modificări până la %s"
                                   % (declarata, in_fisier))
        if publicata and T["note"]:
            r["constatari"].append("itemul declară forma publicată, fișierul are %d note de modificare (ultima %s)"
                                   % (T["note"], T["nota_max"]))
        # 2. pagina formei față de paginile din antet
        pag = re.search(r"DetaliiDocument(?:Afis)?/(\d+)", f.get("url") or "")
        # Pagina formei din istoric are alt id decât paginile de afișare din antet (iar istoricul citit
        # depinde de pagina de pornire). E aceeași formă când forma declarată se potrivește cu fișierul
        # (punctul 1 n-a găsit nimic) și pagina din item are selectată chiar forma declarată.
        if pag and T["pagini"] and pag.group(1) not in T["pagini"] and declarata and not r["constatari"]:
            try:
                if adu_lege.antet_act(adu_lege.ia(f["url"])).get("consolidare") == declarata:
                    pag = None
            except Exception:  # noqa: BLE001
                pass
        if pag and T["pagini"] and pag.group(1) not in T["pagini"]:
            r["constatari"].append("url-ul itemului (%s) nu e printre paginile din antetul fișierului (%s)"
                                   % (pag.group(1), ", ".join(T["pagini"])))
        # Pagina din antetul fișierului trebuie să fie a actului din item: aceeași dată de adoptare. Antetul îl scrie
        # unealta, cu denumirea din item, deci nu dovedește nimic; textul de sub el poate fi al altui act (OMFP
        # 925/2017 purta textul Ordinului MAI 137/2018, venit dintr-un istoric amestecat; 13.10.2026).
        if T["pagini"] and (f.get("dateEnacted") or "")[:10]:
            try:
                d_pag = adu_lege.antet_act(adu_lege.ia(adu_lege.PORTAL % T["pagini"][0])).get("data")
                if d_pag and d_pag[0] != f["dateEnacted"][:10]:
                    r["constatari"].append("pagina fișierului (%s) e a unui act adoptat la %s, iar itemul e adoptat la %s: "
                                           "textul e al altui act" % (T["pagini"][0], d_pag[0], f["dateEnacted"][:10]))
            except Exception:  # noqa: BLE001
                pass
        if r["constatari"]:
            r["stare"] = "nepotrivire"
            r["corectura"] = corectura(f, extra, text, T, istorii)
            continue
        if publicata or not (declarata or in_fisier):
            continue                      # forma publicată: nimic de adăugat
        data = declarata or in_fisier
        # Consolidarea poate întârzia față de aplicarea modificării (forma din 02.04.2023, notă din
        # 01.04.2023): actele se iau de la data formei, altfel de la ultima notă din text.
        acte = adu_lege.acte_de_modificare(text, data) or (
            adu_lege.acte_de_modificare(text, in_fisier) if in_fisier and in_fisier != data else [])
        if not acte and not T["note"] and pag_item and pag_item.group(1) in T["pagini"]:
            # La actele vechi Portalul integrează modificările fără note „(la …)" (Decretul 92/1950 în forma din
            # 31.12.1955 are art. 11^1, absent din forma publicată, și nicio notă; 13.10.2026). Atunci dovada formei
            # e pagina: fișierul vine chiar din pagina din item, iar pagina are selectată forma declarată.
            try:
                if adu_lege.antet_act(adu_lege.ia(f["url"])).get("consolidare") in (data, ""):
                    pagina = adu_lege.ia(f["url"])
                    sel = adu_lege.antet_act(pagina).get("consolidare") or (
                        (adu_lege.consolidari_complete(pagina, pag_item.group(1)) or [("", "")])[0][0])
                    if sel == data:
                        continue          # forma confirmată de pagină; fără note, nu e nimic de completat
            except Exception:  # noqa: BLE001
                pass
        if not acte:
            # Nicio notă nu poartă chiar data formei (consolidarea datată altfel decât aplicarea modificării, ori o
            # notă cu aplicare amânată), dar fișierul e forma declarată: atunci itemul e în regulă, doar fără actul
            # care a produs forma (Legea 69/2000, formele din 15.12.2023 și 31.12.2024; 13.10.2026).
            co = corectura(f, extra, text, T, istorii)
            if co.get("extra") and ("Versiune consolidată la %s" % data) in co["extra"]:
                continue
            r["stare"] = "nepotrivire"
            r["constatari"].append("textul n-are nicio notă de modificare cu data formei (%s); ultima notă e din %s"
                                   % (data, T["nota_max"] or "—"))
            r["corectura"] = corectura(f, extra, text, T, istorii)
            continue
        lista = _baza(f, istorii)
        pana = adu_lege.sfarsitul_formei(lista, data) if lista else ""
        linii = adu_lege.linii_forma({"acte": acte, "data_c": data, "pana_la": pana})
        if not lista:
            linii = linii[:1] + ("Aplicabilă de la %s" % data,)
            r["constatari"].append("istoricul Portalului n-a putut fi citit: perioada are doar începutul")
        vechi = FARA_FORMA.sub("", extra)
        # Marcajul de verificare rămâne ultimul rând: altfel un item deja complet ar ieși „de completat".
        marcaj = [l for l in vechi.split("\n") if l.startswith(MARCAJ)]
        vechi = "\n".join(l for l in vechi.split("\n") if l.strip() and not l.startswith(MARCAJ))
        nou = "\n".join([vechi] + list(linii) + marcaj)
        if nou.strip() != extra.strip():
            r["stare"], r["extra_nou"] = "completare", nou
    return out




def main():
    p = argparse.ArgumentParser()
    p.add_argument("--colectie", action="append", default=[], help="nume, cale sau cheie; se poate repeta")
    p.add_argument("--toata-biblioteca", action="store_true", help="toate actele normative din bibliotecă")
    p.add_argument("--amana", action="store_true",
                   help="lasă în scrieri_amanate completările și memoizarea; nepotrivirile NU se scriu")
    p.add_argument("--fara", action="append", default=[], metavar="CHEIE",
                   help="un item lăsat deoparte (de pildă unul la care proba arată ceva de verificat)")
    p.add_argument("--chei", nargs="*", default=None, help="numai itemii aceștia (în colecție sau în bibliotecă)")
    p.add_argument("--corecteaza", action="store_true",
                   help="la nepotriviri, rescrie itemul după ce conține fișierul (decizia autorului, 12.10.2026)")
    p.add_argument("--doar-nepotriviri", action="store_true",
                   help="pune în coadă numai corecturile nepotrivirilor (completările au fost lăsate deja)")
    a = p.parse_args()
    if not a.colectie and not a.toata_biblioteca:
        raise SystemExit("Dă --colectie sau --toata-biblioteca.")
    ops, stari = [], {}
    for c in (a.colectie or [None]):
        print("== %s" % (c or "toată biblioteca"))
        for r in verifica(c, a.chei):
            if r["cheie"] in a.fara:
                print("  LĂSAT DEOPARTE %s (--fara)" % r["cheie"])
                continue
            stari[r["stare"]] = stari.get(r["stare"], 0) + 1
            if r["memo"] and not a.doar_nepotriviri:
                ops.append({"op": "ensure_cached", "atasament": r["memo"][0], "cale": r["memo"][1]})
            eticheta = {"ok": "OK", "completare": "COMPLETARE", "nepotrivire": "NEPOTRIVIRE",
                        "fără text": "FĂRĂ TEXT"}[r["stare"]]
            print("  %-11s %s %s (text: %s)" % (eticheta, r["cheie"], r["nume"][:40], r["de_unde"]))
            for x in r["constatari"]:
                print("      ! %s" % x)
            if r["extra_nou"]:
                for l in r["extra_nou"].split("\n"):
                    if l.startswith(("Modificată prin:", "Aplicabilă")):
                        print("      + %s" % l[:220])
            if r["stare"] in ("ok", "completare") and not a.doar_nepotriviri:
                # Metadatele s-au verificat pe text: marcajul după care citarea recunoaște itemul
                # (`build_manuscris.py`, regula din 12.10.2026).
                baza = r["extra_nou"] or r["extra"]
                if MARCAJ not in baza:
                    baza = baza.rstrip("\n") + "\n%s %s" % (MARCAJ, datetime.date.today().isoformat())
                if baza != r["extra"]:
                    ops.append({"op": "set_field", "item": r["cheie"], "camp": "extra", "valoare": baza})
            co = r.get("corectura")
            if co:
                print("      → corectură (itemul după fișier): %s" % co["note"])
                if "extra" in co:
                    for l in co["extra"].split("\n"):
                        print("        | %s" % l[:200])
                if co.get("url"):
                    print("        | url: %s" % co["url"])
                if a.corecteaza and "extra" in co:
                    ops.append({"op": "set_field", "item": r["cheie"], "camp": "extra", "valoare": co["extra"]})
                    if co.get("url"):
                        ops.append({"op": "set_field", "item": r["cheie"], "camp": "url", "valoare": co["url"]})
    print("\n" + " | ".join("%s: %d" % kv for kv in sorted(stari.items())))
    if not a.corecteaza:
        print("Nepotrivirile nu se corectează fără `--corecteaza`: autorul hotărăște dacă se schimbă itemul "
              "după fișier (corectura de mai sus) sau se aduce fișierul formei declarate.")
    if a.amana and ops:
        import scrieri_amanate as sa
        print("Lăsat în scrieri amânate (%d operații): %s" % (len(ops), sa.lasa(
            "verificarea formelor din text (%s)" % (", ".join(a.colectie) or "toată biblioteca"), ops, cine="completeaza_forme.py")))
    elif ops:
        print("(probă: n-am scris nimic; `--amana` lasă completările și memoizarea pentru primul ciclu)")


if __name__ == "__main__":
    main()
