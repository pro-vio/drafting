# drafting

A Claude Code plugin for producing and revising a manuscript end to end: Word (.docx) edits as tracked changes,
and references managed with Zotero as the single source of truth (live citations, an auto-generated reference list
through pandoc + citeproc with any CSL style, and a chronological legislation list that CSL cannot express). It also
brings Romanian legislation into Zotero from the Portal Legislativ, verifies item metadata against the source text,
and memoizes PDF text extraction.

## Install

```
/plugin marketplace add pro-vio/drafting
/plugin install drafting@drafting
/reload-plugins
```

Requires Zotero (local API) with Better BibTeX, and pandoc on PATH. The workflow is in
`plugins/drafting/skills/drafting/SKILL.md`. The Chicago Manual of Style reference pages are not included; fetch them
with `scripts/adu_chicago.py`.

The plugin's documentation is mostly in Romanian.

This repository is a published copy, updated from time to time from a private source; changes made here directly
are overwritten.
