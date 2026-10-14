"""Text layout and the text table (data format 6).

The shader does no horizontal layout: this module places every inked glyph
(advances, pair kerning, tracking, word wrapping to a width, alignment, font
fallback) and writes the result as rows of a float RGB table texture, read
by the shader with closest filtering. One row per line of text; the row is
cut into BINS bins of BIN em from XMIN, and each bin holds up to SLOTS glyphs
whose ink reaches into it (ink before margin, nearest centre first), in
TEXELS texels: texel TEXELS*b + s holds (dx, g, i) of the glyph in slot s,
then WORD_TEXELS texels hold the slots' words, three per texel. A glyph sits
in slot i % SLOTS in every bin it reaches (a better-ranked glyph keeps a
taken slot), so the atlas uv a slot reads does not jump at a bin boundary.

dx = the glyph's advance centre minus the bin's centre, in em; g = the
glyph's index in the atlas, i = its index in the text (reading order, inked
glyphs only); g = -1: no glyph. Every value stays exact in half float (small
dx, integers under 2048): Houdini's viewport loads float textures as half
floats by default. A primitive finds its text with two
primvars: text_uv (u, v of the row centre of its first line) and text_lines
(v step from one line to the next, line count).

After the bins, the first row of a text holds its style: the values of the
shader inputs listed in STYLE, three per texel in that order, which the
Layout LOP / SOP takes from primvars text_<input> or from the node. The
shader reads them in Primvars mode, nine texture lookups that Karma XPU
compiles in about a second, where one primvar per input cost two seconds
each (63 s against 13 s for the shader, measured 2026-10-11). Style values
are float32 (Karma reads them exactly; the half-float viewport rounds them).

So the shader reads SLOTS table texels and evaluates SLOTS glyphs per line, with
no search: Karma XPU's compile time grows with every texture lookup (eight
lookups 56 s, twenty-four more than two minutes, measured 2026-10-11), and a
binary search over the line cost sixteen. A table rather than primvars:
Cycles' SVM stack (255 floats) cannot hold a text in primvars.
"""
from . import atlas as atlasmod

# bins of 1/8 em and three glyphs each: no ink contact lost down to -0.25 em
# of tracking (Arial; two glyphs in 1/4 em bins lost 6 % at -0.175 em)
BIN = 0.125                 # em
XMIN = -32.0                # em, x of the first bin
BINS = 512                  # bins per line: x from -32 to +32 em
import os as _os
SLOTS = int(_os.environ.get("MTLX_TEXT_SLOTS", "4"))   # glyphs per bin
WORD_TEXELS = -(-SLOTS // 3)                          # their words, three per texel
TEXELS = SLOTS + WORD_TEXELS                          # texels per bin
BIN_TEXELS = TEXELS * BINS  # texels of the bins
# shader inputs a text may set for itself, see the docstring
STYLE = (("origin", (0.05, 0.9)), ("size", (0.1,)), ("aspect", (1.0,)), ("rotate", (0.0,)),
         ("slant", (0.0,)), ("leading", (1.2,)), ("weight", (0.0,)), ("softness", (0.0,)),
         ("auto_aa", (1.0,)), ("fg", (1.0, 1.0, 1.0)), ("bg", (0.0, 0.0, 0.0)), ("seed", (0.0,)),
         ("jitter_baseline", (0.0,)), ("jitter_rotate", (0.0,)), ("jitter_scale", (0.0,)), ("drop", (0.0,)),
         ("outline_width", (0.03,)), ("outline_offset", (0.0,)), ("shadow_offset", (0.04, 0.04)),
         ("shadow_softness", (0.03,)), ("bevel_width", (0.04,)))   # (input, default)
STYLE_COMPONENTS = sum(len(d) for _, d in STYLE)
STYLE_TEXELS = -(-STYLE_COMPONENTS // 3)
WIDTH = BIN_TEXELS + STYLE_TEXELS   # texels per line of text
# Houdini's VK viewport shrinks textures wider than 2048 (mixing the exact
# values) and reads a one-pixel-high texture as zeros (2026-10-14): a line of
# text spans SUB_ROWS texture rows of TEX_WIDTH, and a table is MIN_TEX_ROWS high at least
MAX_TEX_WIDTH = 2048
SUB_ROWS = -(-WIDTH // MAX_TEX_WIDTH)
TEX_WIDTH = 1 << (-(-WIDTH // SUB_ROWS) - 1).bit_length()   # a power of two: viewports resize the others
MIN_TEX_ROWS = 4


def tex_height(lines):
    """Texture rows of a table of `lines` lines of text: a power of two."""
    return 1 << (max(lines * SUB_ROWS, MIN_TEX_ROWS) - 1).bit_length()
INK_MARGIN = 0.25           # em around a glyph's ink that still counts as reaching a bin: the distance range, so outlines and glows are not cut
MAX_LINES = 64
ALIGNS = ("left", "center", "right", "justify")
SYLLABLE_ENDS = (0x0F0B, 0x0F0C, 0x0F0D, 0x0F0E)   # Tibetan tsheg, non-breaking tsheg, shad, double shad


class FontSet:
    """Glyph map and metrics of an atlas, read from its JSON."""

    def __init__(self, metrics):
        self.m = metrics
        self.fonts = []
        self.ink = {}               # global glyph -> (left, right) of its ink, em from the advance centre
        for f in metrics["fonts"]:
            glyphs = {int(cp): (v[0], v[1]) for cp, v in f["glyphs"].items()}
            for v in f["glyphs"].values():
                self.ink[v[0]] = (v[2], v[3]) if len(v) > 3 else (-v[1] / 2, v[1] / 2)
            kern = {(a, b): v for a, b, v in f["kern"]}
            self.fonts.append({"glyphs": glyphs, "kern": kern, "space": f.get("space", 0.25)})

    @classmethod
    def from_atlas(cls, exr):
        return cls(atlasmod.load_metrics(exr))

    def glyph(self, cp, font):
        """(font index, global glyph, advance) for a code point, with fallback
        to the other fonts of the set, then '?' of the asked font."""
        order = [font] + [i for i in range(len(self.fonts)) if i != font]
        for fi in order:
            hit = self.fonts[fi]["glyphs"].get(cp)
            if hit is not None:
                return fi, hit[0], hit[1]
        q = self.fonts[font]["glyphs"].get(ord("?"))
        return (font, q[0], q[1]) if q else (font, None, 0.5)


def layout(text, fs, font=0, width=0.0, align="left", tracking=0.0, wrap=True):
    """Place the glyphs of `text`. Returns a list of lines, each a list of
    (global glyph, x centre em, code point, word index); x = 0 is the block's
    left edge when width > 0, else the anchor point of the alignment."""
    font = max(0, min(int(font), len(fs.fonts) - 1))
    if align not in ALIGNS:
        align = "left"
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    lines = []
    word = 0
    for para in text.split("\n"):
        # one pass of pen positions over the whole paragraph, then wrap
        items = []                  # (cp, glyph, x_left, adv, is_space, word)
        x, prev = 0.0, None
        in_word = False
        for ch in para:
            cp = ord(ch)
            if cp == 0x200B:        # zero width space: no ink, no advance, only a word break
                if in_word:
                    word += 1
                in_word = False
                continue
            if cp in (0x20, 0xA0):
                fi, g, adv = font, None, fs.fonts[font]["space"]
                if in_word:
                    word += 1
                in_word = False
            else:
                fi, g, adv = fs.glyph(cp, font)
                in_word = True
            if prev is not None and prev[0] == fi:
                x += fs.fonts[fi]["kern"].get((prev[1], cp), 0.0)
            items.append([cp, g, x, adv, cp in (0x20, 0xA0), word])
            x += adv + tracking
            prev = (fi, cp)
            if cp in SYLLABLE_ENDS:     # Tibetan: a tsheg or shad closes the syllable, its own word
                word += 1
                in_word = False
        if in_word:
            word += 1
        for ln, last in _wrap(items, width if wrap else 0.0, tracking):
            lines.append(_align(ln, width, align, last, tracking))
    return lines


def _line_width(items, tracking):
    if not items:
        return 0.0
    return items[-1][2] + items[-1][3] - items[0][2]


def _wrap(items, width, tracking):
    """Split a paragraph's items into lines no wider than width (0 = no wrap),
    breaking at spaces, or inside a word longer than the width."""
    if width <= 0 or not items:
        yield items, True
        return
    lines, cur, start = [], [], 0
    i = 0
    while i < len(items):
        # next word: [i, j) non-spaces, then the spaces after it [j, k)
        j = i
        while j < len(items) and not items[j][4]:
            j += 1
        k = j
        while k < len(items) and items[k][4]:
            k += 1
        word = items[i:j]
        trial = [it for it in cur] + word
        if cur and word and _line_width(_rebase(trial), tracking) > width:
            lines.append(_strip(cur))
            cur = []
        if word and _line_width(_rebase(word), tracking) > width:
            # a word wider than the line: break it by characters
            piece = []
            for it in word:
                if piece and _line_width(_rebase(piece + [it]), tracking) > width:
                    lines.append(piece)
                    piece = []
                piece.append(it)
            cur = piece
        else:
            cur += word
        cur += items[j:k]
        i = k
    lines.append(_strip(cur))
    for n, ln in enumerate(lines):
        yield _rebase(ln), n == len(lines) - 1


def _strip(items):
    while items and items[-1][4]:
        items = items[:-1]
    while items and items[0][4]:
        items = items[1:]
    return items


def _rebase(items):
    if not items:
        return []
    x0 = items[0][2]
    return [[it[0], it[1], it[2] - x0, it[3], it[4], it[5]] for it in items]


def _align(items, width, align, last, tracking):
    lw = _line_width(items, tracking)
    if align == "justify" and width > 0 and not last:
        spaces = [n for n, it in enumerate(items) if it[4]]
        extra = (width - lw) / len(spaces) if spaces else 0.0
        out, shift = [], 0.0
        for it in items:
            if it[4]:
                shift += extra
            elif it[1] is not None:
                out.append((it[1], it[2] + shift + it[3] / 2, it[0], it[5]))
        return out
    if align == "center":
        off = (width - lw) / 2 if width > 0 else -lw / 2
    elif align == "right":
        off = width - lw if width > 0 else -lw
    else:
        off = 0.0
    return [(it[1], it[2] + off + it[3] / 2, it[0], it[5]) for it in items if not it[4] and it[1] is not None]


# ---------------------------------------------------------------- the text table

def _new_row():
    import numpy as np
    row = np.zeros((WIDTH, 3), dtype=np.float32)
    for slot in range(SLOTS):              # no glyph in any slot
        row[slot:BIN_TEXELS:TEXELS, 1] = -1.0
    return row


def encode(lines, fs, warn=None):
    """Lines from layout() -> the table rows of one text, (n_lines, WIDTH, 3)
    float32. Each bin of BIN em along the line holds the SLOTS glyphs whose ink
    (plus INK_MARGIN) reaches into it, the nearest centres first."""
    import numpy as np
    if len(lines) > MAX_LINES:
        if warn:
            warn("%d lines, %d kept" % (len(lines), MAX_LINES))
        lines = lines[:MAX_LINES]
    rows = []
    i = 0
    clipped = False
    for ln in lines:
        row = _new_row()
        bins = [[] for _ in range(BINS)]
        for g, x, cp, w in ln:
            xl, xr = fs.ink.get(g, (-0.3, 0.3))
            b0 = int(np.floor((x + xl - INK_MARGIN - XMIN) / BIN))
            b1 = int(np.floor((x + xr + INK_MARGIN - XMIN) / BIN))
            if b0 < 0 or b1 >= BINS:
                clipped = True
            for b in range(max(b0, 0), min(b1, BINS - 1) + 1):
                lo = XMIN + b * BIN
                # rank: how far the ink is from the bin (0 when it reaches in),
                # then how far the centre is: ink before margins, near before far
                gap = max(0.0, x + xl - (lo + BIN), lo - (x + xr))
                bins[b].append(((gap, abs(x - (lo + 0.5 * BIN))), x - (lo + 0.5 * BIN), g, i, w))
            i += 1
        for b, items in enumerate(bins):
            items.sort(key=lambda t: t[0])
            # a glyph sits in slot i % SLOTS in every bin it reaches, so its atlas
            # uv does not jump at a bin boundary: Karma XPU widens its texture
            # filter where the uv jumps, and the seam shows (2026-10-11)
            words = [0.0] * (3 * WORD_TEXELS)
            for it in items:
                slot = it[3] % SLOTS
                if row[TEXELS * b + slot, 1] < 0:          # free; else a better-ranked glyph holds it
                    row[TEXELS * b + slot] = (it[1], it[2], it[3])
                    words[slot] = it[4]
            if items:
                row[TEXELS * b + SLOTS:TEXELS * (b + 1)] = np.reshape(words, (WORD_TEXELS, 3))
        rows.append(row)
    if clipped and warn:
        warn("a line reaches past %.0f em from the anchor and is clipped" % -XMIN)
    if not rows:
        rows.append(_new_row())
    return np.stack(rows, 0)


def style_offset(name):
    """First component of input `name` in the style (texel = offset // 3)."""
    k = 0
    for n, d in STYLE:
        if n == name:
            return k
        k += len(d)
    raise KeyError(name)


def style_vector(values):
    """{input: value or tuple} -> the style, a tuple of STYLE_COMPONENTS
    floats; an input missing or None takes its default."""
    out = []
    for name, d in STYLE:
        v = values.get(name)
        if v is None:
            v = d
        v = tuple(float(x) for x in v) if hasattr(v, "__len__") else (float(v),)
        if len(v) != len(d):
            raise ValueError("%s: %d values, %d expected" % (name, len(v), len(d)))
        out += v
    return tuple(out)


def decode(rows):
    """Inverse of encode() for tests: per line, the sorted set of
    (glyph, character index, x, word) found in its bins."""
    out = []
    for row in rows:
        seen = {}
        for b in range(BINS):
            centre = XMIN + (b + 0.5) * BIN
            words = row[TEXELS * b + SLOTS:TEXELS * (b + 1)].reshape(-1)
            for slot in range(SLOTS):
                dx, g, i = row[TEXELS * b + slot]
                if g >= 0:   # a glyph spans several bins: keep its first position
                    seen.setdefault((int(g), int(i)), (int(g), int(i), float(dx) + centre, int(words[slot])))
        out.append(sorted(seen.values(), key=lambda t: t[1]))
    return out


class Table:
    """The text table being written: the rows of several texts, stacked."""

    def __init__(self):
        self.rows = []
        self.entries = []        # (first row, line count) per text

    def add(self, rows, style=None):
        """Add one text's rows; style: its style_vector() (read in Primvars
        mode only; zeros when None)."""
        rows = [r.copy() for r in rows]
        if style is not None:
            flat = rows[0][BIN_TEXELS:].reshape(-1)
            flat[:len(style)] = style
        self.entries.append((len(self.rows), len(rows)))
        self.rows += rows
        return len(self.entries) - 1

    def primvars(self, k, tile=1001):
        """(text_uv, text_lines) of text k: uv of its first line's row centre
        (with the UDIM tile's offset), and (v step per line, line count)."""
        first, n = self.entries[k]
        h = tex_height(len(self.rows))
        tu, tv = udim_offset(tile)
        return (tu, tv + (first * SUB_ROWS + 0.5) / h), (SUB_ROWS / h, float(n))

    def write(self, path):
        return write_table(self.rows or [_new_row()], path)


def write_table(rows, path):
    """Rows (each (WIDTH, 3), one per line of text) -> float32 RGB EXR of
    TEX_WIDTH x tex_height: line r is texture rows r * SUB_ROWS + s from the
    bottom (v = (row + 0.5) / height), texel k of it in row s = k // TEX_WIDTH.
    Scanline, no mip levels: the shader reads exact values with closest filtering."""
    import os
    import numpy as np
    import OpenImageIO as oiio
    oiio.attribute("threads", 1)            # OIIO's EXR writer crashes in hython otherwise
    oiio.attribute("exr_threads", 1)
    rows = list(rows)
    tex = np.zeros((tex_height(len(rows)), TEX_WIDTH, 3), np.float32)
    for r, row in enumerate(rows):
        padded = np.pad(row, ((0, SUB_ROWS * TEX_WIDTH - WIDTH), (0, 0)))
        tex[r * SUB_ROWS:(r + 1) * SUB_ROWS] = padded.reshape(SUB_ROWS, TEX_WIDTH, 3)
    img = np.ascontiguousarray(tex[::-1])                # image row 0 = top
    spec = oiio.ImageSpec(TEX_WIDTH, img.shape[0], 3, "float")
    spec.attribute("compression", "zip")
    tmp = path + ".tmp.exr"
    out = oiio.ImageOutput.create(tmp)
    if out is None or not out.open(tmp, spec):
        raise RuntimeError("cannot write %s: %s" % (path, oiio.geterror()))
    out.write_image(img)
    out.close()
    os.replace(tmp, path)
    return path


def udim_offset(tile):
    """(u, v) offset of a UDIM tile number (1001 = (0, 0))."""
    t = int(tile) - 1001
    return float(t % 10), float(t // 10)


def text_rows(text, fs, font=0, width=0.0, align="left", tracking=0.0, wrap=True, warn=None):
    return encode(layout(text, fs, font, width, align, tracking, wrap), fs, warn)
