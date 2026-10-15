"""Feature test: hython tests/test_features.py <out_dir> [cpu,xpu,cycles]

One grid, one text, every per-letter variation and effect on. Each output of
the node is rendered alone (float EXR) and compared, pixel by pixel, with the
reference evaluator at the rendered texture coordinates, on pixels away from
edges (where the render samples' antialiasing makes no difference).
"""
import os
import subprocess
import sys

import numpy as np
import hou

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "python3.13libs"))
sys.path.insert(0, HERE)
import mtlx_text  # noqa: E402
import mtlx_eval  # noqa: E402
from fonts import test_fonts  # noqa: E402

OUT = os.path.abspath(sys.argv[1])
ENGINES = (sys.argv[2] if len(sys.argv) > 2 else "cpu").split(",")
RENDERERS = {"cpu": "BRAY_HdKarma", "xpu": "BRAY_HdKarmaXPU", "cycles": "HdCyclesPlugin"}
# output: (scale to bring it near 0..1 for the comparison tolerance, tolerance)
OUTPUTS = {"mask": (1.0, 0.05), "outline": (1.0, 0.05), "shadow": (1.0, 0.05),
           "bevel_height": (1.0, 0.05), "sdf": (4.0, 0.02), "sdf_round": (4.0, 0.02),
           "character_id": (1.0, 0.01), "word_id": (1.0, 0.01), "line_id": (1.0, 0.01),
           "random": (1.0, 0.01), "glyph_uv": (1.0, 0.01)}
ONLY = sys.argv[3].split(",") if len(sys.argv) > 3 else list(OUTPUTS)
os.makedirs(OUT, exist_ok=True)

hou.hda.installFile(os.path.join(PKG, "otls", "mtlx_text.hda"))
stage = hou.node("/stage")
sc = stage.createNode("sopcreate", "grid")
sc.parm("pathprefix").set("/geo/grid/")
sop = sc.node("sopnet/create")
g = sop.createNode("grid")
g.parm("orient").set("xy")
g.parmTuple("size").set((2.0, 1.0))
g.parmTuple("rows").set((2,))
g.parmTuple("cols").set((2,))
uvw = sop.createNode("attribwrangle")
uvw.parm("class").set("point")
uvw.parm("snippet").set("v@uv = set(fit(@P.x, -1, 1, 0, 1), fit(@P.y, -0.5, 0.5, 0, 1), 0);")
uvw.setInput(0, g)
o = sop.createNode("output")
o.setInput(0, uvw)

ml = stage.createNode("materiallibrary")
ml.setInput(0, sc)
ml.parm("matpathprefix").set("/materials/")
t = ml.createNode("mtlxtextsdf", "text_shader")
t.parm("fonts").set(1)
t.parm("font1").set(test_fonts()[1])
t.parm("text").set("JITTER words\nand EFFECTS")
t.parm("text_align").set(1)
t.parm("text_width").set(9.0)
mtlx_text.on_fonts(t)
for name, value in (("size", 0.11), ("aspect", 2.0), ("leading", 1.3), ("seed", 7.0),
                    ("jitter_baseline", 0.06), ("jitter_rotate", 12.0), ("jitter_scale", 0.15), ("drop", 0.15),
                    ("outline_width", 0.05), ("outline_offset", 0.03), ("shadow_softness", 0.04),
                    ("bevel_width", 0.05), ("rotate", 6.0), ("slant", 0.15)):
    t.parm(name).set(value)
t.parmTuple("origin").set((0.5 - 0.11 * 4.5, 0.85))
t.parmTuple("shadow_offset").set((0.06, 0.06))
comb = ml.createNode("mtlxcombine3", "to_color")
surf = ml.createNode("mtlxsurface_unlit", "probe")
surf.setNamedInput("emission_color", comb, "out")
tc = ml.createNode("mtlxtexcoord")
sep = ml.createNode("mtlxseparate2")
sep.setInput(0, tc)
comb_uv = ml.createNode("mtlxcombine3")
comb_uv.setInput(0, sep, 0)
comb_uv.setInput(1, sep, 1)
surf_uv = ml.createNode("mtlxsurface_unlit", "uvs")
surf_uv.setNamedInput("emission_color", comb_uv, "out")
ml.parm("materials").set(2)
for n, name in enumerate(("probe", "uvs")):
    ml.parm("matnode%d" % (n + 1)).set(name)
    ml.parm("matpath%d" % (n + 1)).set(name)
assign = stage.createNode("assignmaterial")
assign.setInput(0, ml)
assign.parm("primpattern1").set("/geo/grid/*")
cam = stage.createNode("camera")
cam.setInput(0, assign)
cam.parmTuple("t").set((0, 0, 2.5))
cam.parm("focalLength").set(50)
cam.parm("horizontalAperture").set(41.4214)
rs = stage.createNode("karmarenderproperties")
rs.setInput(0, cam)
rs.parm("camera").set("/cameras/camera1")
rs.parm("samplesperpixel").set(1)
rs.parm("colorlimit").set(1e6)
rs.parm("pixelfilter").set("box")
rs.parm("pixelfiltersize").set(1.0)


def connect(output):
    sepg = None
    for k in range(3):
        comb.setInput(k, None)
    if output == "glyph_uv":
        sepg = ml.node("sep_uv") or ml.createNode("mtlxseparate2", "sep_uv")
        sepg.setNamedInput("in", t, "glyph_uv")
        comb.setInput(0, sepg, 0)
        comb.setInput(1, sepg, 1)
    else:
        out_index = t.outputNames().index(output)
        for k in range(3):
            comb.setInput(k, t, out_index)


def render(mat, engine, tag):
    assign.parm("matspecpath1").set("/materials/" + mat)
    usd = os.path.join(OUT, "scene_%s.usda" % tag)
    rs.stage().Export(usd)
    exr = os.path.join(OUT, "%s_%s.exr" % (engine, tag))
    subprocess.check_call([os.path.join(hou.getenv("HFS"), "bin", "husk"), "--renderer", RENDERERS[engine],
                           "--settings", "/Render/rendersettings", "--res", "768", "384", "-o", exr, usd],
                          stdout=subprocess.DEVNULL)
    import OpenImageIO as oiio
    oiio.attribute("threads", 1)
    i = oiio.ImageInput.open(exr)
    a = np.asarray(i.read_image("float"))
    i.close()
    return a


iface = mtlx_eval.node_interface(t)
fails = []
for engine in ENGINES:
    uvimg = render("uvs", engine, "uvs")
    cover = uvimg[..., 3] > 0.999
    ev = mtlx_eval.Evaluator(iface)
    ref_all = ev.run(uvimg[..., :2], outputs=tuple(OUTPUTS))
    m = np.asarray(ref_all["mask"])

    def flat_where(ref):
        """Covered pixels whose 3x3 neighbourhood has about the same reference
        value (of this output and of the mask): away from every edge."""
        ref = ref if ref.ndim == 2 else ref[..., 0]
        f = cover.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                f &= np.abs(np.roll(np.roll(m, dy, 0), dx, 1) - m) < 0.01
                f &= np.abs(np.roll(np.roll(ref, dy, 0), dx, 1) - ref) < 0.05
        return f
    for name in ONLY:
        scale, tol = OUTPUTS[name]
        connect(name)
        img = render("probe", engine, name)
        ref = np.asarray(ref_all[name])
        if engine == "xpu":
            ref = np.maximum(ref, 0.0)      # Karma XPU clamps a negative emission to 0
        flat = flat_where(ref)
        got = img[..., :2] if name == "glyph_uv" else img[..., 0]
        bad = np.abs(got - ref) * scale > tol
        if bad.ndim == 3:
            bad = bad.any(-1)
        bad &= flat
        frac = bad.sum() / max(flat.sum(), 1)
        print("%s %-13s off the reference: %5d / %d (%.3f %%)" % (engine, name, bad.sum(), flat.sum(), 100 * frac))
        sys.stdout.flush()
        # XPU samples a pixel elsewhere than the uv render it is compared to: piecewise
        # outputs (ids, random, glyph uv) shift by a pixel along glyph boundaries
        limit = 0.01 if engine == "xpu" and name in ("character_id", "random", "glyph_uv") else 0.005
        if frac > limit:
            fails.append((engine, name, frac))
            from PIL import Image
            Image.fromarray((np.clip(np.abs(got - ref) * scale, 0, 1).reshape(got.shape[:2] + (-1,))[..., 0]
                             * 255).astype(np.uint8)).save(os.path.join(OUT, "diff_%s_%s.png" % (engine, name)))
assert not fails, fails
print("OK")
