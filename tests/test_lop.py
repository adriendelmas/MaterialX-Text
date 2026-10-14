"""Layout LOP test: hython tests/test_lop.py <out_dir> [cpu,xpu,cycles]

Two signs (two mesh prims), each with its own string primvar `text`, one
MtlX Text material, the MtlX Text Layout LOP. Checks that the LOP authors the
primvars and sets the shader, then renders and compares every pixel of each
sign with the reference evaluator run with that sign's primvars.
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
TEXTS = {"sign_a": "LEFT SIGN\nsecond line", "sign_b": "Right one, \u00e9t\u00e9"}
os.makedirs(OUT, exist_ok=True)

for f in ("mtlx_text.hda", "mtlx_text_layout_lop.hda"):
    hou.hda.installFile(os.path.join(PKG, "otls", f))
stage = hou.node("/stage")
sc = stage.createNode("sopcreate", "signs")
sc.parm("pathprefix").set("/geo/")
sop = sc.node("sopnet/create")
merge = sop.createNode("merge")
for k, (name, text) in enumerate(TEXTS.items()):
    g = sop.createNode("grid")
    g.parm("orient").set("xy")
    g.parmTuple("size").set((0.95, 0.95))
    g.parmTuple("t").set((-0.5 + k, 0, 0))
    g.parmTuple("rows").set((2,))
    g.parmTuple("cols").set((2,))
    w = sop.createNode("attribwrangle")
    w.parm("class").set("primitive")
    w.parm("snippet").set('s@path = "/geo/%s"; s@text = "%s";' % (name, text.replace("\n", "\\n")))
    w.setInput(0, g)
    uv = sop.createNode("attribwrangle")
    uv.parm("class").set("point")
    uv.parm("snippet").set("v@uv = set(fit(@P.x, %f, %f, 0, 1), fit(@P.y, -0.475, 0.475, 0, 1), 0);"
                           % (-0.5 + k - 0.475, -0.5 + k + 0.475))
    uv.setInput(0, w)
    merge.setNextInput(uv)
out = sop.createNode("output")
out.setInput(0, merge)

ml = stage.createNode("materiallibrary")
ml.setInput(0, sc)
ml.parm("matpathprefix").set("/materials/")
t = ml.createNode("mtlxtextsdf", "text_shader")
t.parm("fonts").set(1)
t.parm("font1").set(test_fonts()[0])
t.parm("text").set("unused")
mtlx_text.on_fonts(t)
t.parm("size").set(0.11)
t.parmTuple("origin").set((0.05, 0.85))
t.parm("leading").set(1.1)
surf = ml.createNode("mtlxsurface_unlit", "text")
surf.setNamedInput("emission_color", t, "color")
tc = ml.createNode("mtlxtexcoord")
sep = ml.createNode("mtlxseparate2")
sep.setInput(0, tc)
comb = ml.createNode("mtlxcombine3")
comb.setInput(0, sep, 0)
comb.setInput(1, sep, 1)
surf_uv = ml.createNode("mtlxsurface_unlit", "uvs")
surf_uv.setNamedInput("emission_color", comb, "out")
ml.parm("materials").set(2)
for n, name in enumerate(("text", "uvs")):
    ml.parm("matnode%d" % (n + 1)).set(name)
    ml.parm("matpath%d" % (n + 1)).set(name)
assign = stage.createNode("assignmaterial")
assign.setInput(0, ml)
assign.parm("primpattern1").set("/geo/*")
assign.parm("matspecpath1").set("/materials/text")

lay = stage.createNode("mtlxtextlayout")
lay.setInput(0, assign)
lay.parm("material").set("/materials/text")
lay.parm("primpattern").set("/geo")
lay.parm("table").set(os.path.join(OUT, "table.<UDIM>.exr").replace(os.sep, "/"))
lay.parm("text_align").set(1)
lay.parm("text_width").set(8.0)

cam = stage.createNode("camera")
cam.setInput(0, lay)
cam.parmTuple("t").set((0, 0, 2.6))
cam.parm("focalLength").set(50)
cam.parm("horizontalAperture").set(41.4214)
rs = stage.createNode("karmarenderproperties")
rs.setInput(0, cam)
rs.parm("camera").set("/cameras/camera1")
rs.parm("samplesperpixel").set(4)
rs.parm("colorlimit").set(1e6)

# what the LOP authored
st = lay.stage()
assert not lay.errors(), lay.errors()
from pxr import UsdGeom  # noqa: E402
pvs = {}
for name in TEXTS:
    api = UsdGeom.PrimvarsAPI(st.GetPrimAtPath("/geo/" + name))
    uvv, lnv = api.GetPrimvar("text_uv").Get(), api.GetPrimvar("text_lines").Get()
    assert uvv is not None and lnv is not None, name
    pvs[name] = {"text_uv": np.array(uvv[0]), "text_lines": np.array(lnv[0])}
    print(name, "text_uv", tuple(uvv[0]), "text_lines", tuple(lnv[0]))
from mtlx_text import lop  # noqa: E402
shader = lop._text_shader(st, "/materials/text")
assert shader.GetInput("textsource").Get() == 1
assert "<UDIM>" in shader.GetInput("table").Get().path
assert os.path.isfile(os.path.join(OUT, "table.1001.exr"))
assert pvs["sign_a"]["text_lines"][1] == 2 and pvs["sign_b"]["text_lines"][1] == 1


def render(mat, engine):
    assign.parm("matspecpath1").set("/materials/" + mat)
    usd = os.path.join(OUT, "scene_%s.usda" % mat)
    rs.stage().Export(usd)
    exr = os.path.join(OUT, "%s_%s.exr" % (engine, mat))
    subprocess.check_call([os.path.join(hou.getenv("HFS"), "bin", "husk"), "--renderer", RENDERERS[engine],
                           "--settings", "/Render/rendersettings", "--res", "1024", "512", "-o", exr, usd],
                          stdout=subprocess.DEVNULL)
    import OpenImageIO as oiio
    oiio.attribute("threads", 1)
    i = oiio.ImageInput.open(exr)
    a = np.asarray(i.read_image("float"))
    i.close()
    return a


iface = mtlx_eval.node_interface(t)
iface["textsource"] = 1
iface["table"] = os.path.join(OUT, "table.1001.exr").replace(os.sep, "/")
for engine in ENGINES:
    img, uvimg = render("text", engine), render("uvs", engine)
    cover = uvimg[..., 3] > 0.999
    h, w = cover.shape
    left = np.zeros_like(cover)
    left[:, : w // 2] = True
    from PIL import Image
    Image.fromarray((np.clip(img[..., 0], 0, 1) * 255).astype(np.uint8)).save(os.path.join(OUT, engine + ".png"))
    total_wrong = 0
    for name, half in (("sign_a", left), ("sign_b", ~left)):
        m = cover & half
        ref = np.asarray(mtlx_eval.Evaluator(iface, pvs[name]).run(uvimg[..., :2], outputs=("mask",))["mask"])
        r = img[..., 0]
        flat = m & ((r < 0.02) | (r > 0.98))
        wrong = flat & (np.abs(ref - r) > 0.5)
        cov = (r[m] > 0.5).mean()
        print("%s %s: coverage %.4f, pixels off the reference %d / %d" % (engine, name, cov, wrong.sum(), flat.sum()))
        assert cov > 0.005, "no text on " + name
        total_wrong += wrong.sum()
        assert wrong.sum() <= 1e-3 * flat.sum(), name + " disagrees with the reference"
print("OK")
