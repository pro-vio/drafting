# Cum se citează sursele online, după manual

Pagini web, bloguri, rețele sociale, **forumuri și liste de discuții**, conținut generat de un
model, comunicări personale. Regulile sunt ale Chicago Manual of Style, ediția a 18-a; ce scrie
aici e luat din materialul liber al manualului, adus pe disc cu `scripts/adu_chicago.py` și
păstrat în `references/chicago/`.

## Ce e liber și ce nu

Textul secțiunilor (`chicagomanualofstyle.org/book/ed18/…`) cere abonament: serverul răspunde cu
302 către ecranul de autentificare. Libere sunt două lucruri, iar împreună ajung:

- **Citation Quick Guide** — exemplele oficiale, pe feluri de sursă, în ambele sisteme;
- **cuprinsul capitolelor 13 și 14** — numărul și titlul exact al fiecărei secțiuni, deci se
  poate trimite la „CMOS 18, 14.107" fără să ghicești.

`scripts/adu_chicago.py` le aduce pe amândouă și scrie un `INDEX.md` cu data aducerii. Rulează-l
înainte să te bizui pe ce scrie mai jos, dacă au trecut luni: `python scripts/adu_chicago.py`.

## Secțiunile care ne privesc

| Secțiune | Ce acoperă |
|---|---|
| 14.103 | Titlurile siturilor, blogurilor și ale rețelelor sociale |
| 14.104 | Pagini web și situri |
| 14.105 | Postări de blog și bloguri |
| 14.106 | Conținut de pe rețele sociale |
| **14.107** | **Liste de discuții și forumuri electronice** |
| 14.111 | Comunicări personale |
| 14.112 | Conținut generat de un model |

## Forma, autor–dată

Exemplele sunt cele din Quick Guide, cuvânt cu cuvânt:

> NASA Webb Telescope (@NASAWebb). 2022. "👀 Sneak a peek at the deepest & sharpest infrared image
> of the early universe ever taken—all in a day's work for the Webb telescope. (Literally,
> capturing it took less than a day!)." Twitter (now X), July 11.
> https://twitter.com/NASAWebb/status/1546621080298835970.

> Chicago Manual of Style. 2015. "Is the world ready for singular they? We thought so back in
> 1993." Facebook, April 17, 2015.
> https://www.facebook.com/ChicagoManual/posts/10152906193679151.

Deci: **autorul așa cum semnează, anul, titlul sau textul postării între ghilimele, situl, ziua
și luna, adresa.** Un fir de forum se citează la fel, cu titlul firului în locul textului.

Patru lucruri care se greșesc des:

1. **Numele de utilizator e autorul.** Nu se înlocuiește cu situl și nu se scoate. Manualul
   citează până și un comentariu pe numele celui care l-a scris: „(Michele Truty, April 17, 2015,
   1:09 p.m., comment on Chicago Manual of Style 2015)". Numele se ia litera cu literă cum apare
   — inclusiv minuscule — și **nu se inversează**: e un nume de utilizator, nu un nume de autor
   de carte. Alfabetizarea în listă merge după forma scrisă.
2. **Fără dată de acces, când există dată.** Data accesării intră numai dacă nu se poate
   determina nicio dată de publicare sau revizuire; atunci anul devine `n.d.` (14.104). O
   postare datată n-o cere.
3. **Comentariul se citează prin postarea la care stă**, nu singur.
4. **Anul stă după autor, iar ziua și luna la sursă.** Al doilea exemplu de mai sus repetă anul;
   primul nu. Când anul e deja după autor, nu se mai repetă.

Iar când o postare e pomenită o singură dată și în treacăt, manualul spune că **ajunge textul**:
„Citations of content posted to social media can usually be limited to the text." Lista de
referințe e pentru cazul în care sursa chiar poartă o afirmație din lucrare.

## În Zotero

Tipul de item e `forumPost`: `title`, `forumTitle`, `postType`, `date`, `url`, `language`.
Autorul se scrie literal (`fieldMode 1`), cu numele de utilizator întreg într-un singur câmp —
`zot.add_item(..., creators=[("author", "kysty18")])`. Pe un item existent,
`zot._set_creators(cur, item_id, [("author", "…")])`, într-o sesiune de scriere.
