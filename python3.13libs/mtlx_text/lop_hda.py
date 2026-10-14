"""Builds the MtlX Text Layout LOP HDA: a subnet holding a Python Script LOP
that calls mtlx_text.lop.cook with its parent's parameters."""
import os

from . import layout

TYPE = "mtlxtextlayout"
LABEL = "MtlX Text Layout"
CODE = "import mtlx_text.lop\nmtlx_text.lop.cook(hou.pwd())\n"
HELP = """= MtlX Text Layout =

#type: node
#context: lop
#internal: mtlxtextlayout
#icon: SOP/font

\"\"\"Lays out each prim's text for a MtlX Text material.\"\"\"

For every prim (under *Primitives*) with a string primvar `text` - one text for
the prim (constant) or one per face (uniform) - lays every distinct text out
with the fonts of the material's MtlX Text shader, writes them as the shader's
*text table* (a float EXR, a row per line of text) and gives the prim the
primvars `text_uv` and `text_lines`, which point the shader at its text. The
shader is set to read them: nothing to link by hand.

Any other input of the shader can vary per prim too: a primvar `text_<input>`
(`text_size`, `text_rotate`, `text_fg`, `text_seed`...) overrides its value.
This node reads it and writes it in the table with the text (re-cook it after
changing one); prims without it get the shader's value.
The layout itself reads, when the prim has them, `text_font` (int),
`text_width` (float, em), `text_align` (string or index) and `text_tracking`
(float, em).

@parameters

Material:
    The material holding the MtlX Text shader (its fonts and atlas). Bake the
    atlas on the shader's node first.

Primitives:
    The prims to look at (and below them). Prims without the text primvar are
    skipped.

Text Primvar:
    The string primvar holding the text.

Table:
    The text table this node writes. With `<UDIM>` in the path, the node writes
    the tile given by *UDIM Tile*: several layout nodes can then feed one
    material, each with its own tile.

Time Dependent:
    Read the text at the current frame (animated text) instead of its default.
"""


def build(path):
    import hou
    stage = hou.node("/stage")
    sub = stage.createNode("subnet", TYPE)
    py = sub.createNode("pythonscript", "layout")
    py.setInput(0, sub.indirectInputs()[0])
    py.parm("python").set(CODE)
    out = sub.node("output0") or sub.createNode("output", "output0")
    out.setInput(0, py)
    if os.path.isfile(path):
        os.remove(path)
    hda = sub.createDigitalAsset(name=TYPE, hda_file_name=path, description=LABEL, min_num_inputs=1,
                                 max_num_inputs=1)
    d = hda.type().definition()
    ptg = d.parmTemplateGroup()
    mat = hou.StringParmTemplate("material", "Material", 1, default_value=("/materials/text",),
                                 help="Material prim holding the MtlX Text shader.")
    mat.setTags({"script_action": "import loputils\nloputils.selectPrimsInParm(kwargs, False)",
                 "script_action_icon": "BUTTONS_reselect", "sidefx::usdpathtype": "prim"})
    ptg.append(mat)
    pat = hou.StringParmTemplate("primpattern", "Primitives", 1, default_value=("/",),
                                 help="Prims to look at, and below them.")
    pat.setTags({"script_action": "import loputils\nloputils.selectPrimsInParm(kwargs, True)",
                 "script_action_icon": "BUTTONS_reselect", "sidefx::usdpathtype": "primlist"})
    ptg.append(pat)
    ptg.append(hou.StringParmTemplate("text_primvar", "Text Primvar", 1, default_value=("text",)))
    ptg.append(hou.StringParmTemplate("table", "Table", 1, default_value=("$HIP/tex/mtlx_text/$OS.<UDIM>.exr",),
                                      string_type=hou.stringParmType.FileReference,
                                      file_type=hou.fileType.Image))
    ptg.append(hou.IntParmTemplate("udim", "UDIM Tile", 1, default_value=(1001,), min=1001, max=1100))
    ptg.append(hou.ToggleParmTemplate("time_dependent", "Time Dependent", default_value=False))
    ptg.append(hou.SeparatorParmTemplate("sep1"))
    ptg.append(hou.IntParmTemplate("text_font", "Font", 1, default_value=(0,), min=0, max=7,
                                   help="Font index in the shader's font list, unless the prim has text_font."))
    ptg.append(hou.FloatParmTemplate("text_width", "Width (em)", 1, default_value=(0.0,), min=0.0, max=60.0,
                                     help="Wrapping width, unless the prim has text_width. 0: no wrapping."))
    ptg.append(hou.MenuParmTemplate("text_align", "Align", layout.ALIGNS, ("Left", "Center", "Right", "Justify"),
                                    help="Unless the prim has text_align."))
    ptg.append(hou.FloatParmTemplate("text_tracking", "Tracking (em)", 1, default_value=(0.0,), min=-0.5,
                                     max=1.0, help="Unless the prim has text_tracking."))
    d.setParmTemplateGroup(ptg)
    d.setDescription(LABEL)
    d.setIcon("SOP_font")
    d.addSection("HelpText", HELP)
    d.save(path, hda)
    hda.destroy()
    print("HDA", "Lop/" + TYPE, "->", path)
