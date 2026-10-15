"""Glyph atlas of a font set: MTSDF EXR + metrics JSON.

One atlas holds every glyph of every font of a set; a glyph is addressed by a
global index g (< 2048): cell (g mod cols, g div cols), rows counted from the
bottom (v up). All cells share one geometry: a square of `cell` em, the
glyph's advance centre on the cell's vertical centre line, the baseline
`baseline` em above the cell bottom. Texels hold signed distances in em,
positive inside, clamped to +-range: the atlas EXR holds the multi-channel
distances (RGB), its `_sdf` companion the true distance (one channel).
"""
import hashlib
import json
import math
import os
import time

import numpy as np

from . import fonts as fontmod
from . import msdf
from . import msdfgen

FORMAT = 6
GUTTER = 2                  # texels of "far outside" around every cell
MAX_GLYPHS = 2048

CHARSETS = {
    "western": "Latin-1 and the Windows-1252 extras",
    "latin_ext_a": "Latin Extended-A (Central and Eastern European)",
    "greek": "Greek",
    "cyrillic": "Cyrillic",
    "tibetan": "Tibetan (Microsoft Himalaya, Noto Serif Tibetan...; no shaping: stacks as the font draws them)",
}


def charset_codepoints(names, custom=""):
    """Ordered, unique code points of the named sets plus custom characters."""
    cps = []
    for n in names:
        if n == "western":
            cps += list(range(0x20, 0x7F)) + list(range(0xA0, 0x100))
            cps += [0x20AC, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030,
                    0x0160, 0x2039, 0x0152, 0x017D, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022,
                    0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x017E, 0x0178]
        elif n == "latin_ext_a":
            cps += list(range(0x100, 0x180))
        elif n == "greek":
            cps += list(range(0x391, 0x3AA)) + list(range(0x3B1, 0x3CA)) + list(range(0x386, 0x390))
        elif n == "cyrillic":
            cps += list(range(0x400, 0x460))
        elif n == "tibetan":        # the assigned code points of U+0F00..0FFF
            for lo, hi in ((0xF00, 0xF47), (0xF49, 0xF6C), (0xF71, 0xF97), (0xF99, 0xFBC),
                           (0xFBE, 0xFCC), (0xFCE, 0xFDA)):
                cps += list(range(lo, hi + 1))
        else:
            raise ValueError("unknown charset: " + n)
    cps += [ord(c) for c in custom]
    cps = [c for c in dict.fromkeys(cps) if c != 0x20 and c != 0xA0]   # spaces have no ink
    return cps


def cache_dir():
    d = os.environ.get("MTLX_TEXT_CACHE")
    if d:
        return d
    try:
        import hou
        if not hou.hipFile.isNewFile():
            return os.path.join(hou.getenv("HIP"), "tex", "mtlx_text")
        return os.path.join(hou.getenv("HOUDINI_TEMP_DIR") or os.path.expanduser("~"), "mtlx_text")
    except ImportError:
        return os.path.join(os.path.expanduser("~"), ".mtlx_text")


def atlas_key(font_refs, charsets, custom, px_per_em, rng, backend="numpy"):
    parts = [str(FORMAT), ",".join(charsets), custom, str(px_per_em), str(rng)]
    if backend != "numpy":          # numpy keys unchanged: existing atlases stay valid
        parts.append(backend)
    for ref in font_refs:
        path, _ = fontmod.split_ref(ref)
        st = os.stat(path)
        parts.append("%s|%d|%d" % (ref, st.st_size, int(st.st_mtime)))
    return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()[:10]


def atlas_path(font_refs, charsets=("western",), custom="", px_per_em=48, rng=0.25, directory=None, backend=None):
    """Path of the atlas EXR for this font set; the JSON is the same path with .json.
    backend: numpy or msdfgen (default: msdfgen.backend(), from MTLX_TEXT_MSDF)."""
    name = os.path.splitext(os.path.basename(fontmod.split_ref(font_refs[0])[0]))[0]
    if len(font_refs) > 1:
        name += "_and_%d" % (len(font_refs) - 1)
    key = atlas_key(font_refs, charsets, custom, px_per_em, rng, msdfgen.backend(backend))
    return os.path.join(directory or cache_dir(), "%s_%s.exr" % (name, key)).replace("\\", "/")


def metrics_path(exr):
    return os.path.splitext(exr)[0] + ".json"


def sdf_path(exr):
    """The true-distance companion of an atlas: one channel. Cycles reads only
    the colour of a 4-channel image (hdCycles), so the atlas is two files: RGB
    multi-channel distances, read as vector3, and this one, read as float."""
    return os.path.splitext(exr)[0] + "_sdf.exr"


def bake(font_refs, charsets=("western",), custom="", px_per_em=48, rng=0.25, out=None, log=print, backend=None):
    """Build the atlas of a font set (if its files are missing) and return the
    EXR path. `out` overrides the cache location; backend: numpy or msdfgen
    (default: msdfgen.backend(), from MTLX_TEXT_MSDF)."""
    backend = msdfgen.backend(backend)
    out = out or atlas_path(font_refs, charsets, custom, px_per_em, rng, backend=backend)
    if all(os.path.isfile(p) for p in (out, sdf_path(out), metrics_path(out))):
        return out
    t0 = time.time()
    cps = charset_codepoints(charsets, custom)
    fl = [fontmod.Font(r) for r in font_refs]
    # glyph list and the common cell geometry
    glyphs = []                 # (font index, code point, advance, contours)
    hw, ymin, ymax = 0.0, 0.0, 0.0
    for fi, f in enumerate(fl):
        for cp in cps:
            if not f.has(cp):
                continue
            c = f.contours(cp)
            adv = f.advance(cp)
            glyphs.append((fi, cp, adv, c))
            pts = [p for cc in c for e in cc for p in e]
            if pts:
                hw = max(hw, max(abs(p[0] - adv / 2) for p in pts))
                ymin = min(ymin, min(p[1] for p in pts))
                ymax = max(ymax, max(p[1] for p in pts))
        ymin = min(ymin, f.descender)
        ymax = max(ymax, f.ascender)
    if len(glyphs) > MAX_GLYPHS:
        raise ValueError("%d glyphs, the atlas holds %d: fewer fonts or charsets" % (len(glyphs), MAX_GLYPHS))
    ink = max(2 * hw, ymax - ymin)
    cell_px = int(math.ceil((ink + 2 * rng) * px_per_em))
    cell = cell_px / float(px_per_em)
    baseline = (cell - (ymax - ymin)) / 2 - ymin
    cols = int(math.ceil(math.sqrt(len(glyphs))))
    rows = int(math.ceil(len(glyphs) / float(cols)))
    atlas = np.full((rows * cell_px, cols * cell_px, 4), -rng, dtype=np.float32)
    ts = (np.arange(cell_px) + 0.5) / px_per_em
    meta_fonts = [{"ref": f.ref, "family": f.family, "style": f.style,
                   "ascender": f.ascender, "descender": f.descender, "glyphs": {}, "kern": []}
                  for f in fl]
    native = msdfgen.fields(glyphs, cell_px, px_per_em, cell, baseline, rng) if backend == "msdfgen" else None
    for g, (fi, cp, adv, c) in enumerate(glyphs):
        if native is not None:
            field = native[g]
        else:
            xs = ts - cell / 2 + adv / 2
            ys = ts - baseline
            field = msdf.mtsdf(c, xs, ys, rng)          # rows from the bottom
        # a gutter of "far outside" in every channel: bilinear filtering (and
        # the first mip levels) at a cell's edge mixes in the neighbour cell,
        # and a mix of multi-channel values can have a positive median,
        # dotted lines along the cell edges (seen 2026-10-11)
        field[:GUTTER], field[-GUTTER:], field[:, :GUTTER], field[:, -GUTTER:] = -rng, -rng, -rng, -rng
        cx, cy = g % cols, g // cols
        r0 = (rows - 1 - cy) * cell_px                    # image row 0 is the top
        atlas[r0:r0 + cell_px, cx * cell_px:(cx + 1) * cell_px] = field[::-1]
        # [global index, advance, ink left and right of the advance centre] in em
        pts = [p for cc in c for e in cc for p in e]
        xl = min(p[0] for p in pts) - adv / 2 if pts else 0.0
        xr = max(p[0] for p in pts) - adv / 2 if pts else 0.0
        meta_fonts[fi]["glyphs"][str(cp)] = [g, round(adv, 6), round(xl, 6), round(xr, 6)]
    for fi, f in enumerate(fl):
        have = [cp for cp in cps if f.has(cp)] + [0x20, 0xA0]
        kern = f.kerning([cp for cp in have if f.has(cp)])
        meta_fonts[fi]["kern"] = [[a, b, round(v, 6)] for (a, b), v in sorted(kern.items())]
        meta_fonts[fi]["space"] = f.advance(0x20) if f.has(0x20) else 0.25
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    _write_exr(np.ascontiguousarray(atlas[..., :3]), out)
    _write_exr(np.ascontiguousarray(atlas[..., 3:4]), sdf_path(out))
    meta = {"format": FORMAT, "atlas": os.path.basename(out), "px_per_em": px_per_em, "backend": backend,
            "range": rng, "cell": cell, "cell_px": cell_px, "baseline": baseline,
            "cols": cols, "rows": rows, "glyph_count": len(glyphs),
            "ascender": max(f.ascender for f in fl), "descender": min(f.descender for f in fl),
            "charsets": list(charsets), "custom": custom, "fonts": meta_fonts}
    with open(metrics_path(out), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    log("mtlx_text: baked %d glyphs of %d font(s) in %.1f s (%s) -> %s"
        % (len(glyphs), len(fl), time.time() - t0, backend, out))
    return out


def _write_exr(img, path):
    """Half-float EXR (any channel count), tiled, with NO mip levels: across a
    bin or line boundary the glyph a candidate reads changes, its atlas uv
    jumps, and the renderer's footprint picks a blurred mip level along that
    pixel line - the faint seams seen in sdf (2026-10-11). The shader
    antialiases from its own pixel footprint instead."""
    import OpenImageIO as oiio
    # OIIO's EXR writer crashes in hython with its own thread pool
    oiio.attribute("threads", 1)
    oiio.attribute("exr_threads", 1)
    h, w, nc = img.shape
    buf = oiio.ImageBuf(oiio.ImageSpec(w, h, nc, "float"))
    buf.set_pixels(oiio.ROI(), img)
    buf.set_write_format(oiio.HALF)
    buf.set_write_tiles(64, 64)
    buf.specmod().attribute("compression", "zip")
    tmp = path + ".tmp.exr"
    if not buf.write(tmp):
        raise RuntimeError("cannot write %s: %s" % (path, buf.geterror()))
    os.replace(tmp, path)


def load_metrics(exr):
    with open(metrics_path(exr), encoding="utf-8") as fh:
        return json.load(fh)
