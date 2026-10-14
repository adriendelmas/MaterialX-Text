"""Builds examples/mtlx_text_example.hip and renders examples/preview.png:
hython examples/build_example.py --font a.ttf [--font b.otf] [--no-render]
(fonts of your own: the first one sets the signs, the others are fallbacks)

A building with signs on its facades: one mesh, one face per sign, each face
with its own string attribute `text`, colour and seed (SOPs), the MtlX Text
Layout LOP turning them into the material's text table and primvars.
"""
import os
import subprocess
import sys

import hou

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "python3.13libs"))
import mtlx_text  # noqa: E402

FONTS = [a for k, a in enumerate(sys.argv[1:]) if sys.argv[k] == "--font"]
if not FONTS:
    raise SystemExit(__doc__)

for f in ("mtlx_text.hda", "mtlx_text_layout_lop.hda"):
    hou.hda.installFile(os.path.join(PKG, "otls", f))
hou.hipFile.clear(suppress_save_prompt=True)
hip = os.path.join(HERE, "mtlx_text_example.hip").replace(os.sep, "/")
hou.hipFile.setName(hip)

stage = hou.node("/stage")
city = stage.createNode("sopcreate", "city")
city.parm("pathprefix").set("/city/")
sop = city.node("sopnet/create")
box = sop.createNode("box", "building")
box.parmTuple("size").set((8, 14, 8))
box.parmTuple("t").set((0, 7, 0))
bname = sop.createNode("attribwrangle", "building_path")
bname.parm("class").set("primitive")
bname.parm("snippet").set('s@path = "/city/building";')
bname.setInput(0, box)
signs = sop.createNode("attribwrangle", "signs")
signs.parm("class").set("detail")
signs.parm("snippet").set(r'''
// one quad per sign on the facades, its uv 0..1, a text and a look per face
addvertexattrib(0, "uv", {0, 0, 0});
addprimattrib(0, "text", "");
addprimattrib(0, "text_fg", {1, 1, 1});
addprimattrib(0, "text_seed", 0.0);
string names[] = {"BOULANGERIE", "Caf\u00e9 du Nord", "HOTEL\nDES ARTS", "Pharmacie", "LIBRAIRIE",
                  "Cin\u00e9ma Rex", "TABAC", "Fleurs & Plantes", "OPTIQUE", "Bistrot 42",
                  "GARAGE", "Atelier Houdini"};
vector colors[] = {{1, 0.85, 0.3}, {0.3, 0.8, 1}, {1, 0.35, 0.35}, {0.4, 1, 0.5}};
int n = len(names);
for (int k = 0; k < n; k++) {
    int side = k % 4;
    float h = 2.0 + 2.6 * (k / 4) + 0.4 * rand(k + 0.3);
    float off = -1.5 + 3.0 * rand(k + 0.7);
    vector c = set(off, h, 4.02);
    matrix3 r = ident();
    rotate(r, radians(90.0 * side), {0, 1, 0});
    vector du = set(1.5, 0, 0) * r, dv = set(0, 0.5, 0) * r;
    c *= r;
    int pts[];
    vector corners[] = array(c - du - dv, c + du - dv, c + du + dv, c - du + dv);
    vector uvs[] = array({0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0});
    int prim = addprim(0, "poly");
    for (int j = 0; j < 4; j++) {
        int p = addpoint(0, corners[j]);
        int v = addvertex(0, prim, p);
        setvertexattrib(0, "uv", prim, j, uvs[j], "set");
    }
    setprimattrib(0, "path", prim, "/city/signs", "set");
    setprimattrib(0, "text", prim, names[k], "set");
    setprimattrib(0, "text_fg", prim, colors[k % 4], "set");
    setprimattrib(0, "text_seed", prim, float(k), "set");
}
''')
signs.setInput(0, bname)
out = sop.createNode("output")
out.setInput(0, signs)
sop.layoutChildren()

ml = stage.createNode("materiallibrary", "materials")
ml.setInput(0, city)
ml.parm("matpathprefix").set("/materials/")
t = ml.createNode("mtlxtextsdf", "sign_text")
t.parm("fonts").set(len(FONTS))
for k, f in enumerate(FONTS):
    t.parm("font%d" % (k + 1)).set(f)
t.parm("text").set("(per sign: MtlX Text Layout LOP)")
mtlx_text.on_fonts(t)
for name, value in (("size", 0.1), ("aspect", 3.0), ("valign", 1), ("jitter_rotate", 2.0),
                    ("jitter_baseline", 0.02)):
    t.parm(name).set(value)
t.parmTuple("origin").set((0.07, 0.5))         # left edge of the 8.6 em box, middle
t.parmTuple("bg").set((0.03, 0.03, 0.04))
surf = ml.createNode("mtlxstandard_surface", "sign")
surf.setNamedInput("base_color", t, "color")
surf.setNamedInput("emission_color", t, "color")
surf.parm("emission").set(0.6)
wall = ml.createNode("mtlxstandard_surface", "wall")
wall.parmTuple("base_color").set((0.55, 0.52, 0.48))
ml.parm("materials").set(2)
for k, name in enumerate(("sign", "wall")):
    ml.parm("matnode%d" % (k + 1)).set(name)
    ml.parm("matpath%d" % (k + 1)).set(name)
ml.layoutChildren()

assign = stage.createNode("assignmaterial")
assign.setInput(0, ml)
assign.parm("nummaterials").set(2)
assign.parm("primpattern1").set("/city/signs")
assign.parm("matspecpath1").set("/materials/sign")
assign.parm("primpattern2").set("/city/building")
assign.parm("matspecpath2").set("/materials/wall")

lay = stage.createNode("mtlxtextlayout", "text_layout")
lay.setInput(0, assign)
lay.parm("material").set("/materials/sign")
lay.parm("primpattern").set("/city")
lay.parm("text_align").set(1)
lay.parm("text_width").set(8.6)

sun = stage.createNode("distantlight::2.0", "sun")
sun.setInput(0, lay)
sun.parmTuple("r").set((-35, 30, 0))
dome = stage.createNode("domelight::3.0", "sky")
dome.setInput(0, sun)
dome.parm("xn__inputsintensity_i0a").set(0.4)
cam = stage.createNode("camera")
cam.setInput(0, dome)
cam.parmTuple("t").set((14, 9, 19))
cam.parmTuple("r").set((-8, 36, 0))
cam.parm("focalLength").set(35)
rs = stage.createNode("karmarenderproperties")
rs.setInput(0, cam)
rs.parm("camera").set("/cameras/camera1")
rs.parm("samplesperpixel").set(16)
rop = stage.createNode("usdrender_rop", "render")
rop.setInput(0, rs)
stage.layoutChildren()
rs.setDisplayFlag(True)
assert not lay.errors(), lay.errors()
hou.hipFile.save(hip)
print("saved", hip)

if "--no-render" not in sys.argv:
    usd = os.path.join(HERE, "_preview.usda")
    rs.stage().Export(usd)
    png = os.path.join(HERE, "preview.png")
    subprocess.check_call([os.path.join(hou.getenv("HFS"), "bin", "husk"), "--renderer", "BRAY_HdKarma",
                           "--settings", "/Render/rendersettings", "--res", "960", "720", "-o", png, usd],
                          stdout=subprocess.DEVNULL)
    os.remove(usd)
    print("rendered", png)
