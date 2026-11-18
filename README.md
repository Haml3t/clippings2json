# clippings2json

Turns a Kindle `My Clippings.txt` into clean JSON, so you never have to look at that format again.

```
clippings2json "My Clippings.txt" -o clippings.json
```

You need Python 3.8 or newer. There are no dependencies.

## Install

```
pip install clippings2json-1.0.0-py3-none-any.whl
# or, without installing anything:
python3 clippings2json.py "My Clippings.txt" -o clippings.json
```

To get the file, plug the Kindle in by USB and copy `documents/My Clippings.txt` off it.

## What it fixes

- **Edited highlights.** When you extend or trim a highlight, the Kindle keeps the old entry and adds a new one. Only the latest version is kept, and `stats.superseded_highlights` counts the ones dropped.
- **Notes.** Each note is attached to the highlight it was typed on. Notes with no matching highlight are listed under the book's `notes`.
- **Title and author.** `Title (Author)` is split into separate fields.
- **Dates.** `Added on Tuesday, November 10, 2026 3:04:05 PM` becomes ISO `2026-11-10T15:04:05`. The original text is kept in `added_raw`.
- **Page and location.** Both are extracted. PDFs usually have only a page.
- **Scanned PDFs.** Highlights that have no text are counted in `stats.highlights_without_text`.
- **Bookmarks.** Dropped by default. `--bookmarks` keeps them.
- **Stable IDs.** Every clipping gets a 12-character `id` that stays the same on re-runs, so you can dedupe or diff exports later.
- **Encoding.** The byte-order mark and Windows line endings are handled.

## Options

| flag | |
|---|---|
| `-o FILE` | write to FILE instead of stdout |
| `--flat` | one record per clipping (notes carry `highlight_id`) instead of grouped by book |
| `--book TEXT` | only books whose title or author contains TEXT |
| `--bookmarks` | keep bookmarks |
| `--llm` | send entries the parser can't read to Claude (see below) |
| `--model M` | model for `--llm`, default `claude-haiku-4-5` |

## Output (default, grouped by book)

```json
{
  "books": 85, "clippings_read": 1802, "parsed_by_llm": 0, "unparsed": [],
  "library": [
    {
      "title": "The Left Hand of Darkness", "author": "Le Guin, Ursula K.",
      "highlights": [
        {"type": "highlight", "page": "12", "location": 180, "location_end": 184,
         "added": "2026-11-10T15:05:00", "text": "Light is the left hand of darkness…",
         "id": "3f1c…", "notes": [{"type": "note", "text": "epigraph for the zine", "id": "…"}]}
      ],
      "notes": [],
      "stats": {"highlights": 1, "notes": 1, "superseded_highlights": 1, "highlights_without_text": 0}
    }
  ]
}
```

## Claude mode (`--llm`)

The regex parser handles the English Kindle format: it reads all 1,802 entries in a real 9,000-line file. It can't read entries from a Kindle set to another language, or damaged entries. Those are listed in `unparsed` with their raw text.

```
export ANTHROPIC_API_KEY=sk-ant-...
clippings2json "My Clippings.txt" --llm -o clippings.json
```

Only the unparsed entries are sent to Claude, 40 at a time. Everything else stays on your machine. Claude Haiku is the default. Pass `--model claude-sonnet-4-5` if you want a bigger model.

## Test

```
python3 tests/test_basic.py
```

MIT license.
