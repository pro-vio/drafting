# -*- coding: utf-8 -*-
"""validate_docx — the mandatory check after writing tracked changes.

A hand-edited document.xml can be subtly malformed in ways Word will silently repair or
choke on. Before handing a .docx back, prove three things with pandoc:
  1. it opens at all            -> `pandoc f.docx -t plain`
  2. accepting changes works    -> `pandoc f.docx --track-changes=accept`   (your edits appear)
  3. rejecting changes works    -> `pandoc f.docx --track-changes=reject`   (original returns)

Usage:
    python validate_docx.py <file.docx> [--show accept|reject|plain] [--grep "text"]

Exit code 0 = all three render. Non-zero = something is broken (see stderr).
With --show it also prints that rendering so you can eyeball the result; --grep highlights
whether a string is present in the accept rendering (handy to confirm an insertion landed).
"""
import sys, subprocess, argparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def run(docx, mode):
    args = ["pandoc", docx, "-t", "plain"]
    if mode in ("accept", "reject"):
        args += [f"--track-changes={mode}"]
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("--show", choices=["accept", "reject", "plain"])
    ap.add_argument("--grep")
    a = ap.parse_args()

    ok = True
    renders = {}
    for mode in ("plain", "accept", "reject"):
        rc, out, err = run(a.docx, mode)
        renders[mode] = out
        status = "OK" if rc == 0 else "FAIL"
        if rc != 0:
            ok = False
        print(f"  [{status}] pandoc --track-changes={mode if mode!='plain' else '(none)'}  ({len(out)} chars)")
        if rc != 0 and err:
            print("        " + err.strip().splitlines()[-1])

    if a.grep:
        present = a.grep in renders.get("accept", "")
        print(f"\n  grep «{a.grep[:50]}» in accepted text: {'FOUND' if present else 'NOT FOUND'}")

    if a.show:
        print("\n" + "=" * 60 + f"\n--track-changes={a.show}\n" + "=" * 60)
        print(renders[a.show])

    if not ok:
        print("\nINVALID — pandoc could not render one of the modes. The docx is malformed.")
        sys.exit(1)
    print("\nVALID — opens, accepts, and rejects cleanly.")


if __name__ == "__main__":
    main()
