"""Render test: hython tests/test_render.py <out_dir> [cpu,xpu,cycles] [options]

One grid, rendered three times by each engine: with the text from the
node's Text parameter, with the text from primvars text0.. (written by a
Python SOP through the same layout code), and with its texture coordinates.
Checks:
  * the parameter and primvar renders match;
  * the rendered mask matches the reference evaluator of the nodegraph
    (tests/mtlx_eval.py) run at each pixel's rendered texture coordinates;
  * there is text.
Writes <out_dir>/<engine>.png (the parameter render) and the EXRs.

Options: --fonts a.ttf,b.ttf  --text "..."  --tracking T  --leading L
         --width W  --align left|center|right|justify  --font N  --res W H
"""
import argparse
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
from mtlx_text import layout  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("engines", nargs="?", default="cpu,xpu")
ap.add_argument("--fonts", default=None, help="a.ttf,b.ttf (default: MTLX_TEXT_TEST_FONTS)")
ap.add_argument("--text", default="Hello Houdini, \u00e9t\u00e9 jgpq\nMaterialX text in the shader")
ap.add_argument("--tracking", type=float, default=0.0)
ap.add_argument("--leading", type=float, default=1.2)
ap.add_argument("--width", type=float, default=11.0)
ap.add_argument("--align", default="center")
ap.add_argument("--font", type=int, default=0)
ap.add_argument("--res", type=int, nargs=2, default=(1024, 512))
ap.add_argument("--samples", type=int, default=4)
ap.add_argument("--size", type=float, default=0.075, help="1 em in UV units")
args = ap.parse_args()

ENGINES = {"cpu": "BRAY_HdKarma", "xpu": "BRAY_HdKarmaXPU", "cycles": "HdCyclesPlugin"}
SIZE = args.size        # 1 em in UV units of a 0..1 grid


def build_scene(OUT_DIR):
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
    py = sop.createNode("python")
    py.setInput(0, uvw)
    out = sop.createNode("output")
    out.setInput(0, py)

    matlib = stage.createNode("materiallibrary")
    matlib.setInput(0, sc)
    matlib.parm("matpathprefix").set("/materials/")
    nodes = {}
    for name in ("param", "prim"):
        t = matlib.createNode("mtlxtextsdf", name + "_text")
        fl = args.fonts.split(",") if args.fonts else test_fonts()
        t.parm("fonts").set(len(fl))
        for k, f in enumerate(fl):
            t.parm("font%d" % (k + 1)).set(f)
        t.parm("text").set(args.text)
        t.parm("text_font").set(args.font)
        t.parm("text_width").set(args.width)
        t.parm("text_align").set(layout.ALIGNS.index(args.align))
        t.parm("text_tracking").set(args.tracking)
        mtlx_text.on_fonts(t)               # parm.set() does not run callbacks
        t.parm("size").set(SIZE)
        t.parmTuple("origin").set((0.5 - SIZE * args.width / 2, 0.9))
        t.parm("aspect").set(2.0)            # a 2:1 grid with a 0..1 x 0..1 uv
        t.parm("leading").set(args.leading)
        t.parmTuple("fg").set((1, 1, 1))
        t.parmTuple("bg").set((0, 0, 0))
        surf = matlib.createNode("mtlxsurface_unlit", "mat_" + name)
        surf.setNamedInput("emission_color", t, "color")
        nodes[name] = t
    tc = matlib.createNode("mtlxtexcoord")
    sep = matlib.createNode("mtlxseparate2")
    sep.setInput(0, tc)
    comb = matlib.createNode("mtlxcombine3")
    comb.setInput(0, sep, 0)
    comb.setInput(1, sep, 1)
    surf = matlib.createNode("mtlxsurface_unlit", "mat_uv")
    surf.setNamedInput("emission_color", comb, "out")
    matlib.parm("materials").set(3)
    for n, name in enumerate(("mat_param", "mat_prim", "mat_uv")):
        matlib.parm("matnode%d" % (n + 1)).set(name)
        matlib.parm("matpath%d" % (n + 1)).set(name)
    exr = nodes["param"].evalParm("atlas")
    assert exr and os.path.isfile(exr), "atlas not baked"
    # the "prim" material reads a 4-text table; the grid's primvars pick the
    # fourth text, the node's own values point elsewhere: a primvar that is
    # not read gives another text; its style (per-text settings) is the
    # "param" node's, and the "prim" node's own size is wrong: Primvars mode
    # must take every setting from the table
    style = layout.style_vector({n: nodes["param"].parmTuple(n).eval() for n, _ in layout.STYLE})
    table = os.path.join(OUT_DIR, "table.exr").replace(os.sep, "/")
    py.parm("python").set(
        "from mtlx_text import layout\n"
        "geo = hou.pwd().geometry()\n"
        "fs = layout.FontSet.from_atlas(%r)\n"
        "texts = ['row zero', 'row one', 'row two, the wrong one', %r]\n"
        "tab = layout.Table()\n"
        "ks = [tab.add(layout.text_rows(t, fs, font=%d, width=%r, align=%r, tracking=%r), %r) for t in texts]\n"
        "tab.write(%r)\n"
        "uv, ln = tab.primvars(ks[3])\n"
        "geo.addAttrib(hou.attribType.Prim, 'text_uv', (0.0, 0.0))\n"
        "geo.addAttrib(hou.attribType.Prim, 'text_lines', (0.0, 0.0))\n"
        "for prim in geo.prims():\n"
        "    prim.setAttribValue('text_uv', uv)\n"
        "    prim.setAttribValue('text_lines', ln)\n"
        % (exr, args.text, args.font, args.width, args.align, args.tracking, style, table))
    py.cook(force=True)
    nodes["prim"].parm("table").set(table)
    nodes["prim"].parm("textsource").set(1)        # read the primvars
    nodes["prim"].parm("size").set(nodes["param"].evalParm("size") * 2.0)

    assign = stage.createNode("assignmaterial")
    assign.setInput(0, matlib)
    assign.parm("primpattern1").set("/geo/grid/*")
    cam = stage.createNode("camera")
    cam.setInput(0, assign)
    cam.parmTuple("t").set((0, 0, 2.5))
    cam.parm("focalLength").set(50)
    cam.parm("horizontalAperture").set(41.4214)
    rs = stage.createNode("karmarenderproperties")
    rs.setInput(0, cam)
    rs.parm("camera").set("/cameras/camera1")
    rs.parm("samplesperpixel").set(args.samples)
    rs.parm("colorlimit").set(1e6)
    return assign, rs, nodes


def render(assign, rs, mat, engine, out_dir):
    assign.parm("matspecpath1").set("/materials/" + mat)
    usd = os.path.join(out_dir, "scene_%s.usda" % mat)
    rs.stage().Export(usd)
    exr = os.path.join(out_dir, "%s_%s.exr" % (engine, mat))
    cmd = [os.path.join(hou.getenv("HFS"), "bin", "husk"), "--renderer", ENGINES[engine],
           "--settings", "/Render/rendersettings", "--res", str(args.res[0]), str(args.res[1]),
           "-o", exr, usd]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL)
    import OpenImageIO as oiio
    oiio.attribute("threads", 1)
    i = oiio.ImageInput.open(exr)
    a = np.asarray(i.read_image("float"))
    i.close()
    return a


def check(engine, imgs, ref_iface, out_dir):
    from PIL import Image
    param, prim, uvimg = imgs["mat_param"], imgs["mat_prim"], imgs["mat_uv"]
    cover = uvimg[..., 3] > 0.999                       # pixels fully on the grid
    m_param, m_prim = param[..., 0], prim[..., 0]
    d_pp = np.abs(m_param - m_prim)[cover].max()
    # reference mask at the rendered texture coordinates (pixel averages: the
    # renders filter, the evaluator samples, so compare where both are flat)
    ev = mtlx_eval.Evaluator(ref_iface)
    ref = np.asarray(ev.run(uvimg[..., :2], outputs=("mask",))["mask"])
    flat = cover & ((m_param < 0.02) | (m_param > 0.98))
    wrong = flat & (np.abs(ref - m_param) > 0.5)
    cov = (m_param[cover] > 0.5).mean()
    Image.fromarray((np.clip(m_param, 0, 1) * 255).astype(np.uint8)).save(os.path.join(out_dir, engine + ".png"))
    print("%s: coverage %.4f, max |param - prim| %.5f, pixels disagreeing with the reference %d / %d"
          % (engine, cov, d_pp, wrong.sum(), flat.sum()))
    assert cov > 0.0002, "no text"
    # the two renders differ by sampling noise only (none of the pixels by more than half)
    diff = np.abs(m_param - m_prim)[cover]
    assert (diff > 0.5).sum() == 0 and diff.mean() < 5e-4, "parameter and primvar renders differ"
    assert wrong.sum() <= 1e-3 * flat.sum(), "render disagrees with the reference evaluator"


if __name__ == "__main__":
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    assign, rs, nodes = build_scene(out_dir)
    iface = mtlx_eval.node_interface(nodes["param"])
    for engine in args.engines.split(","):
        imgs = {m: render(assign, rs, m, engine, out_dir) for m in ("mat_param", "mat_prim", "mat_uv")}
        check(engine, imgs, iface, out_dir)
    print("OK")
