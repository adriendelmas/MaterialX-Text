"""MtlX Text Layout LOP: per-prim text in the stage -> text table + primvars.

For every prim under the pattern that has a string primvar (`text` by
default), constant or uniform (one text per face), each distinct text is laid
out once with the fonts of the MtlX Text shader of the given material, the
rows are written as its text table, and the prim gets the primvars text_uv and
text_lines with the text primvar's interpolation. The shader is set to read
them (inputs:textsource = 1) from that table (inputs:table). Each text's row
also holds its style (layout.STYLE): primvars text_<input> of the prim, the
shader's own values otherwise.
"""
import os

from . import atlas, layout

SHADER_ID = "ND_textsdf_v2"


def _text_shader(stage, material_path):
    """The MtlX Text shader under the material."""
    from pxr import Usd, UsdShade
    prim = stage.GetPrimAtPath(material_path)
    if not prim:
        return None
    for p in Usd.PrimRange(prim):
        sh = UsdShade.Shader(p)
        if sh and sh.GetIdAttr() and sh.GetIdAttr().Get() == SHADER_ID:
            return sh
    return None


def _values(pv, n, default, time):
    """A primvar's values as a list of n (1 for constant), or the default."""
    if not pv or not pv.HasValue():
        return [default] * n
    v = pv.ComputeFlattened(time)
    if v is None:
        return [default] * n
    if not pv.GetTypeName().isArray:        # a scalar, or one vector / color
        return [v] * n
    v = list(v)
    if len(v) == 1:
        return v * n
    return v if len(v) == n else [default] * n


def cook(node):
    import hou
    from pxr import Sdf, Usd, UsdGeom, Vt, Gf
    hda = node.parent()
    stage = node.editableStage()
    time = Usd.TimeCode(hou.frame()) if hda.evalParm("time_dependent") else Usd.TimeCode.Default()

    shader = _text_shader(stage, hda.evalParm("material"))
    if shader is None:
        raise hou.NodeError("No MtlX Text shader under %s." % hda.evalParm("material"))
    a_in = shader.GetInput("atlas")
    exr = a_in.Get().resolvedPath or a_in.Get().path if a_in and a_in.Get() else ""
    if not exr or not os.path.isfile(atlas.metrics_path(exr)):
        raise hou.NodeError("The MtlX Text shader has no baked atlas: press Bake Atlas on its node.")
    fs = layout.FontSet.from_atlas(exr)

    name = hda.evalParm("text_primvar")
    d_font, d_width = hda.evalParm("text_font"), hda.evalParm("text_width")
    d_align = layout.ALIGNS[hda.parm("text_align").evalAsInt()]
    d_track = hda.evalParm("text_tracking")
    pattern = hda.evalParm("primpattern") or "/"
    roots = [p for p in hou.LopSelectionRule(pattern).expandedPaths(stage=stage)] or [Sdf.Path("/")]

    node_style = {}
    for sname, _ in layout.STYLE:
        inp = shader.GetInput(sname)
        node_style[sname] = inp.Get(time) if inp else None

    table, index, encoded, todo, warns = layout.Table(), {}, {}, [], set()
    seen = set()
    for root in roots:
        for prim in Usd.PrimRange(stage.GetPrimAtPath(root)):
            if prim.GetPath() in seen:
                continue
            seen.add(prim.GetPath())
            api = UsdGeom.PrimvarsAPI(prim)
            pv = api.GetPrimvar(name)
            if not pv or not pv.HasValue():
                continue
            interp = pv.GetInterpolation()
            texts = pv.ComputeFlattened(time)
            texts = [texts] if isinstance(texts, str) else list(texts or [])
            n = len(texts)
            if n == 0:
                continue
            fonts = _values(api.GetPrimvar("text_font"), n, d_font, time)
            widths = _values(api.GetPrimvar("text_width"), n, d_width, time)
            aligns = _values(api.GetPrimvar("text_align"), n, d_align, time)
            tracks = _values(api.GetPrimvar("text_tracking"), n, d_track, time)
            styles = {sname: _values(api.GetPrimvar("text_" + sname), n, node_style[sname], time)
                      for sname, _ in layout.STYLE}
            keys = []
            for e, (t, f, w, a, tr) in enumerate(zip(texts, fonts, widths, aligns, tracks)):
                if not isinstance(a, str):
                    a = layout.ALIGNS[max(0, min(int(a), len(layout.ALIGNS) - 1))]
                key = (str(t), int(f), float(w), a if a in layout.ALIGNS else d_align, float(tr))
                if key not in encoded:
                    encoded[key] = layout.encode(
                        layout.layout(key[0], fs, key[1], key[2], key[3], key[4]), fs,
                        warn=lambda m, s=key[0]: warns.add("%r: %s" % (s[:20], m)))
                style = layout.style_vector({sname: v[e] for sname, v in styles.items()})
                if (key, style) not in index:
                    index[(key, style)] = table.add(encoded[key], style)
                keys.append(index[(key, style)])
            todo.append((api, interp if n > 1 else UsdGeom.Tokens.constant, keys))

    path = hda.evalParm("table")
    tile = 1001
    if "<UDIM>" in path:
        tile = hda.evalParm("udim")
    out = path.replace("<UDIM>", str(tile))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    table.write(out)

    for api, interp, keys in todo:
        uvs, lns = [], []
        for k in keys:
            uv, ln = table.primvars(k, tile)
            uvs.append(Gf.Vec2f(*uv))
            lns.append(Gf.Vec2f(*ln))
        for pname, vals in (("text_uv", uvs), ("text_lines", lns)):
            p = api.CreatePrimvar(pname, Sdf.ValueTypeNames.Float2Array, interp)
            p.Set(Vt.Vec2fArray(vals))

    shader.CreateInput("table", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(path))
    shader.CreateInput("textsource", Sdf.ValueTypeNames.Int).Set(1)
    if warns:
        node.addWarning("; ".join(sorted(warns))[:2000])
