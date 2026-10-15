"""Optional native backend of the atlas bake: Viktor Chlumsky's msdfgen
(https://github.com/Chlumsky/msdfgen, MIT), run once per glyph.

It gets the glyph's contours as read by fontTools (a text shape description),
so the geometry is the numpy backend's, and samples the same pixel centres:
msdfgen evaluates pixel (i, j) at ((i + 0.5) / scale - tx, (j + 0.5) / scale - ty).
Its output, normalised to 0..1 over a range of 2 * rng em, is mapped back to
signed em, clamped to +-rng; beyond FAR_TEXELS from the outline every channel
takes the true distance, as in msdf.mtsdf. Orders of magnitude faster than
numpy on large or complex glyphs.

Selection: MTLX_TEXT_MSDF = auto (default: msdfgen when found, else numpy),
numpy or msdfgen. The executable: MTLX_TEXT_MSDFGEN, else bin/msdfgen(.exe)
in the package folder, else msdfgen on PATH.
"""
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import msdf

PKG_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "bin")


def executable():
    """The msdfgen executable, or None."""
    env = os.environ.get("MTLX_TEXT_MSDFGEN")
    if env and os.path.isfile(env):
        return env
    for name in ("msdfgen.exe", "msdfgen"):
        p = os.path.join(PKG_BIN, name)
        if os.path.isfile(p):
            return p
    return shutil.which("msdfgen")


def backend(choice=None):
    """'msdfgen' or 'numpy', from `choice` or MTLX_TEXT_MSDF (auto by default)."""
    choice = (choice or os.environ.get("MTLX_TEXT_MSDF") or "auto").lower()
    if choice == "numpy":
        return "numpy"
    if executable():
        return "msdfgen"
    if choice == "msdfgen":
        raise RuntimeError("MTLX_TEXT_MSDF=msdfgen but no msdfgen executable: set MTLX_TEXT_MSDFGEN, "
                           "or put msdfgen in %s or on PATH" % PKG_BIN)
    return "numpy"


def shape_description(contours):
    """msdfgen's text shape description of contours (lists of edges; an edge
    is its control points: 2 = line, 3 = quadratic, 4 = cubic)."""
    parts = []
    for contour in contours:
        if not contour:
            continue
        s = []
        for e in contour:
            s.append("%.9g, %.9g;" % tuple(e[0]))
            if len(e) > 2:
                s.append("(" + "; ".join("%.9g, %.9g" % tuple(p) for p in e[1:-1]) + ");")
        parts.append("{ " + " ".join(s) + " # }")
    return "\n".join(parts)


def _glyph(exe, contours, cell_px, px_per_em, tx, ty, rng, workdir, k):
    out = np.full((cell_px, cell_px, 4), -rng, dtype=np.float32)
    desc = shape_description(contours)
    if not desc:
        return out
    src = os.path.join(workdir, "g%d.txt" % k)
    dst = os.path.join(workdir, "g%d.bin" % k)
    with open(src, "w") as fh:
        fh.write(desc)
    subprocess.run([exe, "mtsdf", "-shapedesc", src, "-dimensions", str(cell_px), str(cell_px),
                    "-scale", repr(float(px_per_em)), "-translate", repr(float(tx)), repr(float(ty)),
                    "-range", repr(2.0 * rng), "-format", "binfloat", "-o", dst],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    v = np.fromfile(dst, dtype="<f4").reshape(cell_px, cell_px, 4)    # rows from the bottom
    d = (v - 0.5) * (2.0 * rng)
    far = np.abs(d[..., 3]) > msdf.FAR_TEXELS / float(px_per_em)
    d[far, :3] = d[far, 3:4]
    np.clip(d, -rng, rng, out=d)
    return d.astype(np.float32)


def fields(glyphs, cell_px, px_per_em, cell, baseline, rng):
    """The (cell_px, cell_px, 4) field of every glyph (font index, code point,
    advance, contours), rows from the bottom, like msdf.mtsdf on the bake's grid."""
    exe = executable()
    with tempfile.TemporaryDirectory(prefix="mtlx_text_msdfgen_") as wd:
        jobs = [(exe, c, cell_px, px_per_em, cell / 2.0 - adv / 2.0, baseline, rng, wd, k)
                for k, (_, _, adv, c) in enumerate(glyphs)]
        with ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 4) - 2)) as pool:
            return list(pool.map(lambda a: _glyph(*a), jobs))
