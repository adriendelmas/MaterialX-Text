"""Lays out the interface of the HDA that mtlx2hda.py generates from the
nodedef: Text and Fonts folders driving the hidden atlas and text inputs."""
import os

from . import atlas, layout

CB = "import mtlx_text; mtlx_text.%s(kwargs['node'])"
INTERNAL = ("atlas", "atlas_sdf", "atlas_cols", "atlas_rows", "cell", "baseline", "ascender",
            "descender", "range", "atlascolorspace", "atlas_sdfcolorspace", "table", "tablecolorspace", "uv", "lines", "textsource")
TEXT_PARMS = ("text", "text_font", "text_width", "text_align", "text_tracking")
IN_TEXT_MODE = "{ text_mode != text }"   # a menu condition compares its token
# the shader's inputs by tab (the Text tab adds them after the text and its layout)
TABS = {
    "Text": ("leading", "valign", "origin", "size", "aspect", "rotate", "slant"),
    "Look": ("fg", "bg", "weight", "softness"),
    "Variation": ("seed", "jitter_baseline", "jitter_rotate", "jitter_scale", "drop"),
    "Effects": ("corners", "outline_width", "outline_offset", "shadow_offset", "shadow_softness",
                "bevel_width"),
}


HELP = """= MtlX Text =

#type: node
#context: vop
#internal: mtlxtextsdf
#icon: SOP/font

\"\"\"Text drawn by the shader: Karma CPU, Karma XPU and Cycles.\"\"\"

Draws text from a font in the shader, with proportional spacing, kerning,
word wrapping, alignment, overlapping glyphs, sharp corners at any size,
per-letter ids and random variation, outline, drop shadow and bevel. Plug
`color` into a surface's colour, or `mask` into a mix or an opacity.

== Text ==

*Text From: Text Parameter*: the text typed below, the same on every primitive.

*Text From: Layout SOP*: each primitive's own text, laid out by a *MtlX Text
Layout* SOP. In Solaris, the *MtlX Text Layout* LOP does it without this
switch: it reads a string primvar `text` and sets the shader itself.

== Fonts ==

Pick one or more fonts (the menu lists the installed ones; a .ttf / .otf
file or `file.ttc#N` works too) and press *Bake Atlas*: every glyph of the
character sets is turned into a distance field, once, cached in
`$MTLX_TEXT_CACHE` or `$HIP/tex/mtlx_text`. A character the text's font lacks
is taken from the next font of the list.

== Placement and look ==

*Origin* and *Size* place the text in UV: *Size* is the font size (1 em) in
UV units. Set *Aspect* to the surface's height / width ratio when its UVs
span a non-square surface. *Leading* is the line pitch, *Rotate* and *Slant*
turn and italicise the block.

*Edge Softness* widens the edge (antialiasing otherwise comes from the
render samples). *Weight* makes the text bolder (positive) or thinner.

Seed, Baseline Jitter, Rotate Jitter, Scale Jitter and Missing Letters vary
each letter at random.

With a Layout LOP or SOP (Text From: Primvars), every input above can vary
per primitive with a primvar `text_<input>`, for example `text_size`,
`text_rotate`, `text_fg`, `text_seed`: the layout node writes it into the
text table, which the shader reads (no primvar read per input in the shader:
Karma XPU compiles it in seconds, not a minute).

@outputs

mask:
    The text, 0 to 1.
color:
    Background colour mixed to the text colour by the mask.
sdf:
    Signed distance to the edge of the text, in em, positive inside.
sdf_round:
    The same with round corners.
character_id:
    The index of the letter in its text: one value per letter.
glyph_index:
    The glyph in the atlas: identical letters share it.
line_id, word_id:
    The line and the word of the letter.
glyph_uv:
    0 to 1 coordinates inside the letter's cell.
random:
    A random value per letter.
outline, shadow:
    The outline band and the drop shadow.
bevel_height:
    The bevel profile: 0 at the edge, 1 at Bevel Width inside.

The full reference, including the contract for an HDA driving the text from
primvars, is `docs/METHOD.md` in the mtlx_text folder.
"""


def _py(pt, fn):
    pt.setScriptCallback(CB % fn)
    pt.setScriptCallbackLanguage(__import__("hou").scriptLanguage.Python)
    return pt


def text_folder():
    import hou
    f = hou.FolderParmTemplate("folder_text", "Text", folder_type=hou.folderType.Tabs)
    f.addParmTemplate(_py(hou.MenuParmTemplate(
        "text_mode", "Text From", ("text", "sop"), ("Text Parameter", "Layout SOP (per primitive)"),
        help="Text Parameter: the text below, the same on every primitive. Layout SOP: each "
             "primitive's own text, laid out by a MtlX Text Layout SOP into its text table; the "
             "primitive picks its row with the primvar text_uv."), "on_mode"))
    sop = hou.StringParmTemplate(
        "layout_sop", "Layout SOP", 1, string_type=hou.stringParmType.NodeReference,
        help="The MtlX Text Layout SOP whose table this material reads.")
    sop.setTags({"opfilter": "!!SOP!!", "oprelative": "."})
    sop.setConditional(hou.parmCondType.HideWhen, "{ text_mode == text }")
    f.addParmTemplate(_py(sop, "on_mode"))
    f.addParmTemplate(_py(hou.StringParmTemplate(
        "text", "Text", 1, default_value=("Text",), tags={"editor": "1", "editorlines": "3-10"},
        help="The text, laid out by the node into a one-row text table (cached, named after "
             "its content)."), "on_text"))
    f.addParmTemplate(_py(hou.IntParmTemplate(
        "text_font", "Font", 1, default_value=(0,), menu_type=hou.menuType.Normal,
        # two lines: Houdini does not run "import x; return y" written on one
        item_generator_script="import mtlx_text\nreturn mtlx_text.text_font_menu(kwargs['node'])",
        item_generator_script_language=hou.scriptLanguage.Python,
        help="Which font of the Fonts list sets this text. Characters it lacks come from the "
             "next fonts of the list."), "on_text"))
    f.addParmTemplate(_py(hou.MenuParmTemplate(
        "text_align", "Align", layout.ALIGNS, ("Left", "Center", "Right", "Justify"),
        help="Horizontal alignment of each line. Center and Right align on the origin when "
             "Width is 0, inside the width otherwise. Justify needs a width."), "on_text"))
    f.addParmTemplate(_py(hou.FloatParmTemplate(
        "text_width", "Width (em)", 1, default_value=(0.0,), min=0.0, max=60.0, min_is_strict=True,
        help="Line width for word wrapping, in em. 0: no wrapping, lines break at newlines only."),
        "on_text"))
    f.addParmTemplate(_py(hou.FloatParmTemplate(
        "text_tracking", "Tracking (em)", 1, default_value=(0.0,), min=-0.5, max=1.0,
        help="Added to every advance, in em. Negative values make glyphs overlap."), "on_text"))
    # the layout settings belong to the Layout SOP in that mode
    out = hou.FolderParmTemplate("folder_text", "Text", folder_type=hou.folderType.Tabs)
    for pt in f.parmTemplates():
        if pt.name() in TEXT_PARMS:
            pt.setConditional(hou.parmCondType.HideWhen, IN_TEXT_MODE)
        out.addParmTemplate(pt)
    return out


def fonts_folder():
    import hou
    f = hou.FolderParmTemplate("folder_fonts", "Fonts", folder_type=hou.folderType.Tabs)
    mp = hou.FolderParmTemplate("fonts", "Fonts", folder_type=hou.folderType.MultiparmBlock,
                                default_value=1)
    _py(mp, "on_fonts")
    font = hou.StringParmTemplate(
        "font#", "Font #", 1, string_type=hou.stringParmType.FileReference, file_type=hou.fileType.Any,
        menu_type=hou.menuType.StringReplace,
        item_generator_script="import mtlx_text\nreturn mtlx_text.font_menu()",
        item_generator_script_language=hou.scriptLanguage.Python,
        help="A .ttf / .otf font file, or a face of a collection as file.ttc#N. The menu lists "
             "the installed fonts.")
    font.setDefaultExpression(("__import__('mtlx_text').default_font()",))
    font.setDefaultExpressionLanguage((hou.scriptLanguage.Python,))
    mp.addParmTemplate(_py(font, "on_fonts"))
    f.addParmTemplate(mp)
    for name, label in atlas.CHARSETS.items():
        f.addParmTemplate(_py(hou.ToggleParmTemplate(
            "charset_" + name, label, default_value=(name == "western")), "on_fonts"))
    f.addParmTemplate(_py(hou.StringParmTemplate(
        "custom_chars", "Extra Characters", 1, default_value=("",),
        help="Any other characters to bake, typed or pasted."), "on_fonts"))
    f.addParmTemplate(_py(hou.MenuParmTemplate(
        "quality", "Quality", ("32", "48", "64", "96", "128", "192"), ("32 px / em", "48 px / em", "64 px / em", "96 px / em", "128 px / em (close-ups)", "192 px / em (close-ups)"),
        default_value=1, help="Atlas resolution. Corners stay sharp at any size; higher values "
                              "keep thin strokes and small details."), "on_fonts"))
    f.addParmTemplate(_py(hou.ButtonParmTemplate(
        "rebake", "Bake Atlas", help="Bake the atlas of the font list (cached in "
                                     "$MTLX_TEXT_CACHE, else $HIP/tex/mtlx_text)."), "on_fonts"))
    return f


def edit(path):
    import hou
    hou.hda.installFile(path)
    defs = hou.hda.definitionsInFile(path)
    assert len(defs) == 1, defs
    d = defs[0]
    ptg = d.parmTemplateGroup()
    for name in INTERNAL:
        pt = ptg.find(name)
        if pt is not None:
            pt.hide(True)
            ptg.replace(name, pt)
    # tabs as on the Font SOP: the text and its paragraph first, then the look
    def take(names):
        out = []
        for name in names:
            out.append(ptg.find(name))
            ptg.remove(name)
        return out
    text = text_folder()
    text.addParmTemplate(hou.SeparatorParmTemplate("sep_paragraph"))
    for pt in take(TABS["Text"]):
        text.addParmTemplate(pt)
    tabs = [text]
    for label, names in list(TABS.items())[1:]:
        f = hou.FolderParmTemplate("folder_" + label.lower(), label, folder_type=hou.folderType.Tabs)
        for pt in take(names):
            f.addParmTemplate(pt)
        tabs.append(f)
    tabs.append(fonts_folder())
    first = ptg.entries()[0]
    for f in tabs:
        ptg.insertBefore(first, f)
    d.setParmTemplateGroup(ptg)
    d.setDescription("MtlX Text")
    d.addSection("HelpText", HELP)
    # a new node bakes its default font and shows its default text at once
    d.addSection("OnCreated", "import mtlx_text\nmtlx_text.on_text(kwargs['node'])\n")
    d.setExtraFileOption("OnCreated/IsPython", True)
    d.save(path)
    print("HDA", d.nodeTypeName(), "->", path)
