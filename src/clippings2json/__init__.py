#!/usr/bin/env python3
"""clippings2json: turn a Kindle "My Clippings.txt" into clean JSON.

    clippings2json "My Clippings.txt"              > clippings.json   # grouped by book
    clippings2json "My Clippings.txt" --flat       > clippings.json   # one record per clipping
    clippings2json "My Clippings.txt" --book dune                     # only matching books
    clippings2json "My Clippings.txt" --llm                           # Claude for odd entries

What it does
  * splits the file on the ========== separators and parses each entry
    (title, author, highlight/note/bookmark, page, location, date, text)
  * drops stale highlights: editing a highlight on the Kindle appends a NEW
    entry and keeps the old one; the latest overlapping highlight wins
  * attaches each note to the highlight it was typed on
  * gives every clipping a stable 12-char id (same input -> same id), so
    re-runs can be diffed / deduped downstream
  * flags highlights with no text (scanned PDFs)

The parser is plain Python (stdlib only) and handles the English Kindle
format. Entries it can't read (other UI languages, damaged entries) are
listed under "unparsed" with their raw text. With --llm and
ANTHROPIC_API_KEY set, those entries alone are sent to Claude (Haiku by
default) to be structured; nothing else leaves your machine.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

__version__ = "1.0.0"

SEP = "=========="
META = re.compile(r"^-\s*Your (Highlight|Note|Bookmark|Clip(?:ping)?)\b(.*)$", re.I)
PAGE = re.compile(r"\bpage\s+([\dIVXLCDMivxlcdm]+)(?:\s*-\s*([\dIVXLCDMivxlcdm]+))?", re.I)
LOC = re.compile(r"\bLocation\s+(\d+)(?:\s*-\s*(\d+))?", re.I)
ADDED = re.compile(r"Added on\s+(.+)$", re.I)
TITLE = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")
DATE_FORMATS = ("%A, %B %d, %Y %I:%M:%S %p", "%A, %d %B %Y %H:%M:%S",
                "%A, %B %d, %Y %H:%M:%S", "%A, %d %B %Y %I:%M:%S %p")


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def parse_date(s):
    s = s.strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).isoformat()
        except ValueError:
            pass
    return None


def split_title(head):
    """'Title (Author)' -> (title, author). Kindle uses the LAST parenthesised group."""
    m = TITLE.match(head)
    return (m.group(1).strip(), m.group(2).strip()) if m else (head.strip(), "")


def parse_entry(chunk):
    """Return a clipping dict, or None if this entry isn't in the known format."""
    lines = [l.rstrip() for l in chunk.strip("\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    if len(lines) < 2:
        return None
    head, meta = lines[0].strip(), lines[1].strip()
    m = META.match(meta)
    if not m:
        return None
    kind = m.group(1).lower()
    kind = "clip" if kind.startswith("clip") else kind
    title, author = split_title(head)
    pg, lc, ad = PAGE.search(meta), LOC.search(meta), ADDED.search(meta)
    return {
        "type": kind,  # highlight | note | bookmark | clip
        "title": title,
        "author": author,
        "page": pg.group(1) if pg else None,
        "page_end": (pg.group(2) or pg.group(1)) if pg else None,
        "location": _int(lc.group(1)) if lc else None,
        "location_end": _int(lc.group(2) or lc.group(1)) if lc else None,
        "added": parse_date(ad.group(1)) if ad else None,
        "added_raw": ad.group(1).strip() if ad else None,
        "text": "\n".join(lines[2:]).strip(),
    }


def split_entries(text):
    text = text.replace("\ufeff", "").replace("\r\n", "\n")
    return [c for c in text.split(SEP) if c.strip()]


# ---------------------------------------------------------------- LLM fallback

LLM_PROMPT = """Each item below is one entry from a Kindle "My Clippings.txt" file that a
regex parser could not read (often a non-English Kindle UI). Return ONLY a JSON
array with one object per item, in the same order, with these keys:
type ("highlight"|"note"|"bookmark"|"clip"), title, author (""), page (string or null),
page_end, location (int or null), location_end, added (ISO 8601 "YYYY-MM-DDTHH:MM:SS"
or null), added_raw (the date text as written), text (the clipping body, verbatim).
If an item is not a clipping at all, return {"type": "invalid"} for it.

"""


def llm_parse(chunks, model, batch=40):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("--llm needs ANTHROPIC_API_KEY in the environment")
    out = []
    for i in range(0, len(chunks), batch):
        part = chunks[i:i + batch]
        items = "\n\n".join("### item %d\n%s" % (n + 1, c.strip()) for n, c in enumerate(part))
        body = json.dumps({
            "model": model,
            "max_tokens": 16000,
            "messages": [{"role": "user", "content": LLM_PROMPT + items}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body, method="POST",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = json.load(r)
        txt = "".join(b.get("text", "") for b in resp.get("content", []))
        m = re.search(r"\[.*\]", txt, re.S)
        rows = json.loads(m.group(0)) if m else []
        if len(rows) != len(part):
            raise SystemExit("LLM returned %d rows for %d entries; rerun or drop --llm" % (len(rows), len(part)))
        out.extend(rows)
    return out


# ---------------------------------------------------------------- grouping

def _pos(c):
    """(start, end) on the best axis available: location, else numeric page."""
    if c.get("location") is not None:
        return c["location"], c.get("location_end") or c["location"]
    p, e = _int(c.get("page")), _int(c.get("page_end"))
    return (p, e if e is not None else p) if p is not None else (None, None)


def _id(c):
    s, _ = _pos(c)
    raw = "%s|%s|%s|%s|%s" % (c["title"], c["author"], c["type"], s, c["text"])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def build(clips, keep_bookmarks=False):
    books = {}
    for c in clips:
        if c["type"] == "bookmark" and not keep_bookmarks:
            continue
        b = books.setdefault((c["title"], c["author"]), {
            "title": c["title"], "author": c["author"],
            "highlights": [], "notes": [], "bookmarks": [], "_raw_notes": []})
        c["id"] = _id(c)
        if c["type"] in ("highlight", "clip"):
            b["highlights"].append(c)
        elif c["type"] == "note":
            b["_raw_notes"].append(c)
        else:
            b["bookmarks"].append(c)

    for b in books.values():
        kept, superseded = [], 0
        for h in b["highlights"]:
            s, e = _pos(h)
            for i, o in enumerate(kept):
                os_, oe = _pos(o)
                same = (s is not None and os_ is not None and s <= oe and os_ <= e) or \
                       (s is None and h["text"] == o["text"])
                if same:
                    superseded += 1
                    if (h["added"] or "") >= (o["added"] or ""):
                        kept[i] = h
                    break
            else:
                kept.append(h)
        kept.sort(key=lambda h: (_pos(h)[0] is None, _pos(h)[0] or 0))
        for h in kept:
            h["notes"] = []
        loose = []
        for n in b.pop("_raw_notes"):
            ns, _ = _pos(n)
            # a note is anchored at the END of the highlight it was typed on
            tgt = [h for h in kept if ns is not None and _pos(h)[0] is not None
                   and _pos(h)[0] <= ns <= _pos(h)[1]]
            (tgt[-1]["notes"] if tgt else loose).append(n)
        b["highlights"], b["notes"] = kept, loose
        b["stats"] = {
            "highlights": len(kept),
            "notes": len(loose) + sum(len(h["notes"]) for h in kept),
            "superseded_highlights": superseded,
            "highlights_without_text": sum(1 for h in kept if not h["text"]),
        }
        if not keep_bookmarks:
            b.pop("bookmarks")
    return list(books.values())


def flatten(books):
    rows = []
    for b in books:
        for h in b["highlights"]:
            notes = h.pop("notes", [])
            rows.append(dict(h, note_ids=[n["id"] for n in notes]))
            for n in notes:
                rows.append(dict(n, highlight_id=h["id"]))
        for n in b["notes"]:
            rows.append(dict(n, highlight_id=None))
        rows.extend(b.get("bookmarks", []))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(prog="clippings2json",
                                 description="Kindle My Clippings.txt -> JSON")
    ap.add_argument("file", help='path to "My Clippings.txt" (use - for stdin)')
    ap.add_argument("-o", "--output", help="write JSON here instead of stdout")
    ap.add_argument("--flat", action="store_true", help="one record per clipping instead of grouped by book")
    ap.add_argument("--book", help="only books whose title/author contains this text (case-insensitive)")
    ap.add_argument("--bookmarks", action="store_true", help="keep bookmarks (dropped by default)")
    ap.add_argument("--llm", action="store_true", help="send entries the parser can't read to Claude (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--model", default="claude-haiku-4-5", help="model for --llm (default: %(default)s)")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    a = ap.parse_args(argv)

    src = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8", errors="replace").read()
    clips, unparsed = [], []
    for chunk in split_entries(src):
        c = parse_entry(chunk)
        (clips.append(c) if c else unparsed.append(chunk.strip()))

    via_llm = 0
    if unparsed and a.llm:
        rows = llm_parse(unparsed, a.model)
        still = []
        for raw, r in zip(unparsed, rows):
            if r.get("type") in ("highlight", "note", "bookmark", "clip"):
                r.setdefault("author", "")
                r["parsed_by"] = a.model
                clips.append(r)
                via_llm += 1
            else:
                still.append(raw)
        unparsed = still

    books = build(clips, keep_bookmarks=a.bookmarks)
    if a.book:
        q = a.book.lower()
        books = [b for b in books if q in (b["title"] + " " + b["author"]).lower()]

    result = {
        "source": os.path.basename(a.file) if a.file != "-" else "stdin",
        "generator": "clippings2json " + __version__,
        "books": len(books),
        "clippings_read": len(clips),
        "parsed_by_llm": via_llm,
        "unparsed": unparsed,
    }
    result["clippings" if a.flat else "library"] = flatten(books) if a.flat else books

    out = json.dumps(result, indent=2, ensure_ascii=False)
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(out + "\n")
    else:
        sys.stdout.write(out + "\n")
    print("%d books, %d clippings, %d unparsed%s" % (
        len(books), len(clips), len(unparsed),
        "" if a.llm or not unparsed else " (rerun with --llm to have Claude read them)"), file=sys.stderr)


if __name__ == "__main__":
    main()
