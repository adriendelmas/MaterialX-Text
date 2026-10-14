"""Layout and packing checks: hython tests/test_layout.py <scratch_dir>

Bakes a small atlas of the two fonts of MTLX_TEXT_TEST_FONTS (custom
characters only, fast) and checks the layout rules and the data format round
trip.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "python3.13libs"))
sys.path.insert(0, HERE)
from mtlx_text import atlas, layout  # noqa: E402
from fonts import test_fonts  # noqa: E402

out = os.path.abspath(sys.argv[1])
EXTENDED = "ƀƁƆƉƎƒƠƵȘȷ"   # for the fallback check
chars = "AVTaeoWhy?.,lixHgnd" + "é" + EXTENDED
exr = atlas.bake(test_fonts(), charsets=(), custom=chars,
                 out=os.path.join(out, "layout_test.exr"))
fs = layout.FontSet.from_atlas(exr)


def xs(line):
    return [round(x, 4) for _, x, _, _ in line]


# kerning: the pair A-V (most text fonts kern it) moves by its kern value
k = fs.fonts[0]["kern"].get((ord("A"), ord("V")), 0.0)
if not k:
    print("note: the first font has no A-V kern pair; only the advances are checked")
ln = layout.layout("AV", fs)[0]
adv_a = fs.fonts[0]["glyphs"][ord("A")][1]
adv_v = fs.fonts[0]["glyphs"][ord("V")][1]
assert abs((ln[1][1] - ln[0][1]) - (adv_a / 2 + k + adv_v / 2)) < 1e-6, xs(ln)

# tracking adds to every advance
t = layout.layout("AV", fs, tracking=0.1)[0]
assert abs((t[1][1] - t[0][1]) - (ln[1][1] - ln[0][1]) - 0.1) < 1e-6

# spaces carry no glyph; words are numbered
w = layout.layout("Hey you  there", fs)[0]
assert [cp for _, _, cp, _ in w] == [ord(c) for c in "Heyyouthere"]
assert [wd for _, _, _, wd in w] == [0, 0, 0, 1, 1, 1, 2, 2, 2, 2, 2]

# wrapping: no line wider than the width, words kept whole
lines = layout.layout("Hello Wally, the hand gave a hey", fs, width=4.0, wrap=True)
assert len(lines) > 1
for line in lines:
    g, x, cp, _ = line[-1]
    adv = fs.glyph(cp, 0)[2]
    assert line[0][1] - fs.glyph(line[0][2], 0)[2] / 2 >= -1e-6
    assert x + adv / 2 <= 4.0 + 1e-6, (xs(line), x + adv / 2)

# centre: symmetric around width / 2
c = layout.layout("hand", fs, width=6.0, align="center")[0]
left = c[0][1] - fs.glyph(c[0][2], 0)[2] / 2
right = c[-1][1] + fs.glyph(c[-1][2], 0)[2] / 2
assert abs((left + right) / 2 - 3.0) < 1e-6

# justify: every line but the last ends at the width
j = layout.layout("the hand gave a hey the hand gave a hey", fs, width=6.0, align="justify")
for line in j[:-1]:
    assert abs(line[-1][1] + fs.glyph(line[-1][2], 0)[2] / 2 - 6.0) < 1e-6
assert abs(j[-1][0][1] - fs.glyph(j[-1][0][2], 0)[2] / 2) < 1e-6      # last line left

# fallback: a character missing from the asked font comes from another one
only_first = [c for c in EXTENDED if ord(c) in fs.fonts[0]["glyphs"] and ord(c) not in fs.fonts[1]["glyphs"]]
if only_first:
    c = only_first[0]
    fell_back = layout.layout(c, fs, font=1)[0][0][0]
    assert fell_back == fs.fonts[0]["glyphs"][ord(c)][0]
else:
    print("note: the second font has every extended test character; the fallback is not checked")
missing = layout.layout("\u4e2d", fs)[0][0][0]
assert missing == fs.fonts[0]["glyphs"][ord("?")][0]

# table rows: every glyph sits in the bins its ink reaches, with its exact
# x centre, atlas glyph, index in the text and word
import numpy as np
import OpenImageIO as oiio
lines = layout.layout("Hello Wally\nthe hand\n\ngave a hey", fs, align="center")
rows = layout.encode(lines, fs)
assert rows.shape == (4, layout.WIDTH, 3) and rows.dtype == np.float32
back = layout.decode(rows)
assert back[2] == []
i = 0
for ln, got in zip(lines, back):
    want = []
    for g, x, cp, w in ln:
        want.append((g, i, x, w))
        i += 1
    assert [(g, i, w) for g, i, _, w in got] == [(g, i, w) for g, i, _, w in want], (got, want)
    assert all(abs(a[2] - b[2]) < 1e-5 for a, b in zip(got, want))
# the bins of one glyph: those its ink (plus the margin) reaches, no other
g, x, cp, w = lines[0][0]
xl, xr = fs.ink[g]
for b in range(layout.BINS):
    lo = layout.XMIN + b * layout.BIN
    hit = x + xl - layout.INK_MARGIN < lo + layout.BIN and x + xr + layout.INK_MARGIN >= lo
    slots = rows[0][layout.TEXELS * b:layout.TEXELS * b + layout.SLOTS]
    present = any(int(s[1]) == g for s in slots)
    assert present == hit or (hit and slots[0 % layout.SLOTS][1] >= 0), b
    if present:     # a glyph keeps one slot in every bin: i % SLOTS (no uv jump at bin edges)
        assert int(slots[0 % layout.SLOTS][1]) == g
# every value of a row is exact in half float (Houdini's viewport loads half)
assert np.array_equal(rows.astype(np.float16).astype(np.float32)[..., 1:], rows[..., 1:])
assert np.abs(rows.astype(np.float16).astype(np.float32)[..., 0] - rows[..., 0]).max() < 2e-3

# the table: texts stacked, primvars point at each text's first row
t = layout.Table()
k0 = t.add(rows)
k1 = t.add(layout.text_rows("x", fs))
path = t.write(os.path.join(out, "table.exr"))
inp = oiio.ImageInput.open(path)
img = np.asarray(inp.read_image("float"))[::-1]
inp.close()
# each line spans SUB_ROWS texture rows of TEX_WIDTH (<= 2048: Houdini VK shrinks wider textures)
sub = layout.SUB_ROWS
h = layout.tex_height(5)        # a power of two, like TEX_WIDTH
assert layout.TEX_WIDTH <= 2048 and img.shape == (h, layout.TEX_WIDTH, 3)
back = img[:5 * sub].reshape(5, sub * layout.TEX_WIDTH, 3)[:, :layout.WIDTH]
assert np.array_equal(back[:4], rows)
assert t.primvars(k1) == ((0.0, (4 * sub + 0.5) / h), (sub / h, 1.0))
assert t.primvars(k0, tile=1012) == ((1.0, 1.0 + 0.5 / h), (sub / h, 4.0))
# a one-line table is still MIN_TEX_ROWS high (Houdini VK reads a 1-pixel-high texture as zeros)
t1 = layout.Table()
t1.add(layout.text_rows("x", fs))
i1 = oiio.ImageInput.open(t1.write(os.path.join(out, "table1.exr")))
assert i1.spec().height >= layout.MIN_TEX_ROWS
i1.close()

# limits
msgs = []
layout.encode(layout.layout("\n".join("a" * 80), fs), fs, warn=msgs.append)
assert msgs and "lines" in msgs[0]
msgs = []
layout.encode(layout.layout("W" * 120, fs), fs, warn=msgs.append)
assert msgs and "clipped" in msgs[0]

print("layout OK")
