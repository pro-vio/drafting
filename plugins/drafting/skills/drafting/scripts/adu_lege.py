# -*- coding: utf-8 -*-
"""Aduce un act normativ de pe Portalul Legislativ, în forma în vigoare, și îl așază în Zotero.

    python adu_lege.py 73527 --denumire "Legea nr. 273/2006 privind finanțele publice locale" \
        --cod 273/2006 --scurt "Legea 273/2006" --emitent "Parlamentul României"

Se dă id-ul paginii actului de bază. Scriptul găsește singur ultima consolidare, o descarcă
pe aceea (nu pagina de bază -- vezi mai jos), o așază într-un PDF cu strat de text și creează
itemul `statute` cu atașamentul.

Capcana, măsurată pe două acte: portalul are DOUĂ adrese pentru același document, iar ele
servesc forme diferite.

    /Public/DetaliiDocument/<id>       forma CONSOLIDATĂ, cu marcajele „(la ZZ-LL-AAAA, …
                                       a fost modificat de …)"
    /Public/DetaliiDocumentAfis/<id>   forma PUBLICATĂ INIȚIAL, fără marcaje

Diferența e mare și tăcută: Legea 24/2000 are 86 de articole pe prima adresă și 81 pe a doua;
Legea 273/2006, 133 față de 86. Adresa `Afis` arată plauzibil și nu spune nicăieri că e forma
veche. **Se pornește întotdeauna de la prima** — scriptul o face. La actele mari, prima poate
fi doar un antet, cu textul pe o pagină `Afis` cu alt id, legată din ea; acolo `Afis` poartă
forma consolidată. `forma()` urmează legătura.

Peste asta, scriptul compară pagina actului cu ultima consolidare din istoric și o alege pe cea
care poartă modificarea cea mai recentă: lista de consolidări poate rămâne în urmă (la Legea
273/2006, pagina actului avea o modificare din 25.02.2026 pe care consolidarea listată la
01.01.2026 n-o avea).
"""
import os, re, sys, html, argparse, urllib.request, urllib.error
import io, json, time, sqlite3
sys.stdout.reconfigure(encoding="utf-8")

# Scriptul stă în `drafting` din 30 septembrie 2026, lângă `zot` și `pdf_text_cache`. Până atunci
# stătea în skill-ul de proiect `hcl-asemt`, iar șase proiecte fără legătură cu hotărârile de
# consiliu local ajungeau la el pe calea aceea — unealta era generală, locul părea particular, și o
# sesiune nouă n-o găsea din descrieri. Calea veche rămâne o trimitere care îl cheamă pe acesta.
#
# `zot` și `pdf_text_cache` vin din `drafting` instalat, și numai de acolo — chiar dacă scriptul e
# copiat și rulat din alt dosar, lângă un `zot.py` vechi. După import se verifică de unde a venit
# modulul: o poziție în listă nu apără de un `zot` încărcat deja din altă parte, iar o copie veche a
# lui ar scrie în bază prin alt cod.
DRAFTING = os.path.join(os.path.expanduser("~"), ".claude", "skills", "drafting", "scripts")
sys.path[:] = [DRAFTING] + [p for p in sys.path
                            if os.path.normcase(os.path.abspath(p or ".")) != os.path.normcase(DRAFTING)]
import zot
import pdf_text_cache
for _m in (zot, pdf_text_cache):
    if (os.path.normcase(os.path.realpath(os.path.dirname(os.path.abspath(_m.__file__))))
            != os.path.normcase(os.path.realpath(DRAFTING))):
        raise SystemExit("Modulul %s s-a încărcat din %s, nu din %s.\nO copie locală ar scrie în "
                         "bază prin alt cod decât cel din `drafting`; mut-o sau șterge-o."
                         % (_m.__name__, _m.__file__, DRAFTING))
import fitz

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"}
TTF = r"C:\Windows\Fonts\arial.ttf"
PORTAL = "https://legislatie.just.ro/Public/DetaliiDocument/%s"


# Secunde de asteptare la prima incercare; la fiecare reluare se adauga inca o data atat.
# Se schimba cu --timeout. Codul fiscal nu incape uneori in doua minute (masurat pe 9.10.2026).
TIMEOUT = 180


def ia(url, incercari=3, pauza=3.0, timeout=None):
    """Pagina de pe portal, ca text. Reia la erorile 5xx ale serverului, la expirarea citirii si la
    caderea conexiunii; timpul de asteptare creste la fiecare incercare (180, 360, 540 s implicit).

    Masurat pe 16 septembrie 2026: la paginile mari — Codul fiscal are 7,5 milioane de caractere —
    prima cerere cade din cand in cand cu 500, iar a doua reuseste. Pe 9 octombrie 2026, aceeasi
    pagina a cazut de doua ori cu „The read operation timed out" la 120 s: expirarea nu e
    `HTTPError`, deci nu se relua si omora tot lotul. Erorile 4xx nu se reiau: acolo raspunsul nu
    se schimba prin repetare."""
    baza = timeout or TIMEOUT
    for i in range(incercari):
        try:
            cerere = urllib.request.Request(url, headers=UA)
            return urllib.request.urlopen(cerere, timeout=baza * (i + 1)).read().decode("utf8", "replace")
        except urllib.error.HTTPError as e:
            if e.code < 500 or i == incercari - 1:
                raise
            motiv = "portalul a raspuns %d" % e.code
        except (TimeoutError, ConnectionError, urllib.error.URLError) as e:
            if i == incercari - 1:
                raise
            motiv = "citirea a cazut (%s)" % (getattr(e, "reason", None) or e)
        print("  %s; reiau peste %.0f s, cu %d s de asteptare (incercarea %d din %d)"
              % (motiv, pauza, baza * (i + 2), i + 2, incercari))
        time.sleep(pauza)


ABREVIERI = {"legea": "LEGE", "lege": "LEGE", "oug": "ORDONANȚĂ DE URGENȚĂ",
             "og": "ORDONANȚĂ", "ordonanta": "ORDONANȚĂ", "ordonanța": "ORDONANȚĂ",
             "hg": "HOTĂRÂRE", "hotararea": "HOTĂRÂRE", "hotărârea": "HOTĂRÂRE",
             "ordin": "ORDIN", "ordinul": "ORDIN", "decizia": "DECIZIE", "codul": "COD",
             "decretul": "DECRET", "decret": "DECRET", "decretul-lege": "DECRET-LEGE"}


def _fara_diacritice(s):
    for a, b in (("ăâ", "a"), ("î", "i"), ("șş", "s"), ("țţ", "t")):
        for ch in a:
            s = s.replace(ch, b).replace(ch.upper(), b.upper())
    return s


def _tipuri_de_act():
    """{nume normalizat: cod} pentru selectul de tipuri al portalului. Se citește, nu se scrie aici:
    lista are 172 de intrări și se schimbă fără să ne întrebe.

    Numele se dezescapează înainte de normalizare. Portalul scrie HOTĂRÂRE cu entitate HTML
    (`HOTĂR&#194;RE`), iar celelalte tipuri cu diacriticele ca octeți. Fără `html.unescape`,
    cheia devenea `HOTAR&#194;RE` și „HG 412/2025" era refuzat ca tip necunoscut, deși e chiar
    exemplul din mesajul de ajutor (măsurat pe 8 octombrie 2026)."""
    acasa = ia("https://legislatie.just.ro/")
    bloc = re.search(r'<select[^>]*name="DocumentType".*?</select>', acasa, re.S)
    if not bloc:
        raise SystemExit("Nu găsesc lista tipurilor de act pe pagina portalului.")
    return {_fara_diacritice(html.unescape(n)).strip().upper(): (c, html.unescape(n).strip())
            for c, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]*)<', bloc.group(0))}


def cauta_act(cerere, pagini=60):
    """Id-urile paginilor care se potrivesc cu „Legea 207/2015", „OUG 57/2019", „HG 1156/2013".

    Există fiindcă unealta cerea id-ul paginii, iar id-ul nu se poate ghici: un agent a încercat
    unul greșit, a adus alt act și, pentru Codul de procedură fiscală, actul a rămas neadus
    (16 septembrie 2026). Căutarea portalului filtrează după tip și număr, dar NU după an, deci
    anul se alege aici, din data semnării scrisă în fiecare rând.

    Paginile se citesc până se termină rezultatele (cel mult `pagini`), nu doar primele opt: la numerele
    mici portalul întoarce zeci de hotărâri cu același număr — ale consiliilor locale, ale altor
    autorități —, iar cea a Guvernului cădea dincolo de pagina a opta (HG 30/2019 și HG 1/2016,
    12.10.2026). O eroare a portalului se spune ca atare, cu ce s-a găsit până la ea, nu ca traceback
    (căutarea ordonanțelor de urgență a răspuns HTTP 500 peste o oră, în aceeași zi)."""
    m = re.match(r"\s*([A-Za-zĂÂÎȘȚăâîșț\.]+)\s*(?:nr\.?)?\s*([\d.]+)\s*/\s*(\d{4})\s*$", cerere)
    if not m:
        raise SystemExit('Scrie căutarea ca „Legea 207/2015", „OUG 57/2019" sau „HG 1156/2013".')
    fel, numar, an = m.group(1).lower().rstrip("."), m.group(2).replace(".", ""), m.group(3)
    # Numele tipului se normalizează pe amândouă părțile: abrevierile sunt scrise cu diacritice,
    # iar cheile citite din portal sunt fără ele.
    nume = _fara_diacritice(ABREVIERI.get(_fara_diacritice(fel), fel)).upper()
    tipuri = _tipuri_de_act()
    if nume not in tipuri:
        apropiate = [t for t in tipuri if nume.split()[0] in t][:6]
        raise SystemExit("Nu cunosc tipul %r. Tipuri apropiate din portal: %s"
                         % (fel, ", ".join(apropiate) or "niciunul"))

    cod_tip, nume_portal = tipuri[nume]
    gasite, vazute = [], set()
    for pagina in range(1, pagini + 1):
        url = ("https://legislatie.just.ro/Public/RezultateCautare?tipdoc=%s&numardoc=%s&page=%d"
               % (cod_tip, numar, pagina))
        try:
            text = ia(url)
        except Exception as e:  # noqa: BLE001
            mesaj = ("Portalul a refuzat căutarea pentru %s nr. %s, la pagina %d de rezultate (%s)."
                     % (nume_portal, numar, pagina, str(e).splitlines()[0][:120] if str(e) else type(e).__name__))
            if gasite:
                print("  ATENȚIE: " + mesaj + " Arăt ce s-a găsit până acolo.")
                break
            raise SystemExit(mesaj + "\nE o eroare a portalului, nu a cererii: tipul și numărul pot fi corecte. "
                             "Reîncearcă mai târziu, sau dă direct id-ul paginii actului (din adresa "
                             "legislatie.just.ro/Public/DetaliiDocument/<id>).")
        randuri = re.findall(r'href="/Public/DetaliiDocument/(\d+)">\s*\d+\.\s*([^<]*?)\s*</a>', text)
        if not randuri:
            break
        for ident, titlu in randuri:
            titlu = " ".join(titlu.split())
            if ident in vazute:
                continue
            vazute.add(ident)
            if titlu.endswith("/" + an) or ("/" + an) in titlu:
                gasite.append((ident, titlu))
    return nume_portal, numar, an, gasite


def text_din(fragment):
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", fragment)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def articole(pagina):
    """Textul actului, articol cu articol, din marcajele id_art…_ttl / _bdy."""
    marc = [(m.start(), m.group(1)) for m in re.finditer(
        r'<[^>]*id="id_(?:cap|art|sec|tit|anx)[A-Za-z0-9_]*?(_ttl|_bdy)"[^>]*>', pagina)]
    out = []
    for i, (poz, fel) in enumerate(marc):
        sf = marc[i + 1][0] if i + 1 < len(marc) else len(pagina)
        t = text_din(pagina[poz:sf])
        if t:
            out.append((fel, t))
    return out


AFIS = "https://legislatie.just.ro/Public/DetaliiDocumentAfis/%s"


def forma(ident, h=None):
    """(fragmente, surse): textul complet al formei de pe pagina `DetaliiDocument/<ident>`.

    La actele mari, pagina nu poartă tot textul, iar `articole()` singur întorcea prea puțin.
    Măsurat pe 7 octombrie 2026, două tipare:

    - **Pagina e doar antet.** Codul de procedură fiscală: consolidarea 309358 n-are niciun
      marcaj de articol; textul, 430 de articole cu modificări până la 08-05-2026, stă pe
      `DetaliiDocumentAfis/309359`, legat din titlul din antet (`S_HDR`). Pagina de bază 170005
      trimite la fel, la 312603. Se urmează legătura din antet, dacă e una singură.
    - **Anexa e doar o legătură.** HG 1/2016: pagina are cele patru articole ale hotărârii, iar
      anexa — normele de aplicare, grosul actului — e un titlu care trimite la altă pagină
      `Afis` (306834 din consolidarea 306833, 307847 din pagina de bază 174822). Textul anexei
      se aduce și se pune la locul ei, după titlul ei.

    Legătura NU e „id + 1": la pagina de bază, id-ul următor e alt act. Se urmează doar
    legăturile din pagină.

    Paginile `Afis` urmate aici poartă forma consolidată, cu marcajele „(la ZZ-LL-AAAA" — altfel
    decât `Afis` pe id-ul actului de bază, care dă forma inițială (vezi antetul modulului).

    `fragmente` sunt perechi `(fel, text)`, iar textul vine gata trecut prin `text_din`. Trecut
    încă o dată, crapă cu „expected string or bytes-like object"."""
    surse = [PORTAL % ident]
    if h is None:
        h = ia(PORTAL % ident)
    if not re.search(r'id="id_art[A-Za-z0-9]*_(?:ttl|bdy)"', h):
        # Antetul se caută ÎN blocul formei afișate (`div_Formaconsolidata`), nu în toată pagina:
        # pagina mai are lista formelor actului, cu antetul ei, iar la consolidarea 295428 a CPF
        # prima potrivire de acolo ducea la 292653, alt text decât 295429 (măsurat, 7.10.2026).
        bloc = h.find('id="div_Formaconsolidata"')
        antet = re.search(r'<span class="S_HDR">(.*?)</span>', h[bloc:], re.S) if bloc >= 0 else None
        leg = sorted(set(re.findall(r"DetaliiDocumentAfis/(\d+)", antet.group(1)))) if antet else []
        if len(leg) == 1:
            print("  pagina %s n-are text, doar antet; textul e pe Afis/%s" % (ident, leg[0]))
            h = ia(AFIS % leg[0])
            surse.append(AFIS % leg[0])
    marc = [(m.start(), m.group(1), m.group(2)) for m in re.finditer(
        r'<[^>]*id="id_(cap|art|sec|tit|anx)[A-Za-z0-9_]*?(_ttl|_bdy)"[^>]*>', h)]
    out = []
    for i, (poz, tip, fel) in enumerate(marc):
        sf = marc[i + 1][0] if i + 1 < len(marc) else len(h)
        seg = h[poz:sf]
        t = text_din(seg)
        leg = re.findall(r'class="S_REF"><A href="[^"]*DetaliiDocumentAfis/(\d+)"', seg)
        if tip == "anx" and fel == "_bdy" and len(leg) == 1:
            print("  anexa „%s” e pe Afis/%s; o aduc la locul ei" % (t[:50], leg[0]))
            out.append(("_ttl", t))
            out += articole(ia(AFIS % leg[0]))
            surse.append(AFIS % leg[0])
        elif t:
            out.append((fel, t))
    return out, surse


def consolidari(pagina):
    """[(data, id)] pentru toate consolidarile listate, cea mai recenta prima.

    **Se cauta in toata pagina, nu intr-o fereastra.** Cautarea se facea in primele patru mii de
    caractere de dupa marcajul istoricului; la un act modificat de zeci de ori, intrarile se
    intind mult mai departe — masurat, aproape cincisprezece mii de caractere, din care in
    fereastra incapeau douazeci si doua din nouazeci. Si erau **cele mai recente**, adica exact
    cele care nu trebuiau la o cerere despre trecut.

    Tiparul e destul de ingust ca sa nu aiba nevoie de ancora: cere si titlul „Consolidarea
    din", si adresa de detaliu.

    Ordonarea se face **pe data**, nu pe ordinea din pagina. Cine cere ultima consolidare o ia
    de pe prima pozitie, iar asta nu trebuie sa depinda de felul in care e asezat tabelul."""
    gasite = re.findall(
        r"title='Consolidarea din ([\d.]+)'[^>]*href='[^']*DetaliiDocument/(\d+)'", pagina)
    vazute, out = set(), []
    for data, ident in gasite:
        if ident not in vazute:
            vazute.add(ident)
            out.append((data, ident))
    out.sort(key=lambda x: x[0].split(".")[::-1], reverse=True)
    return out


def _cheie_data(d):
    z = d.split(".")
    return "%s-%s-%s" % (z[2], z[1], z[0]) if len(z) == 3 else ""


def consolidari_complete(baza_html, id_baza):
    """[(data, id)] — istoricul ÎNTREG, cea mai recentă prima. `consolidari` citește doar intrările
    cu legătură, iar pe pagina actului de bază asta pierde două lucruri, măsurate pe 9.10.2026:

    - **forma cea mai nouă**: pagina de bază o afișează cu roșu, fără `href` (Legea 76/2002: 39 de
      titluri „Consolidarea din", 38 cu legătură; OUG 111/2010: 21/20; Codul fiscal: 91/90). Ea e
      chiar textul paginii de bază, deci primește id-ul actului de bază;
    - **istoricul vechi**, la actele modificate des: pagina de bază a Codului fiscal listează 91 de
      consolidări, pagina unei consolidări 153. Se completează din pagina celei mai noi consolidări
      cu legătură. Acolo id-urile sunt din cealaltă serie a Portalului (pagina de antet, nu pagina
      de afișare), deci un text adus de pe ele trece prin oprirea din `pregateste` (2.38.1).

    Reuniunea se face pe DATĂ; la aceeași dată rămâne id-ul de pe pagina de bază."""
    lista = consolidari(baza_html)
    out = {d: i for d, i in lista}
    for m in re.finditer(r"<a[^>]*title='Consolidarea din ([\d.]+)'[^>]*>", baza_html):
        if "href" not in m.group(0) and m.group(1) not in out:
            out[m.group(1)] = id_baza
    # Istoricul întreg stă pe paginile din cealaltă serie de id-uri: pagina de afișare a unei
    # consolidări (id-ul din lista paginii de bază) listează tot 90 la Codul fiscal, iar pagina ei
    # pereche, cu id-ul mai mic cu unu, 153 — aceeași perioadă, 64 de date în plus (măsurat
    # 12.10.2026). Denumirea nu se poate compara: la un cod, pagina pereche poartă legea de adoptare
    # („LEGE nr. 227 …"), pagina de afișare codul („CODUL FISCAL …"). Perechea se ACCEPTĂ dacă are
    # selectată aceeași formă și dacă istoricul ei cuprinde toate datele de pe pagina de bază;
    # altfel rămâne lista de pe pagina de bază, cu un avertisment.
    if lista:
        d0, i0 = lista[0]
        try:
            h2 = ia(PORTAL % (int(i0) - 1))
            l2 = dict(consolidari(h2))
            l2[d0] = i0
            # Și aceeași dată de adoptare: perechea unui ordin cu o singură consolidare pe pagina de bază putea fi
            # ALT act cu aceeași formă selectată (OMFP 925/2017 a primit istoricul și textul Ordinului MAI 137/2018,
            # 13.10.2026). La coduri, pagina pereche poartă legea de adoptare, cu aceeași dată.
            if antet_act(h2).get("consolidare") == d0 and all(d in l2 for d, _ in lista) and \
                    antet_act(h2).get("data") == antet_act(baza_html).get("data"):
                for d, i in l2.items():
                    out.setdefault(d, i)
            else:
                print("  istoricul consolidărilor: pagina pereche a lui %s nu e a aceleiași forme; rămân "
                      "cele %d de pe pagina de bază, poate incomplete" % (i0, len(out)))
        except Exception as e:  # noqa: BLE001
            print("  istoricul consolidărilor: n-am putut citi pagina pereche (%s); rămân cele %d de pe "
                  "pagina de bază, poate incomplete" % (e, len(out)))
    return sorted(out.items(), key=lambda x: _cheie_data(x[0]), reverse=True)


def sfarsitul_formei(lista, data_c):
    """Ziua dinaintea consolidării următoare, `zz.ll.aaaa`, sau "" când forma e cea mai nouă."""
    import datetime
    dupa = sorted(_cheie_data(d) for d, _ in lista if _cheie_data(d) > _cheie_data(data_c))
    if not dupa:
        return ""
    z = datetime.date.fromisoformat(dupa[0]) - datetime.timedelta(days=1)
    return z.strftime("%d.%m.%Y")


# Nota de modificare din textul consolidat: „(la 01-01-2024, Alineatul (2) ... a fost modificat de
# Punctul 1, Articolul I din LEGEA nr. 296 din 26 octombrie 2023, publicată în MONITORUL OFICIAL
# nr. 977 din 27 octombrie 2023 )". Poate conține paranteze („Alineatul (2)"), deci se taie la
# „publicat… în MONITORUL OFICIAL nr. … din …", nu la prima paranteză închisă.
NOTA_MODIFICARE = re.compile(
    r"\(la (\d{2})-(\d{2})-(\d{4}),(.{0,600}?)publicat\w* [îi]n MONITORUL OFICIAL\s+nr\.\s*([\d.]+)\s+din\s+(\d{1,2} \w+ \d{4})",
    re.S)
ACT_IN_NOTA = re.compile(
    # Două feluri de număr, măsurate pe 12.10.2026: „nr. 296 din 26 octombrie 2023" și, la ordinele
    # comune, „nr. 2.447/493/2024" (fără „din"). RECTIFICAREA e și ea o decizie publicată în MO.
    # „ORDONANȚA" și „ORDONANȚĂ" apar amândouă în note; cu o singură grafie, ordonanța de urgență
    # scăpa variantei lungi și era citită ca OG (măsurat: „OUG 115/2023" și „OG 115/2023" în aceeași
    # formă). Actul vine după „din" („Articolul I din LEGEA …") sau direct după „de" („modificat de
    # RECTIFICAREA …").
    r"(?:din|de)\s+((LEGEA|ORDONAN[ȚŢT][AĂ] DE URGEN[ȚŢT][ĂA]|ORDONAN[ȚŢT][AĂ]|HOT[ĂA]R[ÂA]REA|ORDINUL|DECIZIA|DECRETUL|RECTIFICAREA)\b[^,]*?"
    r"nr\.\s*([\d./]+?)(?:\s+din\s+\d{1,2} \w+ (\d{4})|(?=,)))", re.S)
TIP_ACT = {"LEGEA": ("Legea", "Legea"), "ORDONANȚA DE URGENȚĂ": ("Ordonanța de urgență a Guvernului", "OUG"),
           "ORDONANȚA": ("Ordonanța Guvernului", "OG"), "HOTĂRÂREA": ("Hotărârea Guvernului", "HG"),
           "ORDINUL": ("Ordinul", "Ordinul"), "DECIZIA": ("Decizia", "Decizia"), "DECRETUL": ("Decretul", "Decretul"),
           "RECTIFICAREA": ("Rectificarea", "Rectificarea")}


def _ascii(s):
    return re.sub(r"\s+", " ", s.translate(str.maketrans("ȚŢȘŞĂÂÎțţșşăâî", "TTSSAAItsssaai")).upper()).strip()


TIP_ACT_ASCII = {_ascii(k): v for k, v in TIP_ACT.items()}


def acte_de_modificare(text, data_c):
    """[(denumire, scurt, mo)] — actele care au produs forma din `data_c` (`zz.ll.aaaa`): cele ale
    căror note de modificare din text poartă data de aplicare `data_c`. Decizia, nu editarea
    textului, identifică forma: așa o numește și Monitorul Oficial („cu modificările și completările
    ulterioare, inclusiv cu cele aduse prin prezenta lege" — Legea 296/2023, MO 977/2023). Ordinea e
    cea a numărului de prevederi atinse, cel mai mult întâi."""
    tinta = "%s-%s-%s" % tuple(data_c.split("."))       # zz-ll-aaaa, ca în notă
    numar = {}
    # PDF-urile scrise de unealtă au cratimele datelor ca U+00AD (cratimă moale); se refac.
    # Textul din cache are marcaje de pagină („--- page 85 ---"), care pot cădea în mijlocul unei note
    # („ORDONANȚA DE --- page 85 --- URGENȚĂ") și o fac de nerecunoscut; se scot.
    t = re.sub(r"\s+", " ", re.sub(r"-{3} page \d+ -{3}", " ", (text or "").replace("­", "-")))
    for m in NOTA_MODIFICARE.finditer(t):
        if "%s-%s-%s" % (m.group(1), m.group(2), m.group(3)) != tinta:
            continue
        acte = list(ACT_IN_NOTA.finditer(m.group(4)))
        if not acte:
            continue
        a = acte[-1]
        lung, scurt = TIP_ACT_ASCII.get(_ascii(a.group(2)), (a.group(2).title(), a.group(2).title()))
        cod = ("%s/%s" % (a.group(3).replace(".", ""), a.group(4)) if a.group(4)
               else "/".join(x.replace(".", "") for x in a.group(3).split("/")))
        mo = "Monitorul Oficial al României, Partea I, nr. %s din %s" % (m.group(5), m.group(6))
        k = ("%s nr. %s" % (lung, cod), "%s %s" % (scurt, cod), mo)
        numar[k] = numar.get(k, 0) + 1
    return [k for k, _ in sorted(numar.items(), key=lambda x: -x[1])]


def camp(cheie, nume, cur=None):
    """Valoarea unui camp al unui item, din cheia lui. "" daca nu-l are.

    Fara `cur`, citirea e read-only si merge cu Zotero deschis.

    **In interiorul unui ciclu de scriere se da `cur`**, cursorul ciclului. Masurat pe 16
    septembrie 2026: dupa ce tranzactia pusese in ea nota de cache a Codului fiscal — 2,5 milioane
    de caractere —, citirea prin a doua conexiune a vazut o imagine incoerenta a bazei si a aruncat
    „database disk image is malformed". Tranzactia s-a derulat inapoi intreaga, deci actul n-a
    intrat, iar `PRAGMA integrity_check` pe baza a dat „ok": baza era buna, citirea era gresita.
    S-a repetat identic la a doua rulare."""
    con = cur if cur is not None else zot._ro()
    r = con.execute(
        "SELECT v.value FROM items i "
        "JOIN itemData d ON d.itemID = i.itemID "
        "JOIN itemDataValues v ON v.valueID = d.valueID "
        "JOIN fields f ON f.fieldID = d.fieldID "
        "WHERE i.key = ? AND f.fieldName = ?", (cheie, nume)).fetchone()
    return r[0] if r else ""


LUNI_RO = {"ianuarie": 1, "februarie": 2, "martie": 3, "aprilie": 4, "mai": 5, "iunie": 6, "iulie": 7,
           "august": 8, "septembrie": 9, "octombrie": 10, "noiembrie": 11, "decembrie": 12}
MO_RX = re.compile(r"(?:MONITORUL OFICIAL|Monitorul Oficial al Rom[âa]niei, Partea I,?)\s*nr\.\s*([\d.]+)\s+din\s+(\d{1,2}\s+\w+\s+\d{4})", re.I)


def _data_ro(text):
    """„2 decembrie 2004" -> ("2004-12-02", „2 decembrie 2004"), sau None."""
    m = re.search(r"(\d{1,2})\s+([a-zăâîșț]+)\s+(\d{4})", text or "", re.I)
    if not m or m.group(2).lower() not in LUNI_RO:
        return None
    return ("%s-%02d-%02d" % (m.group(3), LUNI_RO[m.group(2).lower()], int(m.group(1))),
            "%d %s %s" % (int(m.group(1)), m.group(2).lower(), m.group(3)))


def antet_act(h):
    """Ce spune pagina Portalului despre act, nu despre formă: data adoptării (din denumire),
    emitentul, publicarea în Monitorul Oficial, republicarea, și consolidarea afișată.

    Regula pe care o servește (decizia autorului, 9 octombrie 2026): `dateEnacted` e data
    adoptării, `history` e publicarea în Monitorul Oficial (și republicarea, care e textul
    consolidat oficial — Legea 24/2000, art. 70 alin. (3)), iar versiunea consolidată de pe
    Portal e doar sursa fișierului: „un instrument de documentare care nu produce efecte
    juridice" (art. 70^1 alin. (3)). Corespondența e cea din Bluebook, la care trimite Chicago:
    legea adoptată se citează cu data adoptării și publicarea oficială; o bază de date, cu
    actualitatea ei declarată de baza însăși."""
    def span(cls):
        m = re.search(r'class="%s"[^>]*>(.*?)</span>' % cls, h, re.S)
        return html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""
    den = re.sub(r"\s+", " ", span("S_DEN"))
    emitent = "; ".join(html.unescape(x).strip() for x in re.findall(
        r"<li>(.*?)</li>", (re.search(r'class="S_EMT_BDY"[^>]*>(.*?)</span>', h, re.S) or [None, ""])[1]))
    pub = re.sub(r"\s+", " ", span("S_PUB_BDY"))
    republicat = bool(re.search(r"republicat", den, re.I))
    mo_pub = MO_RX.search(pub)
    out = {"denumire": den, "emitent": emitent, "republicat": republicat,
           "data": _data_ro(den.split(" din ", 1)[1] if " din " in den else ""),
           "mo": ("nr. %s din %s" % (mo_pub.group(1), mo_pub.group(2))) if mo_pub else ""}
    # La un act republicat, antetul paginii poartă Monitorul republicării; publicarea inițială
    # stă în nota de republicare din text.
    out["mo_initial"], out["mo_republicare"] = out["mo"], ""
    if republicat:
        out["mo_republicare"] = out["mo"]
        text = html.unescape(re.sub(r"<[^>]+>", " ", h))
        m = re.search(r"publicat[ăa]\s+(?:inițial\s+)?în\s+Monitorul Oficial al Rom[âa]niei,\s*Partea I,?\s*nr\.\s*([\d.]+)\s+din\s+(\d{1,2}\s+\w+\s+\d{4})", text, re.I)
        out["mo_initial"] = ("nr. %s din %s" % (m.group(1), m.group(2))) if m else ""
    sel = re.search(r"title='Consolidarea din ([\d.]+)' id='fa_selectata'", h)
    out["consolidare"] = sel.group(1) if sel else ""
    return out


def istoric_publicare(antet):
    """Textul pentru `history`: publicarea oficială, și republicarea, dacă există."""
    if not antet.get("mo_initial") and not antet.get("mo_republicare"):
        return ""
    s = "Monitorul Oficial al României, Partea I, %s" % (antet.get("mo_initial") or antet.get("mo"))
    if antet.get("republicat") and antet.get("mo_republicare"):
        s += "; republicată în Monitorul Oficial al României, Partea I, %s" % antet["mo_republicare"]
    return s


def forma_item(k, cur=None):
    """Forma unui item din bibliotecă: pagina Portalului din `url`, iar dacă lipsește, rândul
    „Versiune consolidată la ..." din `extra`; la itemii vechi, `dateEnacted`."""
    url = camp(k, "url", cur) or ""
    m = re.search(r"DetaliiDocument(?:Afis)?/(\d+)", url)
    if m:
        return "pagina " + m.group(1)
    m = re.search(r"Versiune consolidată la ([\d.]+)", camp(k, "extra", cur) or "")
    if m:
        return "consolidarea din " + m.group(1)
    return camp(k, "dateEnacted", cur) or ""


def are_forma(k, id_c, data_c, iso_c):
    """True dacă itemul `k` e chiar forma cerută: aceeași pagină de formă în `url`, ori același
    rând de versiune în `extra`; la itemii aduși înainte de 9.10.2026, data formei din `dateEnacted`."""
    url = camp(k, "url") or ""
    if url:
        return url.rstrip("/").endswith("/%s" % id_c)
    if data_c and ("Versiune consolidată la %s" % data_c) in (camp(k, "extra") or ""):
        return True
    return bool(iso_c) and (camp(k, "dateEnacted") or "").startswith(iso_c)


REPUBLICARE = re.compile(r"title='Republicarea din ([\d.]+)'[^>]*href='[^']*DetaliiDocument/(\d+)")


def republicarea_inainte(h_baza, pana_la):
    """Antetul ultimei republicări dinaintea formei (`pana_la`, AAAA-LL-ZZ), sau {}.
    Se citește din pagina republicării; paginile republicărilor mai vechi întorc uneori antetul
    celei mai noi, deci se ia doar ultima dinaintea formei."""
    reps = sorted(((d, i) for d, i in REPUBLICARE.findall(h_baza or "")),
                  key=lambda x: x[0].split(".")[::-1])
    reps = [x for x in reps if not pana_la or "-".join(x[0].split(".")[::-1]) <= pana_la]
    if not reps:
        return {}
    return antet_act(ia(PORTAL % reps[-1][1]))


def are_codul(r, cod):
    """True daca itemul `r` (un rand din `zot.find`) are EXACT codul `cod` in campul lui.

    Identitatea unui act e codul din `codeNumber`, nu aparitia codului in denumire. Potrivirea
    pe subsir prinde doua feluri de acte care nu sunt actul cerut: cele care il pun in aplicare
    si ii poarta codul in denumire — normele metodologice ale unei legi —, si cele cu numar mai
    lung, „124/2000" pentru „24/2000". O singura functie pentru refuz, pentru forma deja adusa si
    pentru surori, ca regula sa nu mai fie aplicata intr-un loc si uitata in altul."""
    return _cod_normal(camp(r[0], "codeNumber")) == _cod_normal(cod)


def tip_act(denumire):
    """Tipul actului din denumire („Legea", „OUG", „HG" …, ca în `TIPURI_SCURTE`), sau None."""
    d = (denumire or "").strip().lower()
    return next((scurt for tipar, scurt in TIPURI_SCURTE if re.match(tipar, d)), None)


def acelasi_tip(r, denumire):
    """Codul singur nu identifică actul: Legea 52/2011 și HG 52/2011 au același „52/2011”, iar pe
    12.10.2026 hotărârea a fost legată „Related” de lege ca formă a ei. Se cere și același tip, când
    amândouă denumirile îl arată; când una nu-l arată, decide codul, ca înainte."""
    t1, t2 = tip_act(camp(r[0], "nameOfAct") or r[1]), tip_act(denumire)
    return t1 is None or t2 is None or t1 == t2


def _cod_normal(cod):
    """Codul fara spatii de margine si fara punctul de mii din numar: „1.107/2025" = „1107/2025".

    Masurat pe biblioteca, 13 septembrie 2026: din 281 de acte cu cod, 258 il au „numar/an", dar 11
    il au cu punct de mii. Cu egalitatea bruta, `--cod 1107/2025` nu-si recunostea actul adus deja
    si il aducea a doua oara. Se scoate NUMAI punctul dintre cifre, dinaintea barei: celelalte forme
    — „(anexa)", un numar fara an — numesc alt obiect sau nu se pot compara, si raman exacte."""
    cod = (cod or "").strip()
    m = re.match(r"^([\d.]+)(/.*)$", cod)
    return m.group(1).replace(".", "") + m.group(2) if m else cod


# Prescurtarea tipului de act în titlul scurt, după exemplele din `--scurt` („Legea 273/2006”).
# Ordinea contează: „Ordonanța de urgență” se încearcă înaintea „Ordonanței”.
TIPURI_SCURTE = (
    (r"legea\b", "Legea"),
    (r"ordonan[țţt]a de urgen[țţt][ăa]", "OUG"),
    (r"ordonan[țţt]a\b", "OG"),
    (r"hot[ăa]r[âa]rea guvernului\b", "HG"),
    (r"ordinul\b", "Ordinul"),
    (r"decretul\b", "Decretul"),
    (r"hot[ăa]r[âa]rea\b", "Hotărârea"),
)


def scurt_implicit(denumire, cod):
    """Titlul scurt al unui act din denumirea oficială și cod: „Legea 76/2002”, „OUG 111/2010”.

    Lista de legislație citează actele după titlul scurt (`citari-zotero.md` §G), deci un act fără
    el nu trece de filtrul listei. Înainte, titlul scurt venea numai din `--scurt`; la o aducere
    singulară rămânea gol, iar autorul trebuia să-l ceară de fiecare dată (12.10.2026: 34 din 53
    de acte ale unei colecții fără el). Un nume consacrat — „Codul fiscal" — nu se deduce din
    denumire; acela se dă cu `--scurt`. None când tipul actului nu se recunoaște."""
    if not cod:
        return None
    d = (denumire or "").strip().lower()
    for tipar, scurt in TIPURI_SCURTE:
        if re.match(tipar, d):
            return "%s %s" % (scurt, cod)
    return None


def arata_duplicatele(existent, cod, data_forma="", pomenite=()):
    """Tipareste itemii cu acelasi cod in camp, care opresc actul. Se cheama inainte de a renunta.

    Se arata TOTI, nu primul: formele succesive ale aceluiasi act au acelasi cod, iar un refuz
    care numeste un singur item o poate numi tocmai pe cea nepotrivita. `pomenite` sunt
    potrivirile brute, cele care doar poarta codul in denumire; ele nu opresc nimic si se arata
    doar ca informatie, separat."""
    print("Există deja în bibliotecă %d item(i) cu codul %s în câmpul lor:" % (len(existent), cod))
    for k, titlu, _, _ in existent:
        d = forma_item(k)
        print("  %s — %s%s" % (k, (titlu or "")[:66], (" [%s]" % d) if d else ""))
    if len(existent) > 1:
        print("  Sunt mai multe forme ale aceluiași act; uită-te care e cea cerută.")
    arata_pomenite(pomenite, cod)
    if data_forma:
        print("Aceeași formă (%s) e deja adusă. Nu creez duplicat." % data_forma)
    else:
        print("Nu creez duplicat. Șterge-l sau înlocuiește fișierul manual.")
        print("Dacă îți trebuie o ALTĂ formă a aceluiași act, dă `--la-data AAAA-LL-ZZ`: "
              "formele se țin ca obiecte separate, cu codul păstrat.")


def deja_in_biblioteca(a, existent):
    """Actul e deja în bibliotecă: dacă e o singură formă potrivită și nu stă în colecția cerută,
    se filează acolo — fără a-l aduce încă o dată de pe Portal.

    Cazul care a cerut-o, 12.10.2026: din 14 acte lipsă dintr-o colecție de proiect, opt erau deja
    în bibliotecă, în colecția altui proiect. Unealta refuza („există deja") și se oprea acolo,
    deci filarea se făcea de mână. Cu mai multe forme ale aceluiași act nu se alege nimic: care
    formă trebuie e întrebarea autorului, iar `--la-data` o spune."""
    cid = getattr(a, "_cid", None)
    if cid is None or len(existent) != 1:
        if cid is not None and len(existent) > 1:
            print("Nu filez nimic în colecție: sunt %d forme, iar care trebuie spune `--la-data`."
                  % len(existent))
        return None
    cheie = existent[0][0]
    con = zot._ro()
    try:
        iid = zot.item_id_for_key(con, cheie)
        e_acolo = con.execute("SELECT 1 FROM collectionItems WHERE collectionID=? AND itemID=?",
                              (cid, iid)).fetchone()
    finally:
        con.close()
    if e_acolo:
        print("Itemul %s e deja și în colecția cerută." % cheie)
        return None
    print("Itemul %s nu e în colecția cerută: îl filez acolo, fără să-l aduc din nou." % cheie)
    return {"a": a, "doar_colectie": cheie}


def amana(cid_cheie, p):
    """Lasă actul ÎNTREG în `scrieri_amanate` — item, PDF, text memoizat, legăturile formelor —
    când Zotero e deschis. Înainte, unealta se oprea („Închide-l grațios și reia"), iar un lot
    de acte se amâna întreg; acum partea scumpă (Portalul, PDF-ul) se face, iar scrierea o face
    primul ciclu de închidere, al oricui."""
    import scrieri_amanate as sa
    a = p["a"]
    if p.get("doar_colectie"):
        return sa.lasa("filare %s în colecție" % a.scurt, [
            {"op": "add_to_collection", "item": p["doar_colectie"], "colectie": cid_cheie}], cine="adu_lege.py")
    if p.get("evidentieri"):
        raise SystemExit("%s: highlight-urile nu se pot amâna (scrieri_amanate nu le are). Închide "
                         "Zotero și reia, sau adu actul fără --evidentiaza." % a.scurt)
    if getattr(a, "la_item", ""):
        ops = ops_la_item(a.la_item, p)
        return sa.lasa("act normativ %s, atașat la %s" % (a.scurt, a.la_item), ops, cine="adu_lege.py")
    ops = [{"op": "add_item", "tip": "statute", "campuri": campuri_item(p), "colectie": cid_cheie,
            "daca_exista": "creeaza", "nume": "act"},
           {"op": "attach_pdf", "parinte": "act", "cale": p["tinta"], "nume": "pdf"},
           {"op": "ensure_cached", "atasament": "pdf", "cale": p["tinta"]}]
    if a.la_data:
        ops += [{"op": "relateaza", "a": "act", "b": k} for k, _, _, _ in p["surori"]]
    return sa.lasa("act normativ %s" % a.scurt, ops, cine="adu_lege.py")


def ops_la_item(cheie, p):
    """Operațiile care pun forma adusă pe un item care există deja, fără fișier (13.10.2026: actele
    unui proiect, create de mână cu titluri scurte în engleză, fără PDF). Titlul scurt și denumirea
    itemului rămân, dacă le are; câmpurile care descriu forma (`url`, `history`, `dateEnacted`,
    `codeNumber`) vin de la Portal, iar Extra se recompune: emitentul, rândurile formei, notele
    proprii ale itemului, marcajul de verificare."""
    c = campuri_item(p)
    vechi = {f: camp(cheie, f) or "" for f in ("shortTitle", "nameOfAct", "extra")}
    forma = [l for l in c["extra"].split("\n") if not l.startswith("Metadate verificate pe text:")]
    proprii = [l for l in vechi["extra"].split("\n") if l.strip() and l not in forma and not l.startswith(
        ("Versiune ", "Forma publicată", "Modificată prin:", "Aplicabilă", "Metadate verificate pe text:"))]
    marcaj = [l for l in c["extra"].split("\n") if l.startswith("Metadate verificate pe text:")]
    ops = [{"op": "set_field", "item": cheie, "camp": f, "valoare": c[f]}
           for f in ("url", "history", "dateEnacted", "codeNumber") if c.get(f)]
    ops += [{"op": "set_field", "item": cheie, "camp": f, "valoare": c[f]}
            for f in ("shortTitle", "nameOfAct") if not vechi[f].strip() and c.get(f)]
    ops.append({"op": "set_field", "item": cheie, "camp": "extra", "valoare": "\n".join(forma + proprii + marcaj)})
    ops += [{"op": "attach_pdf", "parinte": cheie, "cale": p["tinta"], "nume": "pdf"},
            {"op": "ensure_cached", "atasament": "pdf", "cale": p["tinta"]}]
    ops += [{"op": "relateaza", "a": cheie, "b": k} for k, _, _, _ in p.get("surori") or []]
    return ops


def arata_pomenite(pomenite, cod):
    """Tipareste itemii care poarta codul doar in denumire. Informatie, nu refuz."""
    if not pomenite:
        return
    print("Codul %s apare și în denumirea a %d item(i) care au alt cod în câmpul lor "
          "(nu opresc aducerea):" % (cod, len(pomenite)))
    for k, titlu, _, _ in pomenite:
        print("  %s — %s [cod: %s]" % (k, (titlu or "")[:60], camp(k, "codeNumber") or "—"))


def consolidarea_la(lista, la_data):
    """(data, id) pentru forma in vigoare la `la_data`, sau None daca niciuna nu e destul de veche.

    `lista` vine de la `consolidari`, cu datele scrise `zz.ll.aaaa`; `la_data` e `aaaa-ll-zz`.
    Se alege consolidarea cu data cea mai mare mai mica sau egala cu ea — adica forma care era
    in vigoare atunci, nu cea de acum.

    **De ce nu e o comoditate.** Pentru un an fiscal incheiat, ultima consolidare da textul
    gresit, iar greseala e tacuta: un articol citit din forma nepotrivita da o cifra falsa, si
    nimic din pagina nu spune ca forma nu e cea aplicabila. Masurat pe Codul fiscal, unde cota
    contributiei de pensie e impartita altfel in forma initiala decat in cea din 2024.

    None inseamna ca actul, la data ceruta, era inca in forma publicata initial — care se ia de
    la `/Public/DetaliiDocumentAfis/<id>`, alta adresa, nu de aici."""
    def cheie(d):
        z = d.split(".")
        return "%s-%s-%s" % (z[2], z[1], z[0]) if len(z) == 3 else ""
    potrivite = [(cheie(d), d, i) for d, i in lista if cheie(d) and cheie(d) <= la_data]
    if not potrivite:
        return None
    potrivite.sort()
    _, data, ident = potrivite[-1]
    return data, ident


def pdf(text, tinta):
    font = fitz.Font(fontfile=TTF)
    lat, inal = fitz.paper_size("a4")
    marg, dim, rand = 56, 9.5, 12.6
    randuri = []
    for linie in text.split("\n"):
        if not linie.strip():
            randuri.append("")
            continue
        curent = ""
        for c in linie.split(" "):
            prob = (curent + " " + c).strip()
            if font.text_length(prob, dim) <= lat - 2 * marg:
                curent = prob
            else:
                randuri.append(curent)
                curent = c
        randuri.append(curent)
    doc = fitz.open()
    pag, y = None, 0.0
    for r in randuri:
        if pag is None or y > inal - marg:
            pag = doc.new_page(width=lat, height=inal)
            pag.insert_font(fontname="ro", fontfile=TTF)
            y = marg
        pag.insert_text((marg, y), r, fontname="ro", fontsize=dim)
        y += rand
    doc.save(tinta)
    n = doc.page_count
    doc.close()
    return n


RE_TITLU = re.compile(r"\n\n(?:Articolul|Capitolul|CAPITOLUL|Secțiunea|SECȚIUNEA|Titlul|TITLUL|"
                      r"Anexa|ANEXA)\b[^\n]*\n\n")
# Marcajul unui alineat: „(2) Prevederile…". Nu e marcaj când stă după „alin." sau „art." —
# acolo e o trimitere la alt alineat —, și cere literă mare după el, cum începe orice alineat.
RE_ALINEAT = re.compile(r"(?<!alin\. )(?<!art\. )(?<![\w,])\((\d+(?:\^\d+)?)\) (?=[A-ZĂÂÎȘȚŞŢ])")


def localizeaza(corp, loc):
    """(fragment, cuvantul_de_start) pentru „art. 11" sau „art. 11 alin. (1)", din textul din
    care `pdf()` a scris PDF-ul. Ridică ValueError cu motivul, când nu se găsește.

    Articolul începe la titlul „Articolul N" și se termină la titlul următor. Alineatul începe la
    marcajul „(k)" și se termină la marcajul următor. Notele de modificare dintre ele („(la
    ZZ-LL-AAAA, … a fost modificat de …)") rămân înăuntru: textul alineatului poate continua
    după o notă — la Legea 554/2004, art. 11 alin. (1) are litera e) după prima notă —, iar
    tăierea la prima notă ar fi pierdut tăcut restul alineatului.

    `cuvantul_de_start` e numărul de cuvinte din `corp` dinaintea fragmentului. PDF-ul are exact
    cuvintele lui `corp`, în aceeași ordine, deci `zot.gaseste_fragment` caută de acolo și nu
    poate nimeri o apariție identică mai devreme — un alineat repetat într-o anexă, de exemplu."""
    m = re.fullmatch(r"\s*art\.?\s*(\d+(?:\^\d+)?)(?:\s*,?\s*alin\.?\s*\((\d+(?:\^\d+)?)\))?\s*",
                     loc, re.I)
    if not m:
        raise ValueError("nu înțeleg %r; scrie „art. 7” sau „art. 11 alin. (1)”" % loc)
    art, alin = m.group(1), m.group(2)
    t = re.search(r"\n\nArticolul %s\n\n" % re.escape(art), corp)
    if not t:
        raise ValueError("actul n-are „Articolul %s”" % art)
    sf = RE_TITLU.search(corp, t.end())
    corp_art = corp[t.end():sf.start() if sf else len(corp)]
    inceput, sfarsit = 0, len(corp_art)
    if alin:
        marcaje = list(RE_ALINEAT.finditer(corp_art))
        al = next((x for x in marcaje if x.group(1) == alin), None)
        if not al:
            raise ValueError("articolul %s n-are alineatul (%s); are: %s"
                             % (art, alin, ", ".join("(%s)" % x.group(1) for x in marcaje) or "niciunul"))
        inceput = al.start()
        urm = next((x for x in marcaje if x.start() > al.start()), None)
        sfarsit = urm.start() if urm else len(corp_art)
    fragment = corp_art[inceput:sfarsit].strip()
    if not fragment:
        raise ValueError("%s e gol în text" % loc)
    start = len(corp[:t.end() + inceput].split())
    return fragment, start



# Refuzul unui act dintr-un lot. NU e un cod de ieșire: un `2` întors de aici era adevărat,
# intra în lista de depus și oprea tot lotul cu o eroare de tip, după ce celelalte acte fuseseră
# deja aduse.
OPRIT = "oprit"

# Sub atâtea caractere, textul extras nu e un act normativ. O singură constantă, fiindcă pragul
# se aplică de două ori — la pregătire, pe textul adus, și la depunere, pe textul memoizat —, iar
# două cifre scrise de mână ar ajunge să difere tăcut.
PRAG_TEXT = 2000


def pregateste(a):
    """Aduce actul, alege forma și scrie PDF-ul. NU atinge baza pentru scriere.

    Întoarce ce trebuie depus; None când nu e nimic de depus — actul e deja în bibliotecă,
    ori s-a cerut doar verificarea; `OPRIT` când actul e refuzat.

    **Despărțirea de `depune` e reparația.** Fiecare aducere își deschidea propriul ciclu de
    scriere, iar ciclul pornește Zotero la ieșire; la mai multe acte, calea corectă după
    protocol — un singur ciclu per sarcină — nu se putea parcurge deloc. Acum partea scumpă și
    fără bază se face pentru toate actele, și abia apoi se deschide un singur ciclu."""
    # ORDINEA verificărilor, și e ea însăși reparația: întâi cele care pot opri rularea fără
    # să coste nimic — biblioteca se citește read-only —, abia apoi cele care cer ceva de la om,
    # și abia la urmă descărcarea. Invers, o rulare care oricum n-ar fi scris nimic cerea
    # închiderea programului și aducea câteva sute de pagini ca să refuze la final.
    #
    # Refuzul se face pe itemul cu ACELAȘI cod în câmpul lui (`are_codul`), nu pe codul apărut în
    # denumire. Pe subșir, o bibliotecă ce avea doar normele de aplicare ale unei legi, ori un act
    # „124/2000", oprea aducerea legii „24/2000" cu cod de ieșire 0 — legea nu intra deloc, iar
    # refuzul arăta ca un duplicat adevărat. `zot.find` caută în toate câmpurile, deci prinde și
    # itemul al cărui cod nu apare în denumire; potrivirile doar din denumire se arată separat.
    potriviri = zot.find(a.cod) if not a.doar_verifica else []
    existent = [r for r in potriviri if are_codul(r, a.cod) and acelasi_tip(r, a.denumire)
                and r[0] != getattr(a, "la_item", "")]
    pomenite = [r for r in potriviri if a.cod in (r[1] or "") and r not in existent]
    if existent and not a.la_data:
        arata_duplicatele(existent, a.cod, pomenite=pomenite)
        return deja_in_biblioteca(a, existent)
    arata_pomenite(pomenite, a.cod)

    # `iso_c` e data formei alese, aaaa-ll-zz, și se întoarce mai jos pe orice ramură. Se
    # inițializează aici, nu pe ramuri: ramura obișnuită — forma de azi a unui act modificat —
    # nu-l definea, deci cazul implicit al uneltei cădea la `return` (rateul din 7 octombrie
    # 2026). E a doua oară când aceeași variabilă lipsește de pe o ramură.
    iso_c = ""
    baza = ia(PORTAL % a.id_baza)
    parti_baza, surse_baza = forma(a.id_baza, baza)
    surse = {a.id_baza: surse_baza}
    lista = consolidari_complete(baza, a.id_baza)
    publicata_pana = ""   # forma publicată a unui act modificat ulterior: ziua dinaintea primei consolidări
    if not lista:
        # Lipsa listei NU e o eroare de id. Un act recent sau nemodificat n-are consolidări
        # listate — măsurat pe două hotărâri de guvern —, iar pagina de bază e chiar forma în
        # vigoare. Ieșirea cu eroare făcea scriptul inutilizabil tocmai pentru actele noi.
        print("Nu există listă de consolidări: actul n-a fost modificat, sau e prea recent.")
        print("Folosesc pagina de bază. Comparația între forme nu s-a putut face.")
        data_c, id_c, parti_cons = "", a.id_baza, parti_baza
        if a.la_data:
            # **Un act fără nicio consolidare are o singură formă, iar aceea e și forma de la
            # data cerută** — nu s-a schimbat nimic între timp. La hotărâri de Guvern și la
            # ordine e cazul obișnuit, nu unul de margine: ele se înlocuiesc, nu se modifică.
            #
            # Aici se oprea cu o eroare de programare, fiindcă `iso_c` nu apuca să existe. Urma
            # de execuție nu spunea nimic despre act, deci cine o citea credea că unealta e
            # stricată pe tot — când răspunsul era la îndemână.
            #
            # Se refuză doar dacă data cerută e **anterioară publicării**, și doar când data
            # publicării e cunoscută: atunci actul chiar nu exista încă.
            adoptat = (a.data or "").split(" ", 1)[0]
            if adoptat and adoptat[:10] > a.la_data:
                raise SystemExit(
                    "Actul e publicat la %s, adică DUPĂ data cerută (%s), și n-are nicio "
                    "consolidare.\nLa data aceea nu era în vigoare; nu am ce formă să aduc."
                    % (adoptat[:10], a.la_data))
            # Forma unică poate fi deja adusă. Ramura cu consolidări compară data formei cu ce e
            # în bibliotecă; ramura asta n-avea nicio comparație, iar a doua cerere crea un item
            # identic și îl lega ca „Related" de primul. Aici nu există o dată de formă de
            # comparat — actul are o singură formă —, deci orice item cu ACELAȘI cod în câmpul lui
            # e chiar forma cerută. `existent` e deja lista acelora (`are_codul`).
            deja = existent
            if deja:
                arata_duplicatele(deja, a.cod, "publicată, singura")
                return deja_in_biblioteca(a, deja)
            print("  cerut: forma în vigoare la %s" % a.la_data)
            print("  ALES: forma publicată — actul n-are nicio consolidare, deci nu s-a "
                  "modificat de la publicare, iar forma de atunci e cea de acum.")
        candidati = [("pagina de bază (%s)" % a.id_baza, a.id_baza, parti_baza)]
    elif a.la_data:
        # Cu o dată cerută, comparația „cine poartă modificarea cea mai recentă" NU se mai face:
        # ea alege întotdeauna forma de azi, adică exact ce nu s-a cerut.
        gasit = consolidarea_la(lista, a.la_data)
        if getattr(a, "forma_publicata", False):
            # Forma publicată a paginii date, cerută explicit: o republicare nu apare în lista de forme a
            # Portalului, deci data ei cade pe consolidarea dinainte (Codul de procedură civilă, republicat
            # la 10.04.2015, ieșea ca forma din 10.12.2014; 13.10.2026). Sfârșitul aplicării e ziua
            # dinaintea primei consolidări de după data cerută.
            gasit = None
        if not gasit:
            # La data cerută actul era în forma publicată: textul ei e pe `Afis` al actului de bază
            # (cererea din 12.10.2026, o ordonanță aplicată în 2024 și modificată în 2025). Se aduce
            # numai dacă pagina chiar n-are nicio notă de modificare; altfel e altă formă.
            dupa = [d for d, _ in lista if _cheie_data(d) > a.la_data] if getattr(a, "forma_publicata", False) else []
            cea_mai_veche = min(dupa or [d for d, _ in lista], key=_cheie_data)
            try:
                parti_pub, _ = forma(a.id_baza, ia(AFIS % a.id_baza))
            except Exception as e:  # noqa: BLE001
                parti_pub = []
                print("  forma publicată nu s-a putut citi de pe Afis/%s: %s" % (a.id_baza, str(e)[:120]))
            note_pub = re.findall(r"\(la \d{2}-\d{2}-\d{4}", "\n".join(x for _, x in parti_pub))
            if parti_pub and not note_pub:
                publicata_pana = sfarsitul_formei([(cea_mai_veche, "")], "01.01.0001")
        if not gasit and publicata_pana:
            id_c, data_c, iso_c = a.id_baza, "", ""
            deja = [r for r in existent if (camp(r[0], "url") or "").rstrip("/").endswith("DetaliiDocumentAfis/%s" % a.id_baza)]
            if deja:
                arata_duplicatele(deja, a.cod, "publicată")
                return deja_in_biblioteca(a, deja)
            parti_cons, surse[id_c] = parti_pub, [AFIS % a.id_baza]
            print("  cerut: forma în vigoare la %s" % a.la_data)
            print("  ALES: forma publicată (Afis/%s) — prima consolidare e din %s, după data cerută; "
                  "aplicabilă până la %s" % (a.id_baza, cea_mai_veche, publicata_pana))
            candidati = [("forma publicată (Afis/%s)" % a.id_baza, id_c, parti_cons)]
        elif not gasit:
            # Refuzul spune ÎNTÂI ce s-a citit. Fără cifrele astea el sună a fapt despre act —
            # „era încă în forma inițială" —, când poate fi doar o limită a citirii, iar cine îl
            # crede pleacă să caute exact textul greșit.
            cea_mai_veche = min(lista, key=lambda x: x[0].split(".")[::-1])[0]
            raise SystemExit(
                "Am citit %d consolidări din pagină, între %s și %s.\n"
                "Niciuna nu e anterioară datei %s.\n"
                "Dacă pagina arată consolidări mai vechi decât %s, atunci citirea e de vină, nu "
                "actul — spune-o, e un rateu.\n"
                "Altfel, la data cerută actul era încă în forma publicată inițial, care se ia "
                "de la\n"
                "  https://legislatie.just.ro/Public/DetaliiDocumentAfis/%s\n"
                "Nu descarc forma de azi în locul ei: ar da textul greșit, fără să se vadă."
                % (len(lista), cea_mai_veche, lista[0][0], a.la_data, cea_mai_veche, a.id_baza))
        else:
            data_c, id_c = gasit
            iso_c = "-".join(data_c.split(".")[::-1])   # zz.ll.aaaa -> aaaa-ll-zz
            # Forma se compară aici, cât încă n-am adus textul. `zot.find` întoarce denumirea
            # actului, aceeași la toate formele, deci data se citește din câmpul ei, din bază.
            deja = [r for r in existent if are_forma(r[0], id_c, data_c, iso_c)]
            if deja:
                arata_duplicatele(deja, a.cod, data_c)
                return deja_in_biblioteca(a, deja)
            if id_c == a.id_baza:   # forma cea mai nouă: e textul paginii de bază, deja adus
                parti_cons, surse[id_c] = parti_baza, surse_baza
            else:
                parti_cons, surse[id_c] = forma(id_c)
            print("  cerut: forma în vigoare la %s" % a.la_data)
            print("  ALES: consolidarea din %s (%s) — cea mai recentă anterioară datei" % (data_c, id_c))
            candidati = [("consolidarea din %s (%s)" % (data_c, id_c), id_c, parti_cons)]
    else:
        data_c, id_c = lista[0]
        if id_c == a.id_baza:
            # Cea mai nouă consolidare e chiar textul paginii de bază (intrarea roșie, fără legătură).
            # Înainte de 2.40.0 lista n-o avea, iar comparația se făcea cu penultima formă.
            parti_cons = parti_baza
            candidati = [("consolidarea din %s, pe pagina de bază (%s)" % (data_c, id_c), id_c, parti_baza)]
        else:
            parti_cons, surse[id_c] = forma(id_c)
            candidati = [("pagina de bază (%s)" % a.id_baza, a.id_baza, parti_baza),
                         ("consolidarea din %s (%s)" % (data_c, id_c), id_c, parti_cons)]
    ales, cea_mai_noua = None, None
    for eticheta, ident, parti in candidati:
        t = "\n".join(x for _, x in parti)
        date = re.findall(r"\(la (\d{2})-(\d{2})-(\d{4})", t)
        ultima = max((("%s-%s-%s" % (z[2], z[1], z[0])) for z in date), default="")
        print("  %-34s %4d fragmente, ultima modificare %s" % (eticheta, len(parti), ultima or "necunoscută"))
        if cea_mai_noua is None or ultima > cea_mai_noua:
            cea_mai_noua, ales = ultima, (eticheta, ident, parti)
    eticheta, id_ales, parti = ales
    print("ALES: %s — poartă cea mai recentă modificare (%s)" % (eticheta, cea_mai_noua or "?"))
    if len(parti_baza) == len(parti_cons) and cea_mai_noua == "" and len(candidati) > 1:
        print("ATENȚIE: nu s-a putut distinge. Compară manual înainte de a fila.")

    # Textul adus trebuie să fie chiar forma cerută. O pagină de consolidare fără text trimite,
    # din antet, la o pagină `Afis` — iar trimiterea poate duce la ALTĂ formă. Măsurat pe
    # 9 octombrie 2026: consolidarea Codului fiscal din 18.01.2024 trimitea la textul cu
    # modificări până la 09.12.2022, iar fișierul a intrat în bibliotecă etichetat „18.01.2024",
    # fără ca ceva să compare cererea cu răspunsul. Testul: o formă poartă modificările intrate
    # în vigoare la consolidarea dinaintea ei; dacă ultima modificare din text e mai veche decât
    # acea consolidare, textul e al unei forme anterioare și nu se scrie nimic.
    if id_ales != a.id_baza and cea_mai_noua and data_c:
        _iso = lambda d: "-".join(d.split(".")[::-1])
        inainte = [d for d, _ in lista if _iso(d) < _iso(data_c)]
        prag = max(inainte, key=_iso) if inainte else ""
        if prag and cea_mai_noua < _iso(prag):
            print("\nOPRIT: s-a cerut consolidarea din %s (%s), dar textul adus are ultima "
                  "modificare la %s," % (data_c, id_ales, cea_mai_noua))
            print("anterioară consolidării dinainte (%s). Textul e al altei forme; paginile citite:"
                  % prag)
            for u in surse.get(id_ales, []):
                print("  " + u)
            print("Nu s-a creat niciun item și nu s-a atașat niciun fișier.")
            return OPRIT

    # Extragerea goală NU se filează. Măsurat: pagina actului de adoptare al Codului muncii dă
    # 288 de caractere și zero articole — textul codului stă pe altă pagină —, iar scriptul
    # crea itemul și atașa PDF-ul gol, cu un avertisment care se pierdea în ieșire. În
    # bibliotecă rămânea un act care arată corect și n-are text. Un avertisment nu ține locul
    # unui refuz: itemul rămâne, avertismentul nu.
    _text_ales = "\n".join(x for _, x in parti)
    if len(parti) == 0 or len(_text_ales) < PRAG_TEXT:
        print("\nOPRIT: extragerea a dat %d fragmente și %d caractere."
              % (len(parti), len(_text_ales)))
        print("Prea puțin pentru un act normativ — pagina cerută e probabil actul de adoptare,")
        print("nu textul lui. Caută pe portal pagina care poartă textul și dă id-ul ei.")
        print("Nu s-a creat niciun item și nu s-a atașat niciun fișier.")
        return OPRIT

    # `cea_mai_noua` e ULTIMA MODIFICARE găsită în text, nu data consolidării — două mărimi
    # diferite, care se nimeresc adesea egale. Suprascrierea lui `data_c` cu ea trimitea în
    # `history` și în `dateEnacted` altceva decât forma cerută, iar la o formă veche ar fi
    # trimis pur și simplu altă dată. Cu `--la-data`, data consolidării e cea cerută și rămâne.
    if not a.la_data:
        data_c = cea_mai_noua or data_c
    id_c = id_ales
    linii = [a.denumire, "",
             ("FORMA PUBLICATĂ în Monitorul Oficial, fără modificările ulterioare; aplicabilă până la %s."
              % publicata_pana) if publicata_pana else
             # Data formei, nu a celei mai noi note: o notă cu aplicare amânată („la 01-01-2025" în forma din
             # 12.08.2024) făcea antetul să spună altă formă decât cea adusă (13.10.2026).
             "FORMA ÎN VIGOARE — text cu modificări până la %s." % (
                 "-".join(_zz(data_c).split(".")[::-1]) if data_c and "." in _zz(data_c) else (cea_mai_noua or data_c)),
             "Preluată de pe Portalul Legislativ (Ministerul Justiției):"]
    # Toate paginile din care s-a compus textul, nu doar prima: la un act cu textul sau anexa pe
    # altă pagină, adresa paginii de antet singură nu duce la ce scrie în PDF.
    linii += surse.get(id_c, [PORTAL % id_c]) + ["", "=" * 78, ""]
    for fel, t in parti:
        linii += (["", t, ""] if fel == "_ttl" else [t])
    corp = "\n".join(linii)
    n_art = len(re.findall(r"Articolul\s+\d+", corp))
    print("articole numărate în text: %d | caractere: %d" % (n_art, len(corp)))

    # Locurile de subliniat se găsesc ÎNAINTE de scriere, cât încă nu costă nimic: un articol
    # care nu există oprește actul aici, nu după ce itemul a intrat în bibliotecă fără ele.
    locuri = []
    for loc, comentariu in (getattr(a, "evidentiaza", None) or []):
        if not (comentariu or "").strip():
            raise SystemExit("%s, %s: highlight fără comentariu. Comentariul spune ce afirmă actul "
                             "acolo și cum îl folosim." % (a.scurt, loc))
        try:
            fragment, start = localizeaza(corp, loc)
        except ValueError as e:
            raise SystemExit("%s, de subliniat %s: %s." % (a.scurt, loc, e))
        print("  de subliniat %-22s %d cuvinte: %s …" % (loc, len(fragment.split()), fragment[:60]))
        locuri.append((loc, comentariu, fragment, start))

    if a.doar_verifica:
        return None

    # Numele poartă forma: formele aceluiași act aduse una după alta, cu Zotero deschis, stau în coadă până la
    # aceeași închidere, iar cu un singur nume fiecare o suprascria pe cea dinainte (13.10.2026, patru forme ale
    # Legii 69/2000 ar fi primit toate textul ultimei).
    tinta = os.path.join(zot.LUCRU, "_import",
                         re.sub(r"[^A-Za-z0-9]+", "_", a.scurt + ("_" + _zz(data_c) if data_c else "_publicata")) + ".pdf")
    os.makedirs(os.path.dirname(tinta), exist_ok=True)
    print("PDF:", tinta, pdf(corp, tinta), "pagini")
    evidentieri = []
    for loc, comentariu, fragment, start in locuri:
        bucati = zot.gaseste_fragment(tinta, fragment, start)
        if not bucati:
            raise SystemExit("%s: %s e în text, dar nu l-am regăsit în PDF-ul scris. Nu depun "
                             "actul fără highlight-ul cerut; e un rateu de semnalat."
                             % (a.scurt, loc))
        evidentieri += [(loc, comentariu, b) for b in bucati]

    # Cu `--la-data`, „același act" nu mai înseamnă „același duplicat": două forme consolidate
    # ale aceleiași legi sunt texte diferite, cu cifre diferite, și se citează separat. Refuzul
    # pe cod le împiedica pe toate în afară de prima. Codul rămâne identic — el e cel după care
    # se adună formele —, iar forma stă în titlul scurt, deci potrivirea se face pe amândouă.
    # Sora e itemul cu ACELAȘI cod în câmpul lui, nu doar cu codul apărut în denumire. Codul
    # unei legi apare și în denumirea actelor care o pun în aplicare — normele metodologice —,
    # iar legarea pe potrivirea brută le făcea să apară ca forme ale legii. Scriptul știa deja
    # capcana: o folosea ca să arate toate potrivirile în refuz, dar nu și ca să îngusteze ce
    # leagă. Aceeași cunoștință, aplicată într-un loc și uitată în celălalt — de aceea regula stă
    # acum într-o singură funcție, `are_codul`, iar `existent` e deja filtrat prin ea.
    surori = list(existent) if a.la_data else []
    antet = antet_act(ia(AFIS % a.id_baza))
    rep = republicarea_inainte(baza, iso_c or (cea_mai_noua or "")[:10])
    antet["mo_republicare"], antet["republicat"] = rep.get("mo", ""), bool(rep.get("mo"))
    if not antet.get("data") and rep.get("data"):
        # Codurile publicate ca anexă poartă doar „din 2003"; ziua adoptării stă în denumirea
        # republicării („CODUL MUNCII din 24 ianuarie 2003 (**republicat**)").
        antet["data"] = rep["data"]
    gata = {"a": a, "tinta": tinta, "data_c": data_c, "iso_c": iso_c, "surori": surori, "antet": antet, "id_c": id_c,
            "cea_mai_noua": cea_mai_noua, "eticheta": eticheta, "evidentieri": evidentieri,
            "fara_consolidari": not lista, "publicata_pana": publicata_pana,
            # Decizia care a produs forma și perioada în care s-a aplicat (regula din 12.10.2026,
            # `citari-zotero.md` §G): actele din notele de modificare cu data formei, iar sfârșitul,
            # ziua dinaintea consolidării următoare.
            "acte": acte_de_modificare(_text_ales, _zz(data_c)) if data_c else [],
            "pana_la": sfarsitul_formei(lista, _zz(data_c)) if data_c else ""}
    campuri = campuri_item(gata)
    if getattr(a, "la_item", ""):
        # La un item existent, ce nu dă Portalul (publicarea în Buletinul Oficial, la actele vechi)
        # rămâne cum e în item; nu lipsește.
        campuri.update({f: camp(a.la_item, f) for f in CAMPURI_CERUTE if not (campuri.get(f) or "").strip()})
    lipsa = lipsuri(campuri)
    if lipsa:
        print("\nOPRIT: itemul ar ieși fără %s, deci n-ar trece de lista de legislație." % ", ".join(lipsa))
        print("Dă-le explicit (de pildă `--scurt`, `--denumire`) și reia. Nu s-a scris nimic.")
        return OPRIT
    return gata


def campuri_item(p):
    """Câmpurile itemului `statute`, din ce a adus `pregateste`. O singură sursă pentru depunere,
    pentru coada de scrieri amânate și pentru verificarea câmpurilor cerute de listă."""
    a, data_c, iso_c = p["a"], p["data_c"], p["iso_c"]
    # Regula (decizia autorului, 9 octombrie 2026, pe sursele care o dau):
    #   `dateEnacted` = data ADOPTĂRII, citită din forma publicată (`AFIS` pe actul de bază);
    #   `history`     = publicarea în Monitorul Oficial, și republicarea dinaintea formei — textul
    #                   consolidat oficial e republicarea (Legea 24/2000, art. 70 alin. (3));
    #   `extra`       = emitentul, iar pe al doilea rând versiunea consolidată de pe Portal, adică
    #                   sursa fișierului: „instrument de documentare care nu produce efecte
    #                   juridice" (art. 70^1 alin. (3));
    #   `url`         = pagina formei, care o identifică.
    # Corespondența e cea din Bluebook, la care trimite Chicago (14.170): legea adoptată se citează
    # cu data adoptării și publicarea oficială; o bază de date, cu actualitatea ei. Formele
    # succesive rămân iteme separate, legate „Related", și se deosebesc prin `url` și prin rândul
    # de versiune din `extra`, pe care lista de legislație îl afișează. Înainte, `dateEnacted`
    # purta data formei, iar două forme deveneau, pentru cine citea câmpul, două acte adoptate
    # la date diferite.
    antet = p.get("antet") or {}
    enacted = ("%s %s" % tuple(antet["data"])) if antet.get("data") else a.data
    istoric = istoric_publicare(antet)
    if p.get("publicata_pana"):
        # Forma publicată a unui act modificat după data cerută: rândul de versiune nu mai spune
        # „fără modificări ulterioare", iar perioada are doar sfârșitul (`linii_forma`).
        versiune = "Forma publicată, Portal Legislativ (Ministerul Justiției)"
    elif a.la_data and not iso_c:
        # Nu „fără modificări ulterioare": Portalul nu ține istoricul actelor vechi, deci o listă de
        # consolidări goală nu dovedește că actul n-a fost modificat (Legea 57/1968 a fost republicată,
        # iar Portalul n-o arată; 13.10.2026). Se spune ce s-a văzut.
        versiune = ("Forma publicată, Portal Legislativ (Ministerul Justiției), care nu listează "
                    "modificări ale actului")
    elif p.get("fara_consolidari") and not p.get("cea_mai_noua"):
        # Niciun istoric de consolidări și niciun marcaj de modificare în text: pagina de bază
        # poartă forma publicată. Fără ramura asta, rândul ieșea „cu modificări până la ?" — măsurat
        # pe o ordonanță nemodificată, 12.10.2026.
        versiune = "Forma publicată, fără modificări ulterioare, Portal Legislativ (Ministerul Justiției)"
    elif data_c:
        versiune = "Versiune consolidată la %s, Portal Legislativ (Ministerul Justiției)" % _zz(data_c)
    else:
        versiune = ("Versiune de pe pagina actului de bază, cu modificări până la %s, Portal "
                    "Legislativ (Ministerul Justiției)" % (p.get("cea_mai_noua") or "?"))
    emitent = a.emitent or antet.get("emitent", "")
    return {"nameOfAct": a.denumire, "codeNumber": a.cod, "shortTitle": a.scurt,
            # Itemul adus aici are metadatele luate din forma publicată și din textul formei, verificat
            # înainte de scriere (2.38.1): poartă de la început marcajul cerut la citare.
            "dateEnacted": enacted, "extra": "\n".join(x for x in (emitent, versiune) + linii_forma(p) + (
                "Metadate verificate pe text: %s" % __import__("datetime").date.today().isoformat(),) if x),
            "history": istoric,
            "url": (AFIS if p.get("publicata_pana") else PORTAL) % (p.get("id_c") or a.id_baza)}


def _zz(d):
    """`aaaa-ll-zz` sau `zz.ll.aaaa` -> `zz.ll.aaaa`."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", d or "")
    return "%s.%s.%s" % (m.group(3), m.group(2), m.group(1)) if m else (d or "")


def linii_forma(p):
    """Rândurile din `extra` care identifică forma după decizie (12.10.2026, `citari-zotero.md` §G):
    actele de modificare care au produs-o, cu Monitorul Oficial al fiecăruia, și perioada în care s-a
    aplicat. Monitorul Oficial nu publică „versiuni"; numește starea unui act prin actele care l-au
    modificat („inclusiv cu cele aduse prin prezenta lege"). Lista de legislație le citește de aici."""
    acte, de_la, pana = p.get("acte") or [], _zz(p.get("data_c") or ""), p.get("pana_la") or ""
    out = []
    if acte:
        out.append("Modificată prin: " + "; ".join("%s (%s)" % (lung, mo) for lung, _, mo in acte))
    if acte and de_la:
        out.append("Aplicabilă: %s–%s" % (de_la, pana) if pana else "Aplicabilă de la %s" % de_la)
    if p.get("publicata_pana"):
        # Începutul nu se scrie: textul poate amâna intrarea în vigoare față de publicare.
        out.append("Aplicabilă până la %s" % p["publicata_pana"])
    return tuple(out)


# Câmpurile fără de care actul nu trece de lista de legislație (`build_legislation_list.fmt`) sau
# de regula din `citari-zotero.md` §G. Se verifică ÎNAINTE de scriere: un act care ar ieși din
# listă se oprește aici, cu numele câmpului, nu se descoperă la construirea listei.
CAMPURI_CERUTE = ("shortTitle", "nameOfAct", "codeNumber", "history", "dateEnacted", "url")


def lipsuri(campuri):
    out = [c for c in CAMPURI_CERUTE if not (campuri.get(c) or "").strip()]
    versiune = [l for l in (campuri.get("extra") or "").split("\n") if l.startswith(("Versiune", "Forma"))]
    if not versiune or "?" in versiune[0]:
        out.append("rândul de versiune (data formei necunoscută)")
    return out


def depune(cur, cid, p):
    """Scrie itemul, atașează PDF-ul și leagă formele. Primește cursorul unui ciclu deschis."""
    a, tinta, surori = p["a"], p["tinta"], p["surori"]
    # `daca_exista="creeaza"`: formele succesive ale aceluiasi act au acelasi cod; refuzul pe forma
    # deja adusa se face mai sus, in `pregateste`, pe pagina formei (`are_forma`).
    iid = zot.add_item(cur, "statute", campuri_item(p), collection_id=cid, daca_exista="creeaza")
    aid = zot.attach_pdf(cur, iid, tinta)
    # Textul intră în cache-ul memoizat în ACELAȘI ciclu, după rețeta „sursă nouă" din
    # `pdf-text-cache.md`: item, fișier și text. Fără pasul ăsta, prima căutare de precedent pe
    # act găsea cache-ul gol și plătea extragerea plus încă un ciclu de închidere a lui Zotero.
    #
    # `ensure_cached` primește CHEIA atașamentului, nu itemID-ul întors de `attach_pdf`, și
    # extrage din PDF-ul abia scris, cu marcajele de pagină pe care le așteaptă citarea. Textul
    # din memorie nu se poate da direct: funcția nu primește text, iar o scriere proprie a notei
    # ar fi a doua implementare a formatului ei.
    #
    # Se extrage din `tinta`, fișierul nostru, nu din copia din `storage`: capcana documentată
    # acolo e o notă cu hash corect și ZERO caractere, scrisă când copia se citea goală — iar o
    # asemenea notă pare proaspătă la nesfârșit. Pregătirea a verificat deja că actul are text,
    # deci un text gol aici e chiar capcana, și se refuză: excepția anulează tot ciclul, adică
    # nu rămâne nici itemul fără cache, nici nota goală.
    text, _ = pdf_text_cache.ensure_cached(cur, zot.key_for_item_id(cur, aid), tinta)
    if len((text or "").strip()) < PRAG_TEXT:
        raise RuntimeError(
            "Textul memoizat pentru %s are %d caractere, deși pregătirea a găsit text. Ciclul se "
            "anulează, deci nu s-a scris nimic; reia rularea." % (a.scurt, len((text or "").strip())))
    print("text memoizat în cache: %d caractere" % len(text))
    # Highlight-urile intră în ACELAȘI ciclu cu actul: un ciclu de închidere per sarcină. Un
    # fragment care trece pe pagina următoare dă câte un highlight pe pagină, cu același
    # comentariu, fiindcă o adnotare Zotero stă pe o singură pagină.
    cheia_pdf = zot.key_for_item_id(cur, aid)
    for loc, comentariu, b in p.get("evidentieri", []):
        zot.adauga_evidentiere(cur, cheia_pdf, comentariu=comentariu, **b)
    if p.get("evidentieri"):
        print("highlight-uri scrise: %d, pe %s"
              % (len(p["evidentieri"]), ", ".join(dict.fromkeys(l for l, _, _ in p["evidentieri"]))))
    if a.la_data:
        # Formele aceluiași act se leagă între ele ca „Related". Fără legătură, cine
        # deschide o formă n-are de unde ști că mai există altele — iar asta e chiar
        # întrebarea de pus înainte de a cita un articol dintr-un act modificat des.
        cheia_noua = zot.key_for_item_id(cur, iid)
        legate = []
        for k, titlu, _, _ in surori:
            if zot.relateaza(cur, cheia_noua, k):
                legate.append((k, forma_item(k, cur) or "?"))
        if legate:
            # Se NUMESC, nu se numără: un număr nu arată dacă s-a legat de ce trebuie.
            print("legată ca «Related» de %d formă/forme ale aceluiași act:" % len(legate))
            for k, d in legate:
                print("  %s — %s" % (k, d))
    print("item creat, filat în colecția %r" % a.colectie if cid else "item creat (fără colecție)")

def main():
    global TIMEOUT
    p = argparse.ArgumentParser()
    p.add_argument("id_baza", nargs="?", default="",
                   help="id-ul paginii actului de bază pe legislatie.just.ro")
    p.add_argument("--lista", default="", metavar="FISIER.json",
                   help="mai multe acte, depuse într-un singur ciclu de scriere")
    p.add_argument("--denumire", default="")
    p.add_argument("--cod", default="", help="ex. 273/2006")
    p.add_argument("--scurt", default="", help="ex. Legea 273/2006")
    p.add_argument("--emitent", default="Parlamentul României")
    p.add_argument("--data", default="", help="dateEnacted, ex. 2006-06-29 29 iunie 2006")
    # Fără colecție implicită: „cadru normativ" era colecția proiectului ASEMT, iar un act depus
    # în colecția altui proiect se găsește mult mai greu decât o eroare. Trimiterea din `hcl-asemt`
    # o pune singură, ca înainte.
    p.add_argument("--colectie", default="",
                   help="colecția în care se filează: nume, cale („proiect/cadru normativ”) sau cheie")
    p.add_argument("--forma-publicata", action="store_true",
                   help="cu --la-data: textul de pe pagina de formă publicată (Afis) a id-ului dat, de pildă o "
                        "republicare, pe care lista de forme a Portalului n-o arată")
    p.add_argument("--la-item", default="", metavar="CHEIE",
                   help="atașează forma adusă la acest item existent, fără fișier, în loc să creeze unul nou")
    p.add_argument("--la-data", default="", metavar="AAAA-LL-ZZ",
                   help="forma in vigoare la data asta, nu ultima consolidare")
    p.add_argument("--doar-verifica", action="store_true")
    p.add_argument("--cauta", default="", metavar="„Legea 207/2015”",
                   help="caută pe portal id-urile actelor de tipul, numărul și anul date")
    p.add_argument("--scrie-direct", action="store_true",
                   help="scrie în bază acum (Zotero închis), nu prin coadă; numai cu confirmarea autorului")
    p.add_argument("--confirma", action="store_true",
                   help="cu --lista: scrie în bază după ce ai citit tabelul formelor alese")
    p.add_argument("--evidentiaza", nargs=2, action="append", default=[],
                   metavar=("LOC", "COMENTARIU"),
                   help="subliniază „art. 7” sau „art. 11 alin. (1)” în PDF, cu comentariul dat; "
                        "se repetă pentru fiecare loc. În --lista: \"evidentiaza\": [[loc, comentariu], …]")
    p.add_argument("--timeout", type=int, default=TIMEOUT, metavar="SECUNDE",
                   help="așteptarea la prima încercare pe portal (implicit %(default)s s); "
                        "crește cu atât la fiecare reluare")
    a = p.parse_args()
    TIMEOUT = a.timeout

    if a.cauta:
        nume, numar, an, gasite = cauta_act(a.cauta)
        if not gasite:
            print("Niciun act %s %s din %s în primele pagini de rezultate." % (nume, numar, an))
            print("Verifică tipul și numărul; portalul filtrează după ele, nu după an.")
            return 2
        print("%d potrivire(i) pentru %s %s/%s:" % (len(gasite), nume, numar, an))
        for ident, titlu in gasite:
            print("  %-8s %-34s https://legislatie.just.ro/Public/DetaliiDocument/%s"
                  % (ident, titlu, ident))
        print("\nId-ul se dă apoi ca prim argument, sau ca `id_baza` într-un fișier de listă.")
        return 0
    specuri = [a]
    if getattr(a, "lista", ""):
        # Un fișier cu mai multe acte. Fiecare rând poate da orice câmp; ce lipsește se ia din
        # argumentele comune ale rulării, ca să nu se repete colecția și emitentul la fiecare.
        if a.evidentiaza:
            raise SystemExit("Cu --lista, locurile de subliniat se dau la fiecare act, în fișier: "
                             "\"evidentiaza\": [[\"art. 7\", \"comentariu\"], …]. Din linia de "
                             "comandă s-ar fi pus pe toate actele.")
        randuri = json.load(io.open(a.lista, encoding="utf-8"))
        if not isinstance(randuri, list) or not randuri:
            raise SystemExit("Fișierul %r trebuie să fie o listă cu cel puțin un act." % a.lista)
        specuri = []
        for n, r in enumerate(randuri, 1):
            s = argparse.Namespace(**vars(a))
            for k, v in r.items():
                setattr(s, k.replace("-", "_"), v)
            lipsa = [c for c in ("id_baza", "denumire", "cod") if not getattr(s, c, "")]
            if lipsa:
                raise SystemExit("Actul %d din %r n-are: %s" % (n, a.lista, ", ".join(lipsa)))
            specuri.append(s)
    elif not a.id_baza:
        raise SystemExit("Dă id-ul paginii actului, sau `--lista fisier.json` pentru mai multe.")
    # Titlul scurt lipsă se deduce din denumire și cod („Legea 76/2002”) și se SPUNE. Un nume
    # consacrat („Codul fiscal") nu se deduce; acela se dă cu `--scurt`.
    for s in specuri:
        if not getattr(s, "scurt", "") and not s.doar_verifica:
            s.scurt = scurt_implicit(s.denumire, s.cod) or ""
            if s.scurt:
                print("titlu scurt dedus din denumire și cod: %r (altul se dă cu --scurt)" % s.scurt)

    # Colecția se rezolvă O DATĂ, înaintea ciclului, cu `find_collection`: ea potrivește pe
    # perechea (nume, părinte), acceptă cale sau cheie, și ridică eroare la nume ambiguu.
    cid = None
    if not a.colectie and not a.doar_verifica:
        raise SystemExit("Dă colecția: `--colectie \"proiect/cadru normativ\"` (nume, cale sau cheie).\n"
                         "Nu depun actele fără colecție: un item rătăcit se găsește mult mai greu "
                         "decât o eroare.")
    if a.colectie:
        gasita = zot.find_collection(a.colectie)
        if not gasita:
            raise SystemExit(
                'Colecția %r nu există. Creeaz-o întâi, sau dă-i calea '
                '(„proiect/subcolecție”) ori cheia.\n'
                'Nu depun actele într-o colecție aleasă de mine: un item rătăcit se găsește '
                'mult mai greu decât o eroare.' % a.colectie)
        cid = gasita[1]
    for s in specuri:
        s._cid = cid

    # Prima trecere a unui lot nu scrie nimic — deci nu cere nici aplicația închisă. Ea aduce
    # actele și arată formele alese; scrierea vine la a doua, cu `--confirma`. Cu Zotero deschis,
    # actele se aduc oricum, iar scrierea se lasă în `scrieri_amanate` (vezi `amana`).
    # Scrierea merge MEREU prin coadă: orice scriere în bază așteaptă confirmarea autorului (regula lui). Înainte,
    # cu Zotero închis, unealta scria direct — pe 13.10.2026 a scris un act fără confirmare, doar fiindcă Zotero
    # fusese închis între timp. Scrierea directă rămâne numai cu `--scrie-direct`, cerută explicit.
    amanare = not getattr(a, "scrie_direct", False)
    if not a.doar_verifica and not (getattr(a, "lista", "") and not a.confirma) and amanare:
        print("Aduc actele acum, iar scrierea o las în scrieri amânate; o face ciclul de închidere confirmat de "
              "autor (`python scrieri_amanate.py --ruleaza`).")

    pregatite, oprite = [], []
    for n, s in enumerate(specuri, 1):
        if len(specuri) > 1:
            print("\n[%d/%d] %s" % (n, len(specuri), s.scurt))
        try:
            gata = pregateste(s)
        except (Exception, SystemExit) as e:
            # Un act care nu se poate aduce nu omoară lotul: celelalte s-au adus deja, iar
            # pierderea lor costa o rulare întreagă (9.10.2026, Codul fiscal într-un lot de cinci).
            # Refuzurile din `pregateste` sunt `SystemExit`, pe care `Exception` nu-l prinde: un
            # singur refuz oprea tăcut restul lotului (12.10.2026).
            if len(specuri) == 1:
                raise
            print("  CĂZUT: %s — %s: %s" % (s.scurt, type(e).__name__, e))
            oprite.append("%s (căzut: %s)" % (s.scurt, type(e).__name__))
            continue
        if gata is None and getattr(s, "evidentiaza", None) and not s.doar_verifica:
            # Actul era deja în bibliotecă, deci nu se depune nimic — nici highlight-urile. Se
            # spune, fiindcă altfel cererea pare împlinită.
            print("  ATENȚIE: highlight-urile cerute NU s-au pus — actul e deja în bibliotecă. Pe "
                  "un act existent se pun cu `zot.gaseste_fragment` + `zot.adauga_evidentiere`.")
        if gata is OPRIT:
            oprite.append(s.scurt)
        elif gata:
            pregatite.append(gata)

    # Refuzurile se NUMESC la final, nu doar la actul lor: într-un lot, mesajul unui act oprit
    # rămâne câteva ecrane mai sus, iar „Depuse 4 acte" pare să spună că s-a terminat tot.
    if oprite:
        print("\nOprite, nedepuse (%d): %s" % (len(oprite), ", ".join(oprite)))
    if not pregatite:
        print("\nNimic de depus.")
        return 2 if oprite else 0

    # TABELUL formelor alese, înaintea oricărei scrieri. Unealta alege singură forma fiecărui act,
    # iar alegerea nu se vede în ieșirea unui lot: pe 15 septembrie 2026 un act a intrat cu o formă
    # consolidată în 2019, cu modificări numai până în 2015, pentru o dată cerută din 2024, și
    # nimic n-a semnalat-o. Distanța dintre data cerută și ultima modificare poate fi firească —
    # actul chiar n-a mai fost modificat — sau poate arăta că s-a ales altceva decât trebuia;
    # cine poate spune care din două e autorul, deci lui i se arată, nu i se ascunde sub un prag.
    print("\nFormele alese:")
    print("  %-26s %-12s %-34s %s" % ("act", "cerut la", "forma aleasă", "ultima modificare"))
    for p in pregatite:
        if p.get("doar_colectie"):
            print("  %-26s %-12s %-34s" % (p["a"].scurt[:26], p["a"].la_data or "—",
                                          "deja în bibliotecă: %s, doar filare" % p["doar_colectie"]))
            continue
        print("  %-26s %-12s %-34s %s"
              % (p["a"].scurt[:26], p["a"].la_data or "forma de azi",
                 (p.get("eticheta") or "?")[:34], p.get("cea_mai_noua") or "necunoscută"))

    if len(specuri) > 1 and not a.confirma:
        print("\nNu scriu nimic: un lot se citește înainte de a intra în bibliotecă.")
        print("Dacă formele de mai sus sunt cele cerute, reia aceeași comandă cu `--confirma`.")
        return 3

    # UN SINGUR ciclu, oricâte acte. Ciclul închide și redeschide aplicația; câte unul per act
    # ar închide-o și ar redeschide-o de tot atâtea ori, iar al doilea l-ar prinde pe primul
    # pornind.
    if amanare:
        for p in pregatite:
            print("  lăsat în scrieri amânate: %s" % amana(gasita[0], p))
        print("\n%d act(e) lăsate pentru primul ciclu de închidere. Verificarea colecției se face "
              "după scriere: `python verifica_acte.py --colectie %s`." % (len(pregatite), gasita[0]))
        return 2 if oprite else 0
    eticheta = ("adu-lege-%s" % pregatite[0]["a"].cod.replace("/", "-") if len(pregatite) == 1
                else "adu-lege-%d-acte" % len(pregatite))
    try:
        with zot.write_session(eticheta) as cur:
            for p in pregatite:
                if p.get("doar_colectie"):
                    zot.add_to_collection(cur, zot.item_id_for_key(cur, p["doar_colectie"]), cid)
                    zot.touch(cur, zot.item_id_for_key(cur, p["doar_colectie"]))
                    print("%s filat în colecția %r" % (p["doar_colectie"], a.colectie))
                else:
                    depune(cur, cid, p)
    except sqlite3.DatabaseError as e:
        # Mesajul spune ÎNTÂI ce s-a întâmplat cu baza. „database disk image is malformed" citit
        # singur trimite omul să-și refacă biblioteca din copie, deși tranzacția s-a derulat
        # înapoi întreagă și baza e neatinsă — măsurat pe 16 septembrie 2026.
        raise SystemExit(
            "Scrierea a căzut: %s\n"
            "Tranzacția s-a derulat înapoi ÎNTREAGĂ, deci niciun act n-a intrat și baza a rămas "
            "cum era.\nVerifică, dacă vrei: `PRAGMA integrity_check` pe ~/Zotero/zotero.sqlite.\n"
            "În `~/Zotero/storage` pot rămâne dosare cu PDF-ul copiat înainte de derulare; ele "
            "n-au item și se pot șterge." % e)
    print("\nDepuse %d act(e) într-un singur ciclu de scriere." % len(pregatite))
    # Cealaltă jumătate: trec actele colecției prin lista de legislație? Se spune acum, nu la
    # construirea listei, când autorul trebuia să ceară verificarea de fiecare dată.
    print("\nVerificarea colecției pentru lista de legislație:")
    import verifica_acte
    verifica_acte.raport(gasita[0])
    return 2 if oprite else 0


if __name__ == "__main__":
    sys.exit(main())
