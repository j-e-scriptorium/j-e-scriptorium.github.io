# j-e-scriptorium.github.io

GitHub Pages site, deployed from `main` with Pages' default Jekyll. No file has front matter and
there is no `_config.yml` or `.nojekyll`, so everything is copied verbatim; keep it that way.

## Plain-text edition

`texts/` and `llms.txt` are generated from the book pages and `about.html` by
`tools/export_texts.py` (Python 3, BeautifulSoup). Never edit them by hand.

After any change to a book page (`various-asides.html`, `hebdomad.html`, `reach-of-poetry.html`,
`o-honeybees.html`, `roll-strike-die.html`, `poems-handturned.html`, `near-as-we-can-tell.html`,
`coke-reports-verse.html`) or to `about.html`, run

    python3 tools/export_texts.py

and commit `texts/` and `llms.txt` in the same commit as the page change.

A new book page also needs an entry in `BOOKS` in the exporter. Text files stay `.txt`: a `.md`
file would be turned into a Jekyll page and run through Liquid.
