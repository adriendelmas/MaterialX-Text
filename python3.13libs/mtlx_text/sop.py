"""MtlX Text Layout SOP: per-primitive text -> text table + primvar text_uv.

Each primitive's string attribute is laid out with the fonts of a MtlX Text
material node (its atlas metrics), every distinct text once; the rows are
written as the text table (a UDIM tile of it when the path holds <UDIM>), and
each primitive gets text_uv = (UDIM column, row centre), which the material
reads to find its text. Optional per-primitive overrides: an int font index,
float width, string or int align, float tracking (attribute names are
parameters of the SOP). Each text's row also holds its style (layout.STYLE):
primitive attributes text_<input> (text_fg, text_size...), the material
node's values otherwise.
"""
import os

from . import atlas, layout


def _attr(geo, name):
    return geo.findPrimAttrib(name) if name else None


def cook(node):
    import hou
    hda = node.parent()
    geo = node.geometry()
    mat_path = hda.evalParm("material")
    mat = hda.node(mat_path) if mat_path else None
    if mat is None:
        raise hou.NodeError("Set the MtlX Text material node (its fonts lay the text out).")
    exr = mat.evalParm("atlas")
    if not exr or not os.path.isfile(atlas.metrics_path(exr)):
        raise hou.NodeError("The material %s has no baked atlas: press Bake Atlas on it." % mat.path())
    fs = layout.FontSet.from_atlas(exr)

    a_text = _attr(geo, hda.evalParm("text_attrib"))
    if a_text is None or a_text.dataType() != hou.attribData.String:
        raise hou.NodeError("No string primitive attribute '%s'." % hda.evalParm("text_attrib"))
    a_font = _attr(geo, hda.evalParm("font_attrib"))
    a_width = _attr(geo, hda.evalParm("width_attrib"))
    a_align = _attr(geo, hda.evalParm("align_attrib"))
    a_track = _attr(geo, hda.evalParm("tracking_attrib"))
    d_font, d_width = hda.evalParm("text_font"), hda.evalParm("text_width")
    d_align = layout.ALIGNS[hda.parm("text_align").evalAsInt()]
    d_track = hda.evalParm("text_tracking")

    def align_of(prim):
        if a_align is None:
            return d_align
        v = prim.attribValue(a_align)
        if isinstance(v, str):
            return v if v in layout.ALIGNS else d_align
        return layout.ALIGNS[max(0, min(int(v), len(layout.ALIGNS) - 1))]

    node_style = {n: mat.parmTuple(n).eval() if mat.parmTuple(n) else None for n, _ in layout.STYLE}
    a_style = {n: geo.findPrimAttrib("text_" + n) for n, _ in layout.STYLE}
    a_style = {n: a for n, a in a_style.items() if a is not None and a.dataType() != hou.attribData.String}

    keys, index, encoded, warns = [], {}, {}, set()
    table = layout.Table()
    for prim in geo.prims():
        key = (prim.attribValue(a_text),
               int(prim.attribValue(a_font)) if a_font else d_font,
               float(prim.attribValue(a_width)) if a_width else d_width,
               align_of(prim),
               float(prim.attribValue(a_track)) if a_track else d_track)
        if key not in encoded:
            encoded[key] = layout.encode(
                layout.layout(key[0], fs, key[1], key[2], key[3], key[4]), fs,
                warn=lambda m, t=key[0]: warns.add("%r: %s" % (t[:20], m)))
        style = layout.style_vector(dict(node_style, **{n: prim.attribValue(a) for n, a in a_style.items()}))
        if (key, style) not in index:
            index[(key, style)] = table.add(encoded[key], style)
        keys.append(index[(key, style)])

    path = hda.evalParm("table")
    tile = 1001
    if "<UDIM>" in path:
        tile = hda.evalParm("udim")
        path = path.replace("<UDIM>", str(tile))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    table.write(path)

    for name in ("text_uv", "text_lines"):
        if geo.findPrimAttrib(name) is None:
            geo.addAttrib(hou.attribType.Prim, name, (0.0, 0.0))
    uvs, lines = [], []
    for k in keys:
        uv, ln = table.primvars(k, tile)
        uvs += uv
        lines += ln
    geo.setPrimFloatAttribValues("text_uv", uvs)
    geo.setPrimFloatAttribValues("text_lines", lines)
    if warns:
        node.addWarning("; ".join(sorted(warns))[:2000])
