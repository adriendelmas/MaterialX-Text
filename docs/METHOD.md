# MtlX Text - method and data contract

A MaterialX node (nodedef `ND_textsdf_v2`, node `textsdf`, Houdini VOP
`mtlxtextsdf`, label *MtlX Text*) that draws text in the shader, for **Karma
CPU, Karma XPU and Cycles** (hdCycles). The text comes from the node's Text
parameter, or per primitive from primvars written by the *MtlX Text Layout*
LOP or SOP. Read this before driving the node from an HDA.

Verified 2026-10-11 on Houdini 22.0.368 (tests/): every output of the node
matches a numpy evaluation of the graph (tests/mtlx_eval.py) on Karma CPU,
XPU and Cycles; the text from the parameter and from primvars renders the
same.

## How it works

1. **Atlas** (once per font set, `mtlx_text.atlas`): every glyph of every
   font of the set as a *multi-channel signed distance field* (MSDF, sharp
   corners at any size) in `<name>.exr` (RGB, read as vector3), its true
   signed distance in `<name>_sdf.exr` (one channel), and the metrics in
   `<name>.json` (advances, ink bounds, kerning from GPOS or kern, vertical
   metrics). Distances are in em, positive inside, clamped to +-0.25 em.
   Two bakers write the same files: msdfgen (Viktor Chlumsky's native tool,
   `bin/msdfgen.exe` on Windows, `mtlx_text.msdfgen`), fed the glyph contours
   fontTools reads, about 25x faster and light on memory; and a numpy
   reimplementation (`mtlx_text.msdf`), used when msdfgen is not found.
   Measured on Microsoft Himalaya, 207 glyphs at 48 px/em: 26.5 s against
   1.0 s, medians 0.001 texel apart on average (2026-10-15).
2. **Layout** (on the CPU, `mtlx_text.layout`, one implementation for the
   node, the LOP and the SOP): advances, pair kerning, tracking, wrapping to
   a width at spaces, left / center / right / justify, font fallback (a
   character a font lacks comes from the next font of the set).
3. **Text table** (`mtlx_text.layout.Table`, format 6): a float RGB EXR, one
   line of text (512 bins of 1/8 em from -32 em, then the text's style
   texels), folded into texture rows of at most 2048 texels (a power of two)
   and at least 4 rows high: Houdini's VK viewport shrinks wider textures and
   reads a one-row texture as zeros (2026-10-14). Each bin holds up to four glyphs whose ink, plus 0.25 em,
   reaches into it (smallest ink gap first, then nearest centre), one texel
   (dx, g, i) per slot, then two texels of their words. A glyph sits in slot
   i % 4 in every bin it reaches, and the shader puts the even line of the
   two it reads first: a glyph's atlas uv never jumps at a bin or line
   boundary, where Karma XPU would widen its texture filter (a faint seam,
   2026-10-11). dx = glyph x
   centre minus bin centre (em), g = atlas glyph index, i = index of the glyph
   in the text (inked glyphs, reading order), -1 = none. Every value is exact
   in half float (the viewport loads textures as 16-bit). Read with closest
   filtering, no mip levels.
4. **Shader** (`build.py` writes the nodegraph, standard MaterialX nodes plus
   Karma's ST Derivatives): the two lines whose cells bracket the point, four
   candidate glyphs on each (one table texel per candidate), each candidate's
   MSDF read through its per-letter transform; the union keeps the largest
   distance (glyphs may overlap in both directions), and ids, uv and word come
   from the winner. Every lookup reads a candidate straight from the table:
   the true distance (sdf_round) is read per candidate too, and the shadow
   repeats the union at the shifted point.

## Driving it per primitive (the HDA contract)

Put a string primvar `text` on the prims (a primitive attribute `text` in
SOPs becomes one), then in Solaris, **after** the material assignment:

    MtlX Text Layout LOP:  Material = the material prim holding the MtlX Text
                           shader (its atlas must be baked: Bake Atlas on the
                           node), Primitives = where to look.

The LOP writes the table (`$HIP/tex/mtlx_text/$OS.<UDIM>.exr` by default),
gives each prim `text_uv` and `text_lines` (float2, the interpolation of
`text`: constant, or uniform for one text per face), and sets the shader to
read them (`inputs:textsource = 1`, `inputs:table`). Several layout nodes can
feed one material: give each its own UDIM tile.

The SOP version (MtlX Text Layout SOP) does the same in SOPs: set its
Material to the MtlX Text VOP, and on the material node *Text From: Layout
SOP* with *Layout SOP* pointing at it.

Layout per prim, when the prim has them: `text_font` (int, index in the
font list), `text_width` (float em, 0 = no wrapping), `text_align` (string
`left` / `center` / `right` / `justify`, or 0..3), `text_tracking` (float em).

Look and placement per prim: every input below can be overridden by a primvar
`text_<input>` (an attribute in SOPs) of the same type (float, float2 for origin and shadow_offset,
color3 for fg / bg) - `text_size`, `text_origin`, `text_rotate`, `text_slant`,
`text_leading`, `text_aspect`, `text_weight`, `text_softness`, `text_auto_aa`,
`text_fg`, `text_bg`, `text_seed`, `text_jitter_baseline`,
`text_jitter_rotate`, `text_jitter_scale`, `text_drop`, `text_outline_width`,
`text_outline_offset`, `text_shadow_offset`, `text_shadow_softness`,
`text_bevel_width`. For random placement on buildings,
write `text`, `text_origin`, `text_size`, `text_rotate`, `text_seed` per
primitive. The layout node reads these and writes them in the table, after
the bins of the text's first row (`layout.STYLE`, three per texel); the
shader reads them there in Primvars mode. They are not read by the shader as
primvars: one primvar read per input made Karma XPU compile the shader in
63 s instead of 13 s (2 s per primvar, measured 2026-10-11). So a primvar
`text_<input>` works only through a layout node, and the layout node must
re-cook when it changes; a prim without it gets the shader node's value.

Without a layout node, the material node's own Text parameter is used (the
node writes a one-text table in the atlas cache).

## Inputs

| Input | Meaning |
|---|---|
| origin (UV) | anchor of the text block: top-left when Vertical Anchor is Top and the text is left aligned without width |
| size | 1 em in UV units |
| aspect | em height / em width in UV (a 2:1 surface with a 0..1 UV wants 2) |
| rotate, slant | block rotation in degrees, italic shear (x per y) |
| leading | line pitch in em (1.2 by default; down to about 0.6, lines overlap) |
| valign | vertical anchor: top, middle, bottom of the block |
| weight | moves the edge, em (+ bolder) |
| softness, auto_aa | edge half width in em, plus auto_aa x half a pixel (Karma only: Cycles has no pixel footprint) |
| fg, bg | colours of the color output |
| seed, jitter_baseline (em), jitter_rotate (degrees), jitter_scale (fraction), drop (0..1) | per-letter random variation |
| corners | effects from the sharp (MSDF) or round (true) distance |
| outline_width, outline_offset | outline band, em (offset > 0 = outside) |
| shadow_offset (em, x right y down), shadow_softness | drop shadow |
| bevel_width | bevel profile from the edge inwards, em |

The atlas and table inputs (atlas, atlas_sdf, atlas_cols, atlas_rows, cell,
baseline, ascender, descender, range, table, uv, lines, textsource) are set by
the node's callbacks and the layout nodes; they are hidden on the node.

## Outputs

| Output | Type | Meaning |
|---|---|---|
| mask | float | the text, 0..1 |
| color | color3 | mix(bg, fg, mask) |
| sdf | float | signed distance to the text edge, em, + inside (sharp corners near the edge, true distance beyond 3 atlas texels) |
| sdf_round | float | true signed distance (round corners) |
| character_id | float | index of the glyph in its text, -1 off the text: one value per letter, for per-letter variation |
| glyph_index | float | the glyph in the atlas (identical letters share it) |
| line_id, word_id | float | line and word of the glyph, -1 off the text |
| glyph_uv | vector2 | 0..1 inside the glyph's cell, for per-letter textures |
| random | float | a random per letter, from character_id and the seed |
| outline | float | the outline band |
| shadow | float | the drop shadow (not masked by the text) |
| bevel_height | float | 0 at the edge to 1 at bevel_width inside |
| footprint | float | the pixel size in em (Karma) |

## Limits

* 64 lines per text, lines within -32..+32 em of the anchor (warned).
* Four glyphs per 1/8 em bin per line (`MTLX_TEXT_SLOTS` at build time):
  nothing is lost down to -0.25 em of tracking; at -0.35 em about 3 % of the
  glyph-bin contacts drop (Arial, 2026-10-11). The slot count costs no
  measurable compile or render time between 2 and 5, and none at cook time.
* Atlas resolution: one texel is 1/Quality em (32 to 192 px/em). A ramp on
  sdf narrower than about two texels each side shows the texel grid (facets,
  beads along strokes thinner than two texels): raise Quality for close-ups,
  and use sdf_round (true distance, no MSDF creases) for displacement. The
  numpy baker costs about 2.5 s per glyph at 192 px/em; msdfgen a few
  milliseconds.
* Atlases have no mip levels (format 6): the shader antialiases from its own
  pixel footprint, and mip levels drew seams where a candidate's glyph
  changes. A node with an older atlas bakes it again on its next text edit.
* Leading below about 0.6 em or tracking below about -0.5 em: a third line
  or glyph would be needed and is not drawn.
* Up to 2048 glyphs per atlas (all fonts of the set); character sets:
  Western, Latin Extended-A, Greek, Cyrillic, Tibetan, plus extra characters.
* No ligatures, no right-to-left or complex scripts. Tibetan (character set
  "Tibetan") depends on the font, as there is no shaping: a font that draws
  subjoined letters and vowel signs in place (Microsoft Himalaya) reads
  right, multiple stacks (ཧཱུྃ) approximate; a font that places them through
  OpenType positioning (Noto Serif Tibetan) does not. Words split at spaces, at the tsheg / shad (one word per
  syllable) and at U+200B (zero width space: no ink, no advance), which cuts
  a syllable in two for word_id without changing the drawing.
* The Houdini node is one shader of the nodedef (`mtlx2hda`): a value change
  re-cooks the material library in about 9 ms. Houdini's VK viewport does not
  draw it, and cannot: it ignores every custom MaterialX nodedef, whether its
  library is on HOUDINI_PATH or on PXR_MTLX_PLUGIN_SEARCH_PATHS. Use a Karma
  viewport.
* Cycles: [cycles-for-houdini](https://github.com/adriendelmas/cycles-for-houdini)
  with its patches 0137 (MaterialX libraries on HOUDINI_PATH) and 0139 (ST
  Derivatives).
* Karma XPU compiles each shader network once (about 13 s for this node,
  cold, 2026-10-11), then keeps it in its disk cache; changing a value never
  recompiles, wiring another output does (once). What makes the compile
  grow, measured: a primvar read (2 s each) and chains of dependent texture
  lookups (six chained, 3 minutes). Keep both out of the graph when changing
  it: per-text values go through the table's style texels.

## Files

| Path | Role |
|---|---|
| `build.py` | `hython build.py`: writes the nodegraph, `karmaNodeGraphs.json`, the three HDAs |
| `materialx/libraries/mtlx_text/` | `mtlx_text.mtlx` (generated), `mtlx_text_karma.mtlx` (Karma's ST Derivatives) |
| `python3.13libs/mtlx_text/` | fonts, msdf, atlas, layout, lop, sop, hda builders, callbacks; `hython -m mtlx_text bake/fonts/table` (`table`: a text table and the node's input values as JSON, for a .mtlx written without Houdini's node; `examples/minimal/`) |
| `otls/` | built locally by `hython build.py`, with your own Houdini licence: `mtlx_text.hda` (the node: one shader of the nodedef, from `mtlx2hda`), `mtlx_text_layout_lop.hda`, `mtlx_text_layout.hda` (SOP) |
| `bin/` | `msdfgen.exe` (Windows), the native atlas baker; see NOTICE |
| `tests/` | `test_layout.py`, `test_render.py`, `test_lop.py`, `test_features.py`, `mtlx_eval.py` (reference evaluator) |
| `docs/DESIGN.md` | why it is built this way: the measurements behind each choice |

Environment: `MTLX_TEXT_CACHE` (atlas and node tables; default
`$HIP/tex/mtlx_text`), `MTLX_TEXT_FONT_PATH` (more font folders),
`MTLX_TEXT_MSDF` (`auto`, `numpy` or `msdfgen`: the atlas baker),
`MTLX_TEXT_MSDFGEN` (path of a msdfgen executable, else `bin/` then PATH).
