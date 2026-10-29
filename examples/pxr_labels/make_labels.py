"""Text labels on grids from plain pxr, without Houdini's nodes:
    hython examples/pxr_labels/make_labels.py --font path/to/font.ttf --out out_dir
then renders:
    husk --renderer BRAY_HdKarmaXPU -s /Render/rs -o labels.png out_dir/labels.usda
    husk --renderer HdCyclesPluginGPU -s /Render/rs -o labels.png out_dir/labels.usda

One atlas, one text table holding every label, one material for all of them.
Each label is its own grid prim with CONSTANT primvars text_uv / text_lines
(its row in the table, textsource 1): Karma XPU then compiles the shader once,
fast; several texts on one prim (uniform primvars) compile about six times
slower. In textsource 1 the look and placement (size, origin, aspect, colours)
come from the table's style texels, set here through layout.style_vector.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "python3.13libs"))

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt  # noqa: E402

from mtlx_text import atlas, layout  # noqa: E402

LABELS = [
    "age: adult\nphenology: flowering\ncut: none",
    "age: young\nphenology: green\ncut: mowed",
]
W, H = 0.4, 0.3          # grid size in metres; uv 0..1 on both axes, u right, v up
LINES = 9                # the most lines a label may hold


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)

    exr = atlas.bake([os.path.abspath(a.font)], charsets=("western",), px_per_em=48,
                     out=os.path.join(out, "atlas.exr"), log=lambda m: None)
    m = atlas.load_metrics(exr)
    fs = layout.FontSet.from_atlas(exr)

    # 1 em = size in u; aspect = W / H keeps the em square on a W x H grid; the
    # block of LINES lines at leading 1.2 then spans about 1.2 * LINES em in height
    aspect = W / H
    size = 0.9 / (1.2 * LINES * aspect)
    style = layout.style_vector({"size": size, "aspect": aspect, "origin": [0.05, 0.95], "leading": 1.2,
                                 "fg": [0.95, 0.95, 0.95], "bg": [0.05, 0.05, 0.06]})
    table = layout.Table()
    keys = [table.add(layout.text_rows(t, fs, 0, 0.0, "left", 0.0), style) for t in LABELS]
    table_path = os.path.join(out, "labels_table.exr")
    table.write(table_path)

    stage = Usd.Stage.CreateNew(os.path.join(out, "labels.usda"))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    # the material: textsdf.color -> standard_surface.base_color
    mat = UsdShade.Material.Define(stage, "/Labels/Looks/label")
    txt = UsdShade.Shader.Define(stage, "/Labels/Looks/label/text")
    txt.CreateIdAttr("ND_textsdf_v2")
    for k, v in (("atlas", exr), ("atlas_sdf", atlas.sdf_path(exr)), ("table", table_path)):
        txt.CreateInput(k, Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(v.replace("\\", "/")))
    for k in ("cell", "baseline", "ascender", "descender", "range"):
        txt.CreateInput(k, Sdf.ValueTypeNames.Float).Set(float(m[k]))
    txt.CreateInput("atlas_cols", Sdf.ValueTypeNames.Float).Set(float(m["cols"]))
    txt.CreateInput("atlas_rows", Sdf.ValueTypeNames.Float).Set(float(m["rows"]))
    txt.CreateInput("textsource", Sdf.ValueTypeNames.Int).Set(1)
    color = txt.CreateOutput("color", Sdf.ValueTypeNames.Color3f)
    surf = UsdShade.Shader.Define(stage, "/Labels/Looks/label/surface")
    surf.CreateIdAttr("ND_standard_surface_surfaceshader")
    surf.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(color)
    surf.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set(0.6)
    mat.CreateSurfaceOutput("mtlx").ConnectToSource(surf.CreateOutput("out", Sdf.ValueTypeNames.Token))

    # one grid per label, side by side, facing +z; st = (0, 0) bottom left, (1, 1) top right
    for n, k in enumerate(keys):
        grid = UsdGeom.Mesh.Define(stage, "/Labels/label_%02d" % n)
        x0 = n * (W + 0.05)
        grid.CreatePointsAttr([Gf.Vec3f(x0, 0, 0), Gf.Vec3f(x0 + W, 0, 0), Gf.Vec3f(x0 + W, H, 0),
                               Gf.Vec3f(x0, H, 0)])
        grid.CreateFaceVertexCountsAttr([4])
        grid.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        grid.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        pv = UsdGeom.PrimvarsAPI(grid)
        pv.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex).Set(
            Vt.Vec2fArray([(0, 0), (1, 0), (1, 1), (0, 1)]))
        uv, lines = table.primvars(k)
        pv.CreatePrimvar("text_uv", Sdf.ValueTypeNames.Float2, UsdGeom.Tokens.constant).Set(Gf.Vec2f(*uv))
        pv.CreatePrimvar("text_lines", Sdf.ValueTypeNames.Float2, UsdGeom.Tokens.constant).Set(Gf.Vec2f(*lines))
        UsdShade.MaterialBindingAPI.Apply(grid.GetPrim()).Bind(mat)

    # a camera, a light and render settings, to look at the result
    span = len(LABELS) * (W + 0.05) - 0.05
    cam = UsdGeom.Camera.Define(stage, "/Labels/cam")
    cam.CreateFocalLengthAttr(50.0)
    cam.CreateHorizontalApertureAttr(36.0)
    cam.CreateVerticalApertureAttr(36.0 * 360 / 720)
    UsdGeom.Xformable(cam).AddTranslateOp().Set(Gf.Vec3d(span / 2, H / 2, 1.7 * span))
    __import__("pxr.UsdLux", fromlist=["x"]).DomeLight.Define(stage, "/Labels/dome").CreateIntensityAttr(1.0)
    rs = stage.DefinePrim("/Render/rs", "RenderSettings")
    rs.CreateRelationship("camera").SetTargets([cam.GetPath()])
    rs.CreateAttribute("resolution", Sdf.ValueTypeNames.Int2).Set(Gf.Vec2i(720, 360))
    stage.GetRootLayer().Save()
    print(os.path.join(out, "labels.usda"))


if __name__ == "__main__":
    main()
