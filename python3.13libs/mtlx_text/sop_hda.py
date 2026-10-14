"""Builds the MtlX Text Layout SOP HDA: a subnet holding a Python SOP that
calls mtlx_text.sop.cook on its parent's parameters."""
import os

from . import layout

TYPE = "mtlxtextlayout"
LABEL = "MtlX Text Layout"
CODE = "import mtlx_text.sop\nmtlx_text.sop.cook(hou.pwd())\n"
HELP = """= MtlX Text Layout =

#type: node
#context: sop
#internal: mtlxtextlayout
#icon: SOP/font

\"\"\"Lays out each primitive's text for a MtlX Text material.\"\"\"

Reads a string primitive attribute (`text` by default), lays every distinct text
out with the fonts of a MtlX Text material node, writes the laid-out texts as the
material's *text table* (a float EXR, one row per text) and gives each primitive
the primvar `text_uv` that points the material at its row.

Set the material node to *Text From: Layout SOP* and its *Layout SOP* to this node.
Every other input of the material can also vary per primitive: a primitive
attribute `text_<input>` (for example `text_size`, `text_rotate`, `text_fg`,
`text_seed`) overrides the material's value; this node writes it in the
table with the text.

@parameters

Material:
    The MtlX Text material node whose fonts and atlas lay the text out.

Table:
    The text table this node writes. With `<UDIM>` in the path, the node writes
    the tile given by *UDIM Tile*: several Layout SOPs can then feed one material,
    each with its own tile.

UDIM Tile:
    The tile this node writes when the path holds `<UDIM>` (1001, 1002...).

Text Attribute:
    The string primitive attribute holding each primitive's text.

Font, Width, Align, Tracking:
    The layout settings, the same for every primitive unless the attribute named
    next to each exists on the primitives (an integer font index, a float width
    in em, an align string `left`, `center`, `right`, `justify` or its index, a
    float tracking in em).
"""


def build(path):
    import hou
    obj = hou.node("/obj")
    geo = obj.createNode("geo", "mtlx_text_layout_build")
    sub = geo.createNode("subnet", TYPE)
    py = sub.createNode("python", "layout")
    py.setInput(0, sub.indirectInputs()[0])
    py.parm("python").set(CODE)
    out = sub.createNode("output", "output0")
    out.setInput(0, py)
    if os.path.isfile(path):
        os.remove(path)
    hda = sub.createDigitalAsset(name=TYPE, hda_file_name=path, description=LABEL, min_num_inputs=1,
                                 max_num_inputs=1)
    d = hda.type().definition()
    ptg = d.parmTemplateGroup()
    mat = hou.StringParmTemplate("material", "Material", 1, string_type=hou.stringParmType.NodeReference,
                                 help="The MtlX Text material node (its fonts and atlas).")
    mat.setTags({"opfilter": "!!VOP!!", "oprelative": "."})
    ptg.append(mat)
    ptg.append(hou.StringParmTemplate("table", "Table", 1, default_value=("$HIP/tex/mtlx_text/$OS.<UDIM>.exr",),
                                      string_type=hou.stringParmType.FileReference,
                                      file_type=hou.fileType.Image,
                                      help="The text table written by this node; <UDIM> = the tile below."))
    ptg.append(hou.IntParmTemplate("udim", "UDIM Tile", 1, default_value=(1001,), min=1001, max=1100,
                                   help="The tile this node writes when the table path holds <UDIM>."))
    ptg.append(hou.SeparatorParmTemplate("sep1"))
    ptg.append(hou.StringParmTemplate("text_attrib", "Text Attribute", 1, default_value=("text",)))
    for name, label, tmpl in (
            ("text_font", "Font", hou.IntParmTemplate("text_font", "Font", 1, default_value=(0,), min=0, max=7)),
            ("text_width", "Width (em)", hou.FloatParmTemplate("text_width", "Width (em)", 1, default_value=(0.0,),
                                                               min=0.0, max=60.0)),
            ("text_align", "Align", hou.MenuParmTemplate("text_align", "Align", layout.ALIGNS,
                                                          ("Left", "Center", "Right", "Justify"))),
            ("text_tracking", "Tracking (em)", hou.FloatParmTemplate("text_tracking", "Tracking (em)", 1,
                                                                     default_value=(0.0,), min=-0.5, max=1.0))):
        tmpl.setJoinWithNext(True)
        ptg.append(tmpl)
        key = name.split("_")[1]
        ptg.append(hou.StringParmTemplate(key + "_attrib", "Attribute", 1, default_value=(name,),
                                          help="Primitive attribute overriding %s, if it exists." % label))
    d.setParmTemplateGroup(ptg)
    d.setDescription(LABEL)
    d.setIcon("SOP_font")
    d.addSection("HelpText", HELP)
    d.setExtraFileOption("UnlockOnCreate", False)
    d.save(path, hda)
    geo.destroy()
    print("HDA", TYPE, "->", path)
