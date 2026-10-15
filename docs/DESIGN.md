# Design notes

Why MaterialX Text is built the way it is: the constraints found by
measurement on Houdini 22.0 (Karma CPU / XPU, Cycles through
[cycles-for-houdini](https://github.com/adriendelmas/cycles-for-houdini)),
and the design they led to. [METHOD.md](METHOD.md) is the reference for using it.

## Constraints

- **Karma XPU compile time grows with chained texture lookups** (a lookup
  whose coordinates come from another lookup): 6 chained atlas lookups
  compiled in 180 s, 8 independent ones in 15 s, 1000 arithmetic nodes in
  12 s. So no search in the shader: the text table holds, per bin of the
  line, the few glyphs that can reach it, and every glyph is read straight
  from the table.
- **Karma XPU compiles each primvar read** (about 2 s each): per-primitive
  settings are written into the text table by the layout node instead of
  being read as primvars.
- **Cycles has no vector4**, and an RGBA image reaches it through its colour
  only. Atlases and tables are RGB (vector3) or one channel.
- **Cycles' SVM stack is 255 floats**: the text cannot live in primvars, and
  the running union of glyphs carries few values.
- **A texture coordinate that jumps between neighbouring pixels widens the
  texture filter** (mip levels on CPU, filter width on XPU) and draws a faint
  seam. So the atlases have no mip levels, a glyph keeps one slot of the
  table in every bin it reaches, and the even line of text always comes first.
- **Houdini's VK viewport** ignores custom MaterialX nodedefs, shrinks
  textures wider than 2048 texels and reads a one-pixel-high texture as zeros.
  The table is folded into rows of 2048 texels and at least 4 rows high; the
  node is viewed in a Karma viewport.
- **Karma's smoothstep returns 0 when low == high**: the edge half width is
  kept above 1e-4 em.
- **fontTools 4.55, shipped with Houdini 22.0, lacks `_h_e_a_d.py`**: a copy
  is registered when the import fails.

## As built

- **Units**: 1 em = the font size. The node's `size` is 1 em in texture
  coordinates.
- **Atlas**: every glyph of a font set as a multi-channel signed distance
  field (RGB EXR, read as vector3) and its true signed distance (one-channel
  EXR), in em, clamped to +-0.25 em, no mip levels; a JSON of metrics
  (advances, ink bounds, GPOS / kern kerning, vertical metrics). Baked by
  msdfgen (native, bundled for Windows) or by a numpy reimplementation.
- **Layout** (Python, one implementation for the node, the LOP and the SOP):
  advances, kerning, tracking, word wrapping, alignment, font fallback.
- **Text table**: per line of text, 512 bins of 1/8 em, up to four glyphs per
  bin (x offset, atlas glyph, index in the text) and their words, then the
  text's per-text settings; folded into rows of 2048 texels.
- **Shader** (one MaterialX nodegraph of standard nodes): the two lines whose cells bracket the point,
  four candidate glyphs on each, read through their per-letter transforms;
  a running union keeps the largest distance and the winner's ids.
- **Houdini node**: one shader of the nodedef: a value change re-cooks in
  about 9 ms. A subnet of SideFX's MaterialX VOPs re-translated ~1100 nodes
  to USD on every change (about 1 s) and was dropped.
- **Karma XPU**: each wired output compiles once (10 to 25 s cold), then
  stays in the disk cache; a value change never recompiles.

## Abandoned

- **Pixel-footprint antialiasing** (dropped 2026-10-15: the render samples
  and Edge Softness are enough, and it was Karma only). The edge was
  widened by half a pixel, measured with Karma's ST Derivatives node
  (`kma_stderiv`, implemented natively by Karma, with no MaterialX
  definition in Houdini 22.0); it works on Karma CPU and XPU. Cycles never
  had it. To rebuild it, declare the node for validation and for
  mtlx2karma.py:

  ```xml
  <nodedef name="kma_stderiv_vector2" node="kma_stderiv" nodegroup="karma">
    <input name="input" type="vector2" value="0, 0" />
    <output name="derivS" type="vector2" />
    <output name="derivT" type="vector2" />
    <output name="derivScale" type="float" />
  </nodedef>
  ```

  then, in `build.py`, an `auto_aa` input and the pixel size in em of the
  point (px0, py0, before rotation):

  ```python
  der = G.n("kma_stderiv", "multioutput", "deriv",
            input=G.n("combine2", "vector2", in1=px0, in2=py0))
  fp = G.mul(G.max(G.n("magnitude", "float", **{"in": G.out(der, "derivS", "vector2")}),
                   G.n("magnitude", "float", **{"in": G.out(der, "derivT", "vector2")})),
             G.out(der, "derivScale"))
  hw = G.max(G.add(softness, G.mul(G.mul(auto_aa, fp), 0.5)), 1.0e-4)
  ```

  The same half pixel was added to the shadow's softness, and `fp` was the
  `footprint` output.

## Pitfalls

- **Karma XPU with a full system disk**: every new shader compile stops the
  OptiX device ("Invalid value", then husk hangs) while scenes already in
  the compile cache still render, so it looks like a shader bug. Check the
  free space of the drive holding the temp and cache folders first
  (2026-10-15).
- **Karma XPU compile time and the text primvars** (measured cold, OptiX
  and VEX code caches private, 2026-10-16): with `text_uv` / `text_lines`
  constant on a prim, XPU specialises the shader on them and the text
  compiles in about 15 s; with one value per face (uniform or faceVarying,
  several texts on one prim) the same shader takes about 95 s. A missing
  table file costs as much as per-face primvars; `<UDIM>` in the table path
  about 45 % more than a plain tile. XPU does not specialise on parameter
  values: a uniform switch does not drop code. At build time, leaving out
  jitter halves the per-face case (43 s); fewer slots cut it too.
- **Measuring XPU compiles**: a mesh outside the camera never compiles its
  material, and the VEX code cache (`$HOUDINI_TEMP_DIR/VEX_CodeCache`)
  survives a fresh `OPTIX_CACHE_PATH`; `husk -V 6a` prints the compile
  count and the longest compile.

## Verification

`tests/` renders with Karma CPU, Karma XPU and Cycles and compares every
pixel with a numpy evaluation of the same nodegraph (`tests/mtlx_eval.py`):
text from the node and from primvars, the LOP, every output and effect.
