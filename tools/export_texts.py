#!/usr/bin/env python3
"""Regenerate the plain-text edition of the JES books.

    python3 tools/export_texts.py

Reads the book pages and about.html from the repository root and rewrites
texts/ and llms.txt. The output is deterministic: a second run changes nothing.

Every block of text is numbered (¶n). A paragraph, a block-quoted paragraph,
a verse block, and a list item each count as one block. Section breaks (* * *)
and figure captions ([Figure: ...]) are printed unnumbered; images are omitted. Block numbering for
Various Asides follows the JES corpus reader exactly; cite as [1.4 ¶12], or
[hebdomad/part-iii ¶7] for the other books.
"""
import os
import re
import shutil
import sys
import textwrap

from bs4 import BeautifulSoup, NavigableString, Tag

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://j-e-scriptorium.github.io"
WIDTH = 100

VA = "various-asides"
VA_CREDIT = "Various Asides (2026), ed. Ludovico Ambrosius"

# Other books, in site order: page stem -> (short title, credit line, principal author).
BOOKS = {
    "hebdomad": (
        "Hebdomad",
        "Hebdomad: A World Historical Shitpost (2022), by Jørgen Agosto Comgall, "
        "edited with an introduction by Ludovico Ambrosius",
        "Jørgen Agosto Comgall"),
    "reach-of-poetry": (
        "The Reach of Post-Romantic Poetry",
        "The Reach of Post-Romantic Poetry (2025), by Edkar Marenko",
        "Edkar Marenko"),
    "o-honeybees": (
        "O Honeybees",
        "O Honeybees: An Illustrated Anthology of Bee Pomes (2023), "
        "edited with an introduction by Ludovico Ambrosius",
        "ed. Ludovico Ambrosius"),
    "roll-strike-die": (
        "Roll, Strike, Die, and Other Poems",
        "Roll, Strike, Die, and Other Poems (2021), by Ludovico Ambrosius",
        "Ludovico Ambrosius"),
    "poems-handturned": (
        "Poems Handturned, Machine-Tooled",
        "Poems Handturned, Machine-Tooled (2024), by Ludovico Ambrosius",
        "Ludovico Ambrosius"),
    "near-as-we-can-tell": (
        "Near as We Can Tell",
        "Near as We Can Tell: Natural Histories in Verse (2026), by A. Clanker, "
        "introduction by Ludovico Ambrosius",
        "A. Clanker"),
    "coke-reports-verse": (
        "The Reports of Sir Edward Coke, Kt. In Verse",
        "The Reports of Sir Edward Coke, Kt. In Verse (1742; digitized edition 2026), "
        "by an anonymous 18th c. poet, edited by A. Clanker as directed by E. Lawrence Hayward",
        "anonymous 18th c. poet"),
}

# Content that sits in <main> outside any top-level <section> but belongs to
# the book rather than its navigation: annotated contents, a facsimile title
# page, closing notes on authors. Each entry is exported as one file, placed in
# document order at its first match: page stem -> [(css selector, file id, title)].
# Title pages, plain tables of contents, and colophons outside sections are not
# exported.
EXTRA = {
    "reach-of-poetry": [("main > div.author-bio", "end-matter", "End Matter")],
    "o-honeybees": [("main > div.author-bio", "end-matter", "End Matter")],
    "near-as-we-can-tell": [("main > div.toc", "contents", "Annotated Table of Contents")],
    "coke-reports-verse": [
        ("main > div.book-title:not(#top)", "title-page", "Title Page (1742)"),
        ("main > div.book-footer", "end-matter", "End Matter"),
    ],
}

SIGNATURES = ("signature", "preface-sig")
APPARATUS = {"contents", "title-page", "end-matter", "index"}  # no author note in the indexes
INDENT_CHARS = "     "
BLOCK_TAGS = {"p", "div", "section", "ul", "ol", "blockquote", "pre", "table", "dl",
              "figure", "h1", "h2", "h3", "h4", "h5", "h6"}


# --- text extraction (same logic as the JES corpus reader) -------------------

PRE_CLASSES = {"poem-line", "poem-speaker"}  # styled white-space: pre-wrap


def inline(el, pre=False):
    """Inline text with *italics* and **bold**. Only <br> breaks a line, except
    inside <pre> and pre-wrap verse lines, where source newlines are kept."""
    if isinstance(el, NavigableString):
        return str(el) if pre else str(el).replace("\n", " ")
    if not isinstance(el, Tag) or el.name in ("img", "script", "style"):
        return ""
    if el.name == "br":
        return "\n"
    pre = pre or el.name == "pre" or bool(PRE_CLASSES & set(el.get("class") or []))
    inner = "".join(inline(c, pre) for c in el.children)
    if el.name in ("em", "i") and inner.strip():
        return f"*{inner}*"
    if el.name in ("strong", "b") and inner.strip():
        return f"**{inner}**"
    return inner


def clean(s):
    s = re.sub(r"[^\S\n]+", " ", s)
    return re.sub(r" *\n *", "\n", s).strip()


def slug(s, limit=48):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    if len(s) > limit:
        cut = s[:limit + 1]
        s = cut[:cut.rfind("-")] if "-" in cut else s[:limit]
    return s


def verse_lines(raw):
    """Clean each line of verse, keeping leading non-breaking-space indentation."""
    out = []
    for line in raw.split("\n"):
        body = line.lstrip(" \t\r")
        lead = len(body) - len(body.lstrip(INDENT_CHARS))
        text = clean(body)
        out.append((" " * lead + text) if text else "")
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def verse_of(el):
    if not el.find("p"):
        return verse_lines(inline(el))
    return "\n".join(verse_lines(inline(p)) for p in el.find_all("p"))


def case_of(el):
    """Coke's Reports: one numbered case, its heading and its couplets, as one verse block."""
    lines = []
    h = el.find(["h4", "h5"])
    head = clean(inline(h)) if h else ""
    if head or el.get("id"):
        lines.append(" ".join(x for x in (f"**{head}**" if head else "",
                                          f"(#{el['id']})" if el.get("id") else "") if x))
    for vb in el.select(".verse-block"):
        lines.extend(verse_block_lines(vb))
    return "\n".join(lines)


def verse_block_lines(vb):
    folio = vb.select_one(".entry-folio")
    lines = [clean(inline(v)) for v in vb.select(".verse-line")]
    if folio and lines:
        lines[0] = f"[fol. {clean(folio.get_text())}] {lines[0]}"
    return lines


def is_leaf_div(el):
    return not any(isinstance(c, Tag) and c.name in BLOCK_TAGS for c in el.children)


def blocks_of(container, skip=()):
    """Yield (kind, text) for the block-level content of a prose container.

    Matches the JES corpus reader block for block (its text differs only where
    the reader turns a source newline into a line break the page does not show); the branches marked (ext)
    only fire on markup the reader would otherwise drop, none of which occurs
    in Various Asides.
    """
    for el in container.children:
        if not isinstance(el, Tag):
            continue
        cls = el.get("class") or []
        if "byline" in cls or el.name == "img" or id(el) in skip:
            continue
        if "section-break" in cls:
            yield ("break", el.get("id", ""))
        elif el.name == "blockquote":
            for kind, t in blocks_of(el, skip):
                if kind == "verse":
                    yield ("quote-verse", t)
                elif kind not in ("break", "figure"):
                    yield ("quote", t)
        elif "line-block" in cls or "poem" in cls or "stanza" in cls:
            yield ("verse", verse_of(el))
        elif el.name in ("ul", "ol"):
            for li in el.find_all("li", recursive=False):
                yield ("item", clean(inline(li)))
        elif el.name in ("p", "h3", "h4", "h5", "pre"):
            t = clean(inline(el))
            if t:
                yield ("p", t)
        elif "case" in cls:                                            # (ext)
            yield ("verse", case_of(el))
        elif "verse-block" in cls:                                     # (ext)
            yield ("verse", "\n".join(verse_block_lines(el)))
        elif el.name == "figure":                                      # (ext)
            cap = el.find("figcaption")
            if cap and clean(inline(cap)):
                yield ("figure", f"[Figure: {clean(inline(cap))}]")
        elif el.name == "table":                                       # (ext)
            for tr in el.find_all("tr"):
                cells = [clean(inline(c)) for c in tr.find_all(["th", "td"])]
                if any(cells):
                    yield ("item", " | ".join(cells))
        elif el.name == "dl":                                          # (ext)
            for dt in el.find_all("dt", recursive=False):
                dd = dt.find_next_sibling(["dd", "dt"])
                term = clean(inline(dt))
                yield ("item", f"{term}: {clean(inline(dd))}" if dd and dd.name == "dd" else term)
        elif el.name in ("div", "section"):
            if is_leaf_div(el) and clean(inline(el)):                  # (ext)
                yield ("p", clean(inline(el)))
            else:
                yield from blocks_of(el, skip)


def foreword_glosses(soup):
    glosses = {}
    fw = soup.select_one("#essay-0-3 .prose")
    if not fw:
        return glosses
    for p in fw.find_all("p"):
        if not p.find("strong"):
            continue
        html = str(p)
        last = 0
        for m in re.finditer(r'<a href="#essay-(\d+)-(\d+)">.*?</a>', html):
            frag = re.sub(r"<strong>.*?</strong>", "", html[last:m.start()])
            seg = clean(BeautifulSoup(frag, "html.parser").get_text(" "))
            seg = re.split(r"(?<=[a-z]{2}[.!?])\s+(?=[A-Z])", seg)[-1]
            seg = re.sub(r"^(Finally,\s*)?(I myself,?\s*|I have (also )?)?", "", seg)
            seg = re.sub(r"^[\s;,.]*((and|also|of course)[\s,]+)*", "", seg).strip(" ;,")
            glosses[f"{m.group(1)}.{m.group(2)}"] = seg
            last = m.end()
    return glosses


# --- rendering ---------------------------------------------------------------

def wrap(text, first, rest):
    return textwrap.wrap(text, WIDTH, initial_indent=first, subsequent_indent=rest,
                         break_long_words=False, break_on_hyphens=False) or [first.rstrip()]


def render(kind, text, tag):
    lead = tag + " "
    if kind in ("verse", "quote-verse"):
        q = "> " if kind == "quote-verse" else ""
        lines = [(q + "    " + l) if l else q.rstrip() for l in text.split("\n")]
        lines[0] = lead + lines[0]
        return "\n".join(l.rstrip() for l in lines)
    first, rest = {"p": ("", ""), "quote": ("> ", "> "), "item": ("- ", "  ")}[kind]
    out = []
    for i, para in enumerate(text.split("\n")):
        out.extend(wrap(para, (lead if i == 0 else "") + first, rest))
    return "\n".join(l.rstrip() for l in out)


def header_line(s):
    return "\n".join(wrap(s, "", "  "))


def body(blocks, disavowals=None):
    """Render blocks; return (text, block count, word count)."""
    parts, n, wc, anchor = [], 0, 0, disavowals
    for kind, t in blocks:
        if kind == "figure":
            parts.append(t)
            continue
        if kind == "break":
            if disavowals:
                anchor = t or anchor
            else:
                parts.append("* * *")
            continue
        n += 1
        wc += len(t.split())
        tag = f"¶{n}" + (f" (#{anchor})" if disavowals else "")
        parts.append(render(kind, t, tag))
    return "\n\n".join(parts), n, wc


def write(path, header, text):
    content = header + "\n\n" + text + "\n" if text else header + "\n"
    assert not content.startswith("---"), path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def load(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return BeautifulSoup(f, "html.parser")


def plain(s):
    return re.sub(r"\*+", "", s)


# --- books -------------------------------------------------------------------

def byline_parts(by):
    return [clean(x) for x in inline(by).split("\n") if clean(x)] if by else []


def export_va(out):
    soup = load(VA + ".html")
    glosses = foreword_glosses(soup)
    entries = []
    for sec in soup.select("section.essay"):
        m = re.match(r"essay-(\d+)-(\d+)$", sec.get("id", ""))
        if not m:
            continue
        eid = f"{m.group(1)}.{m.group(2)}"
        title = clean(inline(sec.select_one("h2")))
        lines = byline_parts(sec.select_one(".byline"))
        if eid == "6.2":
            names, rest = lines[:-2], lines[-2:]
            author = "the Committee"
            credit = "The Committee: " + "; ".join(names)
        else:
            author = lines[0] if lines else ""
            credit, rest = author, lines[1:]
        places = [" — ".join(rest[i:i + 2]) for i in range(0, len(rest), 2)]
        author_line = credit + (" — " + "; ".join(places) if places else "")
        fname = f"{m.group(1)}-{m.group(2)}-{slug(plain(title))}.txt"
        sid = sec["id"]
        head = [f"# [{eid}] {title}"]
        if author_line:
            head.append(header_line(author_line))
        head.append(f"From: {VA_CREDIT}")
        head.append(f"URL: {SITE}/{VA}.html#{sid}")
        if eid in glosses:
            head.append(header_line(f"Foreword gloss: {glosses[eid]}"))
        prose = sec.select_one(".prose") or sec
        text, n, wc = body(blocks_of(prose), disavowals=sid if eid == "6.1" else None)
        write(os.path.join(out, VA, fname), "\n".join(head), text)
        entries.append(dict(id=eid, title=title, author=author or "—", words=wc, blocks=n,
                            path=f"{VA}/{fname}", gloss=glosses.get(eid, ""), anchor=sid))
    return entries


def section_title(sec):
    h = sec.find(["h1", "h2", "h3"])
    if h and clean(inline(h)):
        return clean(inline(h)).replace("\n", " ")
    return (sec.get("id") or "untitled").replace("-", " ").title()


def export_book(out, book):
    short, credit, principal = BOOKS[book]
    soup = load(book + ".html")
    main = soup.select_one("main") or soup.body
    extras = {}
    for sel, fid, title in EXTRA.get(book, []):
        for el in soup.select(sel):
            extras[id(el)] = (sel, fid, title)
    units, seen = [], set()
    for el in main.find_all(True):
        if id(el) in extras:
            sel, fid, title = extras[id(el)]
            if sel not in seen:
                seen.add(sel)
                units.append((soup.select(sel), fid, title))
        elif el.name == "section" and not el.find_parent("section"):
            units.append(([el], el.get("id") or slug(section_title(el)), section_title(el)))
    entries, seq = [], 0
    for els, fid, title in units:
        el = els[0]
        sigs = [s for s in el.find_all(class_=SIGNATURES)
                if el.name == "section" and s.find_parent("section") is el]
        skip = {id(s) for s in sigs}
        if el.name != "section":
            skip |= {id(h) for e in els for h in e.find_all(["h1", "h2"])}
        text, n, wc = body(b for e in els for b in blocks_of(e, skip))
        if not n:
            continue
        seq += 1
        fname = f"{seq:02d}-{fid}.txt"
        sid = el.get("id")
        url = f"{SITE}/{book}.html" + (f"#{sid}" if sid else "")
        author = " — ".join(byline_parts(sigs[0])) if sigs else ""
        head = [f"# [{book}/{fid}] {title}"]
        if author:
            head.append(header_line(author))
        head.append(header_line(f"From: {credit}"))
        head.append(f"URL: {url}")
        write(os.path.join(out, book, fname), "\n".join(head), text)
        entries.append(dict(id=f"{book}/{fid}", title=title,
                            author=byline_parts(sigs[0])[0] if sigs else
                            "—" if fid in APPARATUS else principal,
                            words=wc, blocks=n, path=f"{book}/{fname}", gloss="", anchor=sid))
    return short, credit, entries


def export_about(out):
    soup = load("about.html")
    main = soup.select_one("main")
    text, n, wc = body(blocks_of(main))
    head = ["# About the St. Isidore University Press",
            "From: Jsidoris Euniversitatis Scriptorium",
            f"URL: {SITE}/about.html"]
    write(os.path.join(out, "about.txt"), "\n".join(head), text)
    return dict(id="about", title="About the St. Isidore University Press", author="—",
                words=wc, blocks=n, path="about.txt", gloss="", anchor=None)


# --- indexes -----------------------------------------------------------------

def write_index(out, books, about):
    lines = ["# JES plain-text edition — index", "",
             "Generated by tools/export_texts.py from the book pages of " + SITE + ".",
             "Paths are relative to " + SITE + "/texts/.",
             "Each entry: id | title | author | words | path.",
             "Cite blocks as [1.4 ¶12] for Various Asides, [hebdomad/part-iii ¶7] for the other books."]
    for credit, entries in books:
        total = sum(e["words"] for e in entries)
        lines += ["", f"## {credit}", f"{len(entries)} files, {total} words", ""]
        for e in entries:
            lines.append(header_line(f"[{e['id']}] | {e['title']} | {e['author']} | {e['words']} | "
                                     f"{e['path']}").replace("\n  ", "\n      "))
            if e["gloss"]:
                lines.append(header_line(f"    gloss: {e['gloss']}").replace("\n  ", "\n      "))
    lines += ["", "## About the press", "",
              f"[about] | {about['title']} | — | {about['words']} | {about['path']}"]
    with open(os.path.join(out, "index.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def write_llms(books, about):
    base = SITE + "/texts/"
    lines = [
        "# St. Isidore University Press (Jsidoris Euniversitatis Scriptorium)", "",
        f"> The St. Isidore University Press (Jsidoris Euniversitatis Scriptorium) is a heteronymous "
        f"press whose faculty are listed in [about.html]({SITE}/about.html).", "",
        f"This is a plain-text edition of its books, one UTF-8 file per essay or section. Each file opens with a header giving the title, author, book, and the URL of the section "
        f"on the site, followed by numbered blocks (¶n): one per paragraph, quoted paragraph, "
        f"verse block, or list item. Cite as [1.4 ¶12] for Various Asides essay 1.4, block 12, or "
        f"[hebdomad/part-iii ¶7] for the other books. The faculty are described in "
        f"[About the press]({base}{about['path']}).",
    ]
    for credit, entries in books:
        lines += ["", f"## {plain(credit)}", ""]
        for e in entries:
            note = e["author"] if e["author"] != "—" else ""
            if e["gloss"]:
                note = f"{note}; {e['gloss']}" if note else e["gloss"]
            item = f"- [{plain(e['title'])}]({base}{e['path']})"
            lines.append(item + (f": {plain(note)}" if note else ""))
    lines += ["", "## Index", "",
              f"- [Index of all text files]({base}index.txt): every file with id, title, author, "
              f"word count, and path"]
    with open(os.path.join(REPO, "llms.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def main():
    out = os.path.join(REPO, "texts")
    if os.path.isdir(out):
        shutil.rmtree(out)
    books = [(VA_CREDIT, export_va(out))]
    for book in BOOKS:
        _, credit, entries = export_book(out, book)
        books.append((credit, entries))
    about = export_about(out)
    write_index(out, books, about)
    write_llms(books, about)
    for credit, entries in books:
        print(f"{sum(e['words'] for e in entries):7d} words  {len(entries):3d} files  {plain(credit)[:60]}")


if __name__ == "__main__":
    sys.exit(main())
