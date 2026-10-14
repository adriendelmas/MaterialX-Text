# MtlX Text v2 - production plan

Asked 2026-10-10: make the node production ready, all items of the list,
renderers **Karma (CPU, XPU) and Cycles (hdCycles)** only.

## Constraints found by measurement (2026-10-10 / 11)

* **Cycles has no vector4**, and hdCycles routes a 4-channel image to its
  colour only: no alpha. Data and atlases are RGB (vector3) or one channel.
* **Cycles' SVM stack is 255 floats.** Text data in primvars (42 vector3) and
  a union carrying many fields per candidate overflow it ("out of SVM stack
  space"). The text lives in a texture table; the union keeps few values.
* **hdCycles loaded one `libraries` folder** (Houdini's): it could not see a
  package's nodedef. Fixed in the delegate (`mtlx_expand.cpp`: also every
  `materialx/` folder on HOUDINI_PATH and `CYCLES_MATERIALX_SEARCH_PATH`),
  plus two log fixes (nodedef value defaults left to CompleteMaterialXDefaults;
  `kma_stderiv` translated to a zero derivative). Built 2026-10-11, tested
  from a temporary plugin tree; not yet committed nor installed.
* **Karma XPU compile time explodes with chains of dependent texture
  lookups** (a lookup whose coordinates come from another lookup):
  6 chained atlas lookups 180 s, 8 independent ones 15 s, 1000 arithmetic
  nodes 12 s (scratch probes, 2026-10-11). A binary search over the line is
  such a chain: no search, the table holds per-bin candidates.
* **Karma's smoothstep returns 0 when low == high**: the edge half width is
  kept above 1e-4 em.
* **Karma clamps sample values at 10** (Color Limit): debug renders of large
  values need colorlimit raised.
* `fontTools` 4.55 ships with Houdini 22.0 **without `_h_e_a_d.py`**: the
  vendored copy is registered when the import fails.
* `kma_stderiv` (Karma ST Derivatives, XPU supported) gives the footprint:
  max(|d em / ds|, |d em / dt|) * derivScale. No MaterialX definition: one is
  declared in `mtlx_text_karma.mtlx`.

## Design (as built)

* Units: 1 em = font size. `size` = 1 em in UV.
* Atlas: MTSDF, RGB multi-channel EXR (vector3) + `_sdf` true distance EXR
  (float), distances in em clamped to +-range, tiled and mipmapped; all fonts
  of a set in one atlas (global glyph index < 2048); metrics JSON with
  advances, ink bounds, GPOS/kern kerning, vertical metrics.
* Layout (Python, one implementation for the node and the SOP): advances,
  kerning, tracking, wrapping by width, left / center / right / justify, font
  fallback.
* Text table (format 6): one float RGB row per line of text, 256 bins of
  0.25 em, two glyphs per bin in three texels ((dx, g, i) twice, words),
  then the text's style texels (per-text values of 28 inputs). A primitive
  points at its text with primvars `text_uv`, `text_lines`; the node's own
  text uses the same table path (one-row-set table in the cache).
* Shader: two bracketing lines, two candidates each from two table texels,
  per-letter jitter (cellnoise), MSDF median, running union; winner rebuilt
  for ids, uv, word, round-corner distance, bevel; shadow re-reads the bins
  at the shifted point. Every placement / look input can come from a primvar
  `text_<input>`, which the layout node writes in the table's style texels.
* Houdini node: one shader of the nodedef (mtlx2hda), ~9 ms per value change.
  A VOP subnet of SideFX's nodes cost ~1 s per change and was dropped
  (2026-10-14); Houdini VK ignores custom nodedefs, so use a Karma viewport.
* Karma XPU compile, cold (shader only, 2026-10-11): 13 s; values never
  recompile (cached per network); each output wired differently compiles once.

## Phases

| # | Phase | State |
|---|---|---|
| 1 | Atlas: MTSDF, multi-font, glyph sets, metrics, kerning | done, tested (IoU, 0 wrong px on Impact A/W) |
| 2 | Layout + text table | done, tests/test_layout.py |
| 3 | Nodegraph core, Karma CPU | done, tests/test_render.py (0 px off the reference) |
| 4 | hdCycles custom libraries; Cycles render | done in the delegate (not committed); Cycles 0 px off |
| 5 | Shader features: AA, variations, jitter, overrides, effects | done; CPU, XPU, Cycles (0140) within the reference |
| 6 | Pipeline: font menu, cache, CLI, versioning, SOP HDA, help, example | font menu, cache, versioning, SOP code done; CLI, help, example left |
| 7 | Verification: golden images, perf, XPU compile time, viewport | XPU cold compile 16 s (was 63 s), Karma XPU viewport checked; golden images left |
