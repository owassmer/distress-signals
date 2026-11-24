"""A PDF page read by its layout, so that each line of the text is one block of the page: a paragraph, a
heading, or a row of a table. A PDF holds glyphs at places and no structure, so the structure is read from
the places.

- Glyphs on one baseline are a line; a gap wider than a letter space starts a new word.
- A line with a gap much wider than a word space is a row of a table, and stands alone.
- A line of prose continues onto the next where the next line's first word would not have fit after it: that
  is what a wrapped line is. The lines of a paragraph are joined into one.
- A page set in columns of prose is read a column at a time.
- A page where prose runs beside something else on the same lines (a figure's labels, a second column the
  strip between could not be found for) is not laid out: it comes in the file's own order, and no line of it
  is taken for a block.

blocks() works on glyphs alone ({text, x0, x1, top, bottom, size}), so it is tested without a PDF."""

import io
import statistics

MARKS = "•●○▪■◦‣–—*·"       # a line that begins with one of these begins a list item


def _lines(chars):
    """The glyphs' lines, top to bottom: each its extent, its words [(text, x0, x1)] and the gaps between them."""
    found = []
    for c in sorted(chars, key=lambda c: (c["top"], c["x0"])):
        mid = (c["top"] + c["bottom"]) / 2
        line = next((ln for ln in reversed(found[-4:]) if ln["top"] - 1 <= mid <= ln["bottom"] + 1), None)
        if line is None:
            line = {"top": c["top"], "bottom": c["bottom"], "chars": []}
            found.append(line)
        line["chars"].append(c)
        line["top"], line["bottom"] = min(line["top"], c["top"]), max(line["bottom"], c["bottom"])
    out = []
    for ln in sorted(found, key=lambda ln: ln["top"]):
        cs = sorted(ln["chars"], key=lambda c: c["x0"])
        size = statistics.median(c["size"] for c in cs)
        words, gaps, word = [], [], [cs[0]]
        for a, b in zip(cs, cs[1:]):
            gap = b["x0"] - a["x1"]
            if gap > max(1.0, 0.17 * size):
                words.append(word)
                gaps.append(gap)
                word = []
            word.append(b)
        words.append(word)
        out.append({"top": ln["top"], "bottom": ln["bottom"], "size": size, "x0": cs[0]["x0"], "x1": cs[-1]["x1"],
                    "chars": cs, "gaps": gaps, "text": " ".join("".join(c["text"] for c in w) for w in words),
                    "words": [("".join(c["text"] for c in w), w[0]["x0"], w[-1]["x1"]) for w in words]})
    return out


def _flow(lines):
    """One column's lines as blocks: a row of a table alone, and a paragraph's lines joined."""
    if not lines:
        return []
    small = [g for ln in lines for g in ln["gaps"] if g < 1.2 * ln["size"]]
    space = statistics.median(small) if small else 3.0
    for ln in lines:
        ln["row"] = any(g > max(3 * space, 1.5 * ln["size"]) for g in ln["gaps"])
    pitches = [b["top"] - a["top"] for a, b in zip(lines, lines[1:])
               if not a["row"] and not b["row"] and 0 < b["top"] - a["top"] < 40]
    pitch = statistics.median(pitches) if pitches else 14.0
    out, i = [], 0
    while i < len(lines):
        text = lines[i]["text"]
        while not lines[i]["row"] and i + 1 < len(lines):
            nxt = lines[i + 1]
            word, w0, w1 = nxt["words"][0]
            near_by = [ln["x1"] for ln in lines[max(0, i - 3):i + 5] if not ln["row"] and abs(ln["top"] - lines[i]["top"]) <= 5 * pitch]
            right = max(near_by)                                           # the measure the lines around it are set to
            wrapped = (right - lines[i]["x1"]) < (w1 - w0) + space         # the next line's first word did not fit
            near = (nxt["top"] - lines[i]["top"]) <= 1.35 * pitch
            if nxt["row"] or not wrapped or not near or word[:1] in MARKS:
                break
            text = text + nxt["text"] if text.endswith("-") and text[-2:-1].isalpha() else text + " " + nxt["text"]
            i += 1
        out.append(text)
        i += 1
    return out


def _gutters(lines, width):
    """The x at which the page divides into columns of prose: a strip no glyph of most lines enters, with prose
    on both sides of it that runs to its own right edge."""
    if len(lines) < 12:
        return []
    bins = [0] * (int(width // 2) + 2)
    for ln in lines:
        covered = set()
        for c in ln["chars"]:
            covered.update(range(int(c["x0"] // 2), int(c["x1"] // 2) + 1))
        for b in covered:
            if 0 <= b < len(bins):
                bins[b] += 1
    left, right = min(ln["x0"] for ln in lines), max(ln["x1"] for ln in lines)
    few, out, start = max(1, len(lines) // 12), [], None
    for b in range(int(left // 2), int(right // 2) + 2):
        if bins[b] <= few and left + 0.2 * (right - left) < 2 * b < right - 0.2 * (right - left):
            start = b if start is None else start
        else:
            if start is not None and (b - start) * 2 >= 8:
                out.append((start + b) / 2 * 2)
            start = None
    kept = []
    for x in out:
        prose = []
        for side in (True, False):                    # what each line has to the left of the strip, then to its right
            pieces = [cs for cs in ([c for c in ln["chars"] if (c["x0"] < x) == side] for ln in lines) if cs]
            if len(pieces) < 8:
                prose.append(False)
                continue
            ends = sorted(cs[-1]["x1"] for cs in pieces)
            edge, first = ends[int(0.75 * (len(ends) - 1))], min(cs[0]["x0"] for cs in pieces)   # where most of its lines end
            wordy = sum(sum(c["text"].isalpha() for c in cs) >= 12 for cs in pieces)
            full = sum(cs[-1]["x1"] >= edge - 0.1 * (edge - first) for cs in pieces)
            prose.append(wordy >= 0.6 * len(pieces) and full >= 0.4 * len(pieces))
        if all(prose):
            kept.append(x)
    return kept


class Unlaid(ValueError):
    """The page's layout could not be read as blocks."""


def _beside(lines):
    """The share of lines on which a run of prose sits beside other words across a wide gap: prose next to a
    figure's labels, or two columns read as one. A table's row has words in its label and figures beside it."""
    mixed = 0
    for ln in lines:
        pieces, piece = [], [ln["words"][0][0]]
        for (word, _, _), gap in zip(ln["words"][1:], ln["gaps"]):
            if gap > 1.5 * ln["size"]:
                pieces.append(piece)
                piece = []
            piece.append(word)
        pieces.append(piece)
        letters = [sum(ch.isalpha() for w in piece for ch in w) for piece in pieces]
        mixed += sum(n >= 4 for n in letters) >= 2 and max(letters) >= 25
    return mixed / len(lines) if lines else 0


def blocks(chars, width):
    """A page's blocks, in reading order, from its glyphs: each a paragraph, a heading or a row. Unlaid where
    prose shares its lines with something else."""
    chars = [c for c in chars if c["text"].strip()]
    lines = _lines(chars)
    cuts = _gutters(lines, width)
    if not cuts:
        if _beside(lines) >= 0.25:
            raise Unlaid("prose shares its lines with other text")
        return _flow(lines)
    edges = [float("-inf"), *cuts, float("inf")]
    out, band = [], []

    def flush():
        for lo, hi in zip(edges, edges[1:]):
            out.extend(_flow(_lines([c for ln in band for c in ln["chars"] if lo <= (c["x0"] + c["x1"]) / 2 < hi])))
        band.clear()

    for ln in lines:
        if any(c["x0"] < x < c["x1"] for x in cuts for c in ln["chars"]) or \
                any(a[2] < x < b[1] and b[1] - a[2] < 8 for x in cuts for a, b in zip(ln["words"], ln["words"][1:])):
            flush()                                   # a line that runs across the columns stands between them
            out.extend(_flow([ln]))
        else:
            band.append(ln)
    flush()
    return out


def read(content, pages=400):
    """(text, title, block_lines) of a PDF. The text has a '[page N]' line before each page's blocks.
    block_lines gives the ranges of lines, counted from 0, in which a line is one block; a page whose layout
    could not be read comes as its text in the file's own order, outside those ranges."""
    import pdfplumber
    from pypdf import PdfReader
    plain = PdfReader(io.BytesIO(content))
    title = (plain.metadata or {}).get("/Title") or ""
    out, ranges = [], []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for n, page in enumerate(pdf.pages[:pages], 1):
            try:
                chars = [{"text": c["text"], "x0": c["x0"], "x1": c["x1"], "top": c["top"], "bottom": c["bottom"],
                          "size": c.get("size") or (c["bottom"] - c["top"])} for c in page.chars if c.get("upright", True)]
                found = [" ".join(b.split()) for b in blocks(chars, page.width)]
            except Exception:                         # a page pdfminer cannot lay out
                found = None
            if out:
                out.append("")
            out.append(f"[page {n}]")
            if found is None:
                out.extend(" ".join(line.split()) for line in (plain.pages[n - 1].extract_text() or "").splitlines())
            else:
                first = len(out) - 1
                out.extend(b for b in found if b)
                ranges.append([first, len(out) - 1])
    return "\n".join(out), title, ranges
