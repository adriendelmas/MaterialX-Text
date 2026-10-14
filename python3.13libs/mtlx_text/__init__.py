"""MtlX Text: text drawn by a MaterialX shader (Karma CPU / XPU, Cycles).

Modules: fonts (outlines, metrics, installed fonts), msdf (distance fields),
atlas (font set -> MTSDF EXR + metrics JSON), layout (text -> text primvars),
hda (the node's interface). This module holds the HDA callbacks and the API
a SOP or a script uses. See docs/METHOD.md.
"""
import os

from . import atlas, layout  # noqa: F401

ALIGNS = layout.ALIGNS
DEFAULT_FONTS = ("arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc", "LiberationSans-Regular.ttf")


def default_font():
    from . import fonts
    for d in fonts.font_dirs():
        for name in DEFAULT_FONTS:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p.replace("\\", "/")
    return ""


# ---------------------------------------------------------------- node settings

def node_fonts(node):
    n = node.parm("fonts").evalAsInt() if node.parm("fonts") else 0
    refs = [node.evalParm("font%d" % (k + 1)) for k in range(n)]
    return [r for r in refs if r]


def node_charsets(node):
    return tuple(c for c in atlas.CHARSETS if node.parm("charset_" + c) and node.evalParm("charset_" + c))


def node_atlas_args(node):
    q = node.parm("quality")
    return dict(charsets=node_charsets(node), custom=node.evalParm("custom_chars") if node.parm("custom_chars") else "",
                px_per_em=int(q.evalAsString()) if q else 48)   # the menu token is the px count: no index list to go stale


def _set(node, name, value):
    p = node.parmTuple(name)
    if p is None:
        return
    if isinstance(value, (tuple, list)):
        p.set(tuple(value))
    else:
        p[0].set(value)


def apply_atlas(node, exr):
    """Point the node's hidden atlas inputs to a baked atlas."""
    m = atlas.load_metrics(exr)
    _set(node, "atlas", exr)
    _set(node, "atlas_sdf", atlas.sdf_path(exr))
    for k in ("cell", "baseline", "ascender", "descender", "range"):
        _set(node, k, float(m[k]))
    _set(node, "atlas_cols", float(m["cols"]))
    _set(node, "atlas_rows", float(m["rows"]))


# ---------------------------------------------------------------- callbacks

def _status(node, msg):
    try:
        node.setComment(msg)
        node.setGenericFlag(__import__("hou").nodeFlag.DisplayComment, bool(msg))
    except Exception:
        pass


def on_fonts(node):
    """Font list, charsets or quality changed: bake (or reuse) the atlas, then
    lay the text out again with the new metrics."""
    refs = node_fonts(node)
    if not refs:
        _status(node, "no font")
        return
    missing = [r for r in refs if not os.path.isfile(r.partition("#")[0])]
    if missing:
        _status(node, "font not found: " + ", ".join(missing))
        return
    try:
        import hou
        with hou.InterruptableOperation("Baking the text atlas", open_interrupt_dialog=True):
            exr = atlas.bake(refs, **node_atlas_args(node))
    except ImportError:
        exr = atlas.bake(refs, **node_atlas_args(node))
    except Exception as e:      # a font fontTools cannot read, a full disk...
        _status(node, "atlas: %s" % e)
        raise
    apply_atlas(node, exr)
    _status(node, "")
    on_text(node)


def text_table(rows):
    """Write (once) the table of one laid-out text, named after its content,
    in the cache; return its path and its (text_uv, text_lines) values."""
    import hashlib
    t = layout.Table()
    k = t.add(rows)
    path = os.path.join(atlas.cache_dir(), "texts",
                        "text_%s.exr" % hashlib.md5(rows.tobytes() + b"w%d" % layout.TEX_WIDTH).hexdigest()[:16]).replace("\\", "/")
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        t.write(path)
    return path, t.primvars(k)


def on_text(node):
    """Text or paragraph settings changed: lay the text out and point the node
    to its table (Text mode), or to the Layout SOP's table (Layout SOP mode)."""
    if node.parm("text_mode") and node.parm("text_mode").evalAsInt() == 1:
        return on_mode(node)
    exr = node.evalParm("atlas")
    refs = node_fonts(node)
    if (not exr or not os.path.isfile(atlas.metrics_path(exr))
            or atlas.load_metrics(exr).get("format") != atlas.FORMAT       # an older atlas
            or (refs and os.path.basename(exr).lower() != os.path.basename(   # not the atlas of these fonts and settings
                atlas.atlas_path(refs, **node_atlas_args(node))).lower())):     # (by name: the cache folder may move)
        return on_fonts(node) if refs else None
    fs = layout.FontSet.from_atlas(exr)
    warns = []
    rows = layout.text_rows(node.evalParm("text"), fs, font=node.evalParm("text_font"),
                            width=node.evalParm("text_width"),
                            align=ALIGNS[node.parm("text_align").evalAsInt()],
                            tracking=node.evalParm("text_tracking"), warn=warns.append)
    path, (uv, lines) = text_table(rows)
    p = node.parm("table")
    p.deleteAllKeyframes()
    p.set(path)
    _set(node, "uv", uv)
    _set(node, "lines", lines)
    _set(node, "textsource", 0)
    _status(node, "; ".join(warns))


def on_mode(node):
    """Layout SOP mode: the table follows the SOP's Table parameter."""
    if node.parm("text_mode").evalAsInt() == 0:
        return on_text(node)
    sop = node.evalParm("layout_sop")
    _set(node, "textsource", 1)
    p = node.parm("table")
    if sop:
        p.setExpression('chs("%s/table")' % sop.replace('"', ""), language=__import__("hou").exprLanguage.Hscript)
    _status(node, "" if sop else "Layout SOP mode: set the Layout SOP")


# ---------------------------------------------------------------- menus

def font_menu():
    """Menu items (ref, label, ...) of the installed fonts, cached."""
    from . import fonts
    try:
        import hou
        cache = os.path.join(hou.getenv("HOUDINI_TEMP_DIR") or os.path.expanduser("~"), "mtlx_text_fonts_2.json")
    except ImportError:
        cache = None
    items = []
    for ref, fam, sty in fonts.scan_fonts(cache):
        items += [ref, "%s %s" % (fam, sty)]
    return items


def text_font_menu(node):
    items = []
    for k, ref in enumerate(node_fonts(node)):
        items += [str(k), "%d: %s" % (k, os.path.basename(ref))]
    return items or ["0", "0"]
