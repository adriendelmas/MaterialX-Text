"""Build the MtlX Text node: hython build.py

1. Writes materialx/libraries/mtlx_text/mtlx_text.mtlx: the nodedef and its
   nodegraph, standard MaterialX nodes only (plus Karma's kma_stderiv, declared
   in mtlx_text_karma.mtlx), generated here through the MaterialX API.
2. Runs SideFX's mtlx2karma.py -> karmaNodeGraphs.json (Karma CPU/XPU load it
   from HOUDINI_PATH).
3. Runs SideFX's mtlx2hda.py -> otls/mtlx_text.hda, then lays out the HDA's
   interface (python3.13libs/mtlx_text/hda.py).

See docs/METHOD.md for what the graph computes and docs/PLAN.md for why.
"""
import math
import os
import subprocess
import sys

import MaterialX as mx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "python3.13libs"))
from mtlx_text import layout as L  # noqa: E402

NODE = "textsdf"
NODEDEF = "ND_textsdf_v2"
VERSION = "2.0"
LIB_DIR = os.path.join(HERE, "materialx", "libraries", "mtlx_text")
MTLX_FILE = os.path.join(LIB_DIR, "mtlx_text.mtlx")
JSON_FILE = os.path.join(HERE, "karmaNodeGraphs.json")
HDA_FILE = os.path.join(HERE, "otls", "mtlx_text.hda")
HFS = os.environ["HFS"]
HYTHON = os.path.join(HFS, "bin", "hython.exe" if os.name == "nt" else "hython")
PYLIBS = os.path.join(HFS, "houdini", "python3.13libs")
FAR = 1.0e6


class Graph:
    """Small builder over a MaterialX nodegraph: one call per node, unique
    names, literals and interface inputs as plain arguments."""

    def __init__(self, doc, nd, ng):
        self.doc, self.nd, self.ng = doc, nd, ng
        self.itypes = {}
        self.count = {}

    # -- interface
    def iface(self, name, typ, value=None, **attrs):
        i = self.nd.addInput(name, typ)
        if value is not None:
            i.setValueString(value)
        for k, v in attrs.items():
            i.setAttribute(k, str(v))
        self.itypes[name] = typ
        return ("I", name)

    def output(self, name, typ, src):
        self.nd.addOutput(name, typ)
        o = self.ng.addOutput(name, typ)
        node, out = (src[1], src[2]) if isinstance(src, tuple) and src[0] == "O" else (src, None)
        o.setNodeName(node.getName())
        if out:
            o.setOutputString(out)

    # -- nodes
    def n(self, cat, typ, prefix=None, **inputs):
        prefix = prefix or cat
        k = self.count.get(prefix, 0)
        self.count[prefix] = k + 1
        node = self.ng.addNode(cat, "%s_%d" % (prefix, k), typ)
        for name, src in inputs.items():
            self._connect(node, name, src)
        return node

    def _connect(self, node, name, src):
        if isinstance(src, tuple) and src[0] == "I":
            node.addInput(name, self.itypes[src[1]]).setInterfaceName(src[1])
        elif isinstance(src, tuple) and src[0] == "O":
            up, out = src[1], src[2]
            i = node.addInput(name, src[3] if len(src) > 3 else "float")
            i.setNodeName(up.getName())
            i.setOutputString(out)
        elif isinstance(src, tuple) and src[0] == "V":
            node.addInput(name, src[2]).setValueString(src[1])
        elif isinstance(src, (int, float)):
            node.addInput(name, "float").setValueString(repr(float(src)))
        else:
            node.addInput(name, src.getType()).setNodeName(src.getName())

    @staticmethod
    def out(node, name, typ="float"):
        return ("O", node, name, typ)

    @staticmethod
    def lit(value, typ):
        return ("V", value, typ)

    # -- float helpers
    def add(self, a, b):
        return self.n("add", "float", in1=a, in2=b)

    def sub(self, a, b):
        return self.n("subtract", "float", in1=a, in2=b)

    def mul(self, a, b):
        return self.n("multiply", "float", in1=a, in2=b)

    def div(self, a, b):
        return self.n("divide", "float", in1=a, in2=b)

    def floor(self, a):
        return self.n("floor", "float", **{"in": a})

    def max(self, a, b):
        return self.n("max", "float", in1=a, in2=b)

    def min(self, a, b):
        return self.n("min", "float", in1=a, in2=b)

    def clamp01(self, a):
        return self.n("clamp", "float", **{"in": a})

    def ifgt(self, a, b, then, other, typ="float"):
        """then if a > b else other"""
        return self.n("ifgreater", typ, value1=a, value2=b, in1=then, in2=other)

    def gt(self, a, b):
        return self.ifgt(a, b, 1.0, 0.0)

    def and_(self, *xs):
        r = xs[0]
        for x in xs[1:]:
            r = self.mul(r, x)
        return r

    def switch(self, srcs, which, typ="float"):
        """srcs[floor(which)], first below 0, last past the end."""
        if SWITCH_TREE:
            return self._tree(srcs, which, typ, 0)
        if len(srcs) <= 10:
            return self.n("switch", typ, which=which, **{"in%d" % (k + 1): s for k, s in enumerate(srcs)})
        groups = []
        for g0 in range(0, len(srcs), 10):
            w = which if g0 == 0 else self.sub(which, float(g0))
            groups.append(self.switch(srcs[g0:g0 + 10], w, typ))
        return self.switch(groups, self.floor(self.div(which, 10.0)), typ)

    def _tree(self, srcs, which, typ, base):
        """The same selection as a balanced tree of ifgreater."""
        if len(srcs) == 1:
            return srcs[0]
        mid = len(srcs) // 2
        return self.ifgt(which, base + mid - 0.5, self._tree(srcs[mid:], which, typ, base + mid),
                         self._tree(srcs[:mid], which, typ, base), typ)

    def median3(self, a, b, c):
        return self.max(self.min(a, b), self.min(self.max(a, b), c))


DEBUG = os.environ.get("MTLX_TEXT_DEBUG") == "1"
SWITCH_TREE = os.environ.get("MTLX_TEXT_SWITCH_TREE") == "1"
# optional parts of the graph (all by default); a part left out keeps its outputs, at 0
FEATURES = set(os.environ.get("MTLX_TEXT_FEATURES", "aa,jitter,shadow").split(","))


def build_graph():
    doc = mx.createDocument()
    nd = doc.addNodeDef(NODEDEF, "multioutput", NODE)
    nd.setNodeGroup("texture2d")
    nd.setVersionString(VERSION)
    nd.setDefaultVersion(True)
    nd.setDocString("Text drawn in the shader from a multi-channel signed distance field "
                    "glyph atlas; the text and its per-text settings come from a text table")
    ng = doc.addNodeGraph("NG_textsdf_v2")
    ng.setNodeDef(nd)
    G = Graph(doc, nd, ng)

    # ------------------------------------------------------------ interface
    def P(name, typ, value, **attrs):
        """An input a text may set for itself: in Primvars mode, its value in
        the style texels of the text's first table row (layout.STYLE), which
        the Layout LOP / SOP fills from primvars text_<name> or the node."""
        ref = G.iface(name, typ, value, **attrs)
        n = {"float": 1, "vector2": 2, "color3": 3}[typ]
        k0 = L.style_offset(name)
        assert dict(L.STYLE)[name] == tuple(float(x) for x in value.split(",")), name   # same default
        comps = []
        for k in range(k0, k0 + n):
            t, c = divmod(k, 3)
            if t not in style:
                srow, col = divmod(L.BIN_TEXELS + t, L.TEX_WIDTH)
                uv = G.n("combine2", "vector2", in1=G.add(t_u, (col + 0.5) / L.TEX_WIDTH),
                         in2=G.add(t_v, G.mul(sub_dv, float(srow))))
                tex = G.n("image", "vector3", "style", file=table, texcoord=uv,
                          uaddressmode=G.lit("clamp", "string"), vaddressmode=G.lit("clamp", "string"),
                          filtertype=G.lit("closest", "string"))
                style[t] = G.n("separate3", "multioutput", **{"in": tex})
            comps.append(G.out(style[t], ("outx", "outy", "outz")[c]))
        v = comps[0] if n == 1 else G.n("combine%d" % n, typ, **{"in%d" % (j + 1): x for j, x in enumerate(comps)})
        return G.n("ifequal", typ, value1=source, value2=G.lit("1", "integer"), in1=v, in2=ref)

    texcoord = G.iface("texcoord", "vector2", uiname="Texture Coordinates", defaultgeomprop="UV0")
    atlas = G.iface("atlas", "filename", "", uiname="Atlas", uniform="true")
    atlas_sdf = G.iface("atlas_sdf", "filename", "", uiname="Atlas Distance", uniform="true")
    a_cols = G.iface("atlas_cols", "float", "1", uiname="Atlas Columns", uniform="true")
    a_rows = G.iface("atlas_rows", "float", "1", uiname="Atlas Rows", uniform="true")
    cell = G.iface("cell", "float", "1.75", uiname="Cell (em)", uniform="true")
    baseline = G.iface("baseline", "float", "0.5", uiname="Baseline (em)", uniform="true")
    ascender = G.iface("ascender", "float", "0.8", uiname="Ascender (em)", uniform="true")
    descender = G.iface("descender", "float", "-0.2", uiname="Descender (em)", uniform="true")
    rng = G.iface("range", "float", "0.25", uiname="Distance Range (em)", uniform="true")
    table = G.iface("table", "filename", "", uiname="Text Table", uniform="true")
    # where the text is in the table: the node's own values (its Text parameter),
    # or the primvars of a Layout SOP; only the latter mode reads the primvars,
    # which a primitive may carry for another material
    source = G.iface("textsource", "integer", "0", uiname="Text From", uniform="true",
                     enum="Node,Primvars", enumvalues="0,1")

    def S(name, typ, value, **attrs):
        ref = G.iface(name, typ, value, uniform="true", **attrs)
        pv = G.n("geompropvalue", typ, "pv_" + name, geomprop=G.lit("text_" + name, "string"), default=ref)
        return G.n("ifequal", typ, value1=source, value2=G.lit("1", "integer"), in1=pv, in2=ref)
    table_uv = S("uv", "vector2", "0, 0.5", uiname="Table Row (u, v of line 0)")
    table_lines = S("lines", "vector2", "1, 0", uiname="Table Lines (v step, count)")
    # the table position, needed by the style reads below
    tuv = G.n("separate2", "multioutput", **{"in": table_uv})
    t_u, t_v = G.out(tuv, "outx"), G.out(tuv, "outy")
    tl = G.n("separate2", "multioutput", **{"in": table_lines})
    dv, n_lines = G.out(tl, "outx"), G.out(tl, "outy")
    sub_dv = G.div(dv, float(L.SUB_ROWS))       # one texture row (a line of text spans SUB_ROWS)
    style = {}           # style texel -> its separate3
    # placement
    origin = P("origin", "vector2", "0.05, 0.9", uiname="Origin")
    size = P("size", "float", "0.1", uiname="Size (em in UV)", uisoftmin="0.001", uisoftmax="1")
    aspect = P("aspect", "float", "1", uiname="Aspect", uisoftmin="0.1", uisoftmax="10")
    rotate = P("rotate", "float", "0", uiname="Rotate (degrees)", uisoftmin="-180", uisoftmax="180")
    slant = P("slant", "float", "0", uiname="Slant", uisoftmin="-0.5", uisoftmax="0.5")
    leading = P("leading", "float", "1.2", uiname="Leading (em)", uisoftmin="0.5", uisoftmax="3")
    valign = G.iface("valign", "integer", "0", uiname="Vertical Anchor", enum="Top,Middle,Bottom",
                     enumvalues="0,1,2")
    # look
    weight = P("weight", "float", "0", uiname="Weight (em)", uisoftmin="-0.1", uisoftmax="0.1")
    softness = P("softness", "float", "0", uiname="Edge Softness (em)", uisoftmin="0", uisoftmax="0.1")
    auto_aa = P("auto_aa", "float", "1", uiname="Pixel Antialiasing", uisoftmin="0", uisoftmax="2")
    fg = P("fg", "color3", "1, 1, 1", uiname="Text Color")
    bg = P("bg", "color3", "0, 0, 0", uiname="Background Color")
    # per-letter variation
    seed = P("seed", "float", "0", uiname="Seed")
    j_base = P("jitter_baseline", "float", "0", uiname="Baseline Jitter (em)", uisoftmin="0", uisoftmax="0.2")
    j_rot = P("jitter_rotate", "float", "0", uiname="Rotate Jitter (degrees)", uisoftmin="0", uisoftmax="30")
    j_scale = P("jitter_scale", "float", "0", uiname="Scale Jitter", uisoftmin="0", uisoftmax="0.5")
    drop = P("drop", "float", "0", uiname="Missing Letters", uimin="0", uimax="1")
    # effects
    corner = G.iface("corners", "integer", "0", uiname="Effect Corners", enum="Sharp,Round", enumvalues="0,1")
    o_width = P("outline_width", "float", "0.03", uiname="Outline Width (em)", uisoftmin="0", uisoftmax="0.2")
    o_offset = P("outline_offset", "float", "0", uiname="Outline Offset (em)", uisoftmin="-0.1", uisoftmax="0.1")
    s_offset = P("shadow_offset", "vector2", "0.04, 0.04", uiname="Shadow Offset (em)")
    s_soft = P("shadow_softness", "float", "0.03", uiname="Shadow Softness (em)", uisoftmin="0", uisoftmax="0.2")
    b_width = P("bevel_width", "float", "0.04", uiname="Bevel Width (em)", uisoftmin="0.001", uisoftmax="0.2")
    neg = G.mul(rng, -1.0)

    # ------------------------------------------------------------ text table
    def fetch(k, v):
        """Texel k of the line of text whose first texture row is at v: (r, g, b),
        exact (closest, no mip levels); texture row k // TEX_WIDTH of that line."""
        srow = G.floor(G.div(G.add(k, 0.5), float(L.TEX_WIDTH)))
        col = G.sub(k, G.mul(srow, float(L.TEX_WIDTH)))
        uv = G.n("combine2", "vector2", in1=G.add(t_u, G.div(G.add(col, 0.5), float(L.TEX_WIDTH))),
                 in2=G.add(v, G.mul(srow, sub_dv)))
        tex = G.n("image", "vector3", "table", file=table, texcoord=uv, uaddressmode=G.lit("clamp", "string"),
                  vaddressmode=G.lit("clamp", "string"), filtertype=G.lit("closest", "string"))
        s = G.n("separate3", "multioutput", **{"in": tex})
        return G.out(s, "outx"), G.out(s, "outy"), G.out(s, "outz")

    # ------------------------------------------------------------ point in em, y down
    p = G.n("divide", "vector2", in1=G.n("subtract", "vector2", in1=texcoord, in2=origin), in2=size)
    ps = G.n("separate2", "multioutput", **{"in": p})
    px0, py0 = G.out(ps, "outx"), G.div(G.out(ps, "outy"), aspect)
    th = G.mul(rotate, math.pi / 180.0)
    cs, sn = G.n("cos", "float", **{"in": th}), G.n("sin", "float", **{"in": th})
    x = G.add(G.mul(cs, px0), G.mul(sn, py0))                 # rotate by -angle
    yup = G.sub(G.mul(cs, py0), G.mul(sn, px0))
    vfac = G.mul(G.n("convert", "float", **{"in": valign}), 0.5)
    block_h = G.add(G.sub(ascender, descender), G.mul(G.sub(n_lines, 1.0), leading))
    y = G.add(G.mul(yup, -1.0), G.mul(block_h, vfac))       # y down from the block top
    pivot = G.mul(G.add(ascender, descender), 0.5)          # glyph rotation / scale centre above the baseline

    # on-screen size of the point in em (Karma: ST Derivatives; elsewhere 0)
    if "aa" in FEATURES:
        der = G.n("kma_stderiv", "multioutput", "deriv",
                  input=G.n("combine2", "vector2", in1=px0, in2=py0))
        fp = G.mul(G.max(G.n("magnitude", "float", **{"in": G.out(der, "derivS", "vector2")}),
                         G.n("magnitude", "float", **{"in": G.out(der, "derivT", "vector2")})),
                   G.out(der, "derivScale"))
    else:
        fp = G.mul(auto_aa, 0.0)

    # rows whose cell centres bracket y; cell of row r: baseline b_r = asc + r * leading
    c0 = G.add(G.sub(ascender, G.mul(cell, 0.5)), baseline)
    r0 = G.floor(G.div(G.sub(y, c0), leading))
    # even row first, odd row second: a row keeps its place when the bracket
    # moves, so its glyphs' atlas uv does not jump (Karma XPU seams)
    par = G.sub(r0, G.mul(G.floor(G.div(r0, 2.0)), 2.0))
    rows = [G.add(r0, par), G.sub(G.add(r0, 1.0), par)]

    def rand(i, k):
        """Random in [0, 1) per glyph index i and stream k, from the seed."""
        return G.n("cellnoise3d", "float", position=G.n("combine3", "vector3", in1=G.add(i, 0.5),
                                                        in2=G.add(seed, 0.5), in3=float(k) + 0.5))

    def make(g, i, ok, rr, b_r, xc):
        """A glyph to evaluate, from a table entry (atlas glyph g, index i in
        the text, x centre), with its per-letter transform."""
        c = {"ok": ok, "g": g, "i": i, "xc": xc, "row": rr, "b": b_r,
             "jy": 0.0, "cos": 1.0, "sin": 0.0, "scl": 1.0}
        if "jitter" in FEATURES:
            # per-letter variation: baseline shift, rotation, scale, dropped letters
            ang = G.mul(G.mul(j_rot, G.sub(G.mul(rand(i, 1), 2.0), 1.0)), math.pi / 180.0)
            c.update(jy=G.mul(j_base, G.sub(G.mul(rand(i, 0), 2.0), 1.0)),
                     cos=G.n("cos", "float", **{"in": ang}), sin=G.n("sin", "float", **{"in": ang}),
                     scl=G.add(1.0, G.mul(j_scale, G.sub(G.mul(rand(i, 2), 2.0), 1.0))),
                     ok=G.mul(ok, G.ifgt(drop, rand(i, 3), 0.0, 1.0)))
        return c

    # ------------------------------------------------------------ per candidate
    def local(c, px, py):
        """Glyph-cell coordinates (u, v unclamped) of the point (px, py: em, y
        down) for candidate c, through slant and its random transform."""
        dx = G.sub(px, c["xc"])
        dyu = G.add(G.sub(c["b"], py), c["jy"])          # height above the (shifted) baseline
        dx = G.sub(dx, G.mul(slant, dyu))
        ry = G.sub(dyu, pivot)
        lx = G.div(G.add(G.mul(c["cos"], dx), G.mul(c["sin"], ry)), c["scl"])
        ly = G.div(G.sub(G.mul(c["cos"], ry), G.mul(c["sin"], dx)), c["scl"])
        return G.add(G.div(lx, cell), 0.5), G.div(G.add(G.add(ly, pivot), baseline), cell)

    def atlas_uv(c, u, v):
        gy = G.floor(G.div(c["g"], a_cols))
        gx = G.sub(c["g"], G.mul(gy, a_cols))
        return G.n("combine2", "vector2", in1=G.div(G.add(gx, u), a_cols), in2=G.div(G.add(gy, v), a_rows))

    def lookup(c, u_raw, v_raw, true_distance=False):
        """Signed distance in em of candidate c at cell coordinates (u, v),
        continued outside the cell, scaled back by the glyph's scale."""
        u, v = G.clamp01(u_raw), G.clamp01(v_raw)
        outside = G.mul(G.n("magnitude", "float", **{"in": G.n(
            "combine2", "vector2", in1=G.sub(u_raw, u), in2=G.sub(v_raw, v))}), cell)
        uv = atlas_uv(c, u, v)
        if true_distance:
            d = G.n("image", "float", file=atlas_sdf, texcoord=uv, uaddressmode=G.lit("clamp", "string"),
                    vaddressmode=G.lit("clamp", "string"), filtertype=G.lit("linear", "string"))
        else:
            tex = G.n("image", "vector3", file=atlas, texcoord=uv, uaddressmode=G.lit("clamp", "string"),
                      vaddressmode=G.lit("clamp", "string"), filtertype=G.lit("linear", "string"))
            ts = G.n("separate3", "multioutput", **{"in": tex})
            d = G.median3(G.out(ts, "outx"), G.out(ts, "outy"), G.out(ts, "outz"))
        d = G.mul(G.sub(d, outside), c["scl"])
        return G.ifgt(c["ok"], 0.5, G.max(d, neg), neg), u, v

    def fold(best, d, fields):
        """Running union: keep the largest distance and the fields of its glyph."""
        if best is None:
            return dict(fields, d=d)
        res = {"d": G.max(best["d"], d)}
        for k, v in fields.items():
            res[k] = G.ifgt(d, best["d"], v, best[k])
        return res

    # per bracketing line: which row, its baseline, its two candidates at a point
    rowinfo = []
    for r in rows:
        rvalid = G.and_(G.gt(r, -0.5), G.gt(n_lines, r))
        rr = G.ifgt(rvalid, 0.5, r, 0.0)
        rowinfo.append({"r": rr, "ok": rvalid, "b": G.add(ascender, G.mul(rr, leading)),
                        "v": G.add(t_v, G.mul(rr, dv))})

    def bin_of(px):
        return G.n("clamp", "float", **{"in": G.floor(G.div(G.sub(px, L.XMIN), L.BIN)),
                                        "low": 0.0, "high": float(L.BINS - 1)})

    def candidates(px):
        """The glyphs of both lines whose ink reaches the bin of px: two per line,
        one table texel each (no search)."""
        b = bin_of(px)
        centre = G.add(G.mul(G.add(b, 0.5), L.BIN), L.XMIN)
        out = []
        for k, ri in enumerate(rowinfo):
            for slot in range(L.SLOTS):
                dx, g, i = fetch(G.add(G.mul(b, float(L.TEXELS)), float(slot)), ri["v"])
                ok = G.and_(ri["ok"], G.gt(g, -0.5))
                c = make(G.ifgt(ok, 0.5, g, 0.0), i, ok, ri["r"], ri["b"], G.add(centre, dx))
                c["k"], c["slot"] = float(k), float(slot)
                out.append(c)
        return out

    # every lookup reads a candidate straight from the table: a lookup of the
    # winner, chosen from lookups, is a chain that Karma XPU compiles in minutes
    best = None
    t_union = {}        # union of the candidates' true distances
    for c in candidates(x):
        u_raw, v_raw = local(c, x, y)
        best = fold(best, lookup(c, u_raw, v_raw)[0],
                    {"g": c["g"], "i": c["i"], "xc": c["xc"], "k": c["k"], "slot": c["slot"], "ok": c["ok"]})
        td = lookup(c, u_raw, v_raw, true_distance=True)[0]
        t_union["p"] = td if "p" not in t_union else G.max(t_union["p"], td)

    # the winner, rebuilt from its fields
    won = G.gt(best["ok"], 0.5)
    w_rr = G.ifgt(best["k"], 0.5, rowinfo[1]["r"], rowinfo[0]["r"])
    w_b = G.ifgt(best["k"], 0.5, rowinfo[1]["b"], rowinfo[0]["b"])
    win = make(best["g"], best["i"], best["ok"], w_rr, w_b, best["xc"])
    win["d"] = best["d"]
    win["u_raw"], win["v_raw"] = local(win, x, y)
    win["u"], win["v"] = G.clamp01(win["u_raw"]), G.clamp01(win["v_raw"])
    wid_i = G.ifgt(won, 0.5, win["i"], -1.0)
    # the winner's word: the third texel of its bin, on its line (one lookup)
    w_v = G.ifgt(best["k"], 0.5, rowinfo[1]["v"], rowinfo[0]["v"])
    words = []
    for t in range(L.WORD_TEXELS):
        words += fetch(G.add(G.mul(bin_of(x), float(L.TEXELS)), float(L.SLOTS + t)), w_v)
    word = words[0]
    for slot in range(1, L.SLOTS):
        word = G.ifgt(best["slot"], slot - 0.5, words[slot], word)
    word = G.ifgt(won, 0.5, word, -1.0)

    def union_at(px, py):
        """Signed distance of the union of the glyphs around (px, py)."""
        best = None
        for c in candidates(px):
            u_raw, v_raw = local(c, px, py)
            best = fold(best, lookup(c, u_raw, v_raw)[0], {})
        return best["d"]

    sdf = G.add(win["d"], weight)
    # edge half width: softness plus half a pixel; never 0, Karma's smoothstep
    # returns 0 when low == high
    hw = G.max(G.add(softness, G.mul(G.mul(auto_aa, fp), 0.5)), 1.0e-4)

    def step(d, w):
        return G.n("smoothstep", "float", **{"in": d, "low": G.mul(w, -1.0), "high": w})

    mask = step(sdf, hw)
    color = G.n("mix", "color3", fg=fg, bg=bg, mix=mask)

    # true distance of the glyphs' union, for effects with round corners
    sdf_round = G.add(t_union["p"], weight)
    sdf_fx = G.n("ifequal", "float", value1=corner, value2=G.lit("1", "integer"), in1=sdf_round, in2=sdf)

    # outline: a band of outline_width centred outline_offset outside the edge
    o_half = G.mul(o_width, 0.5)
    o_in = G.add(sdf_fx, o_offset)
    outline = G.clamp01(G.sub(step(G.add(o_in, o_half), hw), step(G.sub(o_in, o_half), hw)))

    # shadow: the union again, at the point moved back by the offset
    if "shadow" in FEATURES:
        so = G.n("separate2", "multioutput", **{"in": s_offset})
        sx, sy = G.sub(x, G.out(so, "outx")), G.sub(y, G.out(so, "outy"))
        s_sdf = G.add(union_at(sx, sy), weight)
        shadow = step(s_sdf, G.max(G.add(s_soft, G.mul(G.mul(auto_aa, fp), 0.5)), 1.0e-4))
    else:
        shadow = G.mul(s_soft, 0.0)

    # bevel: height profile from the edge inwards (a normal from it: heighttonormal)
    b_h = G.clamp01(G.div(sdf_fx, b_width))

    G.output("mask", "float", mask)
    G.output("color", "color3", color)
    G.output("sdf", "float", sdf)
    G.output("sdf_round", "float", sdf_round)
    G.output("character_id", "float", wid_i)
    G.output("glyph_index", "float", G.ifgt(won, 0.5, win["g"], -1.0))
    G.output("line_id", "float", G.ifgt(won, 0.5, w_rr, -1.0))
    G.output("word_id", "float", word)
    G.output("glyph_uv", "vector2", G.n("combine2", "vector2", in1=win["u"], in2=win["v"]))
    G.output("random", "float", G.ifgt(won, 0.5, rand(win["i"], 4), 0.0))
    G.output("outline", "float", outline)
    G.output("shadow", "float", shadow)
    G.output("bevel_height", "float", b_h)
    G.output("footprint", "float", fp)
    return doc, len(ng.getNodes())


def write_mtlx():
    stdlib = mx.createDocument()
    mx.loadLibraries(["libraries/stdlib"], mx.FileSearchPath(os.path.join(HFS, "houdini", "materialx")), stdlib)
    doc, count = build_graph()
    vdoc = doc.copy()
    vdoc.importLibrary(stdlib)          # validate against the standard library, write without it
    karma = mx.createDocument()
    mx.readFromXmlFile(karma, os.path.join(LIB_DIR, "mtlx_text_karma.mtlx"))
    vdoc.importLibrary(karma)
    ok, msg = vdoc.validate()
    if not ok:
        raise RuntimeError("invalid MaterialX document:\n" + msg)
    os.makedirs(LIB_DIR, exist_ok=True)
    mx.writeToXmlFile(doc, MTLX_FILE)
    print("wrote", MTLX_FILE, "(%d nodes)" % count)


def run(script, *args):
    cmd = [HYTHON, os.path.join(PYLIBS, script)] + list(args)
    print(" ".join(cmd))
    env = dict(os.environ)
    env["PATH"] = os.path.join(HFS, "bin") + os.pathsep + env.get("PATH", "")   # mtlx2hda calls hotl
    subprocess.check_call(cmd, env=env)


if __name__ == "__main__":
    write_mtlx()
    libs = os.path.join(HFS, "houdini", "materialx") + ";" + os.path.join(HERE, "materialx")
    run("mtlx2karma.py", "-o", JSON_FILE, "-x", libs, "-p", LIB_DIR, "-i", "1")
    os.makedirs(os.path.dirname(HDA_FILE), exist_ok=True)
    if os.path.isfile(HDA_FILE):
        os.remove(HDA_FILE)          # generated file: rebuilt from scratch every time
    # the node as one shader of the nodedef: a value change costs ~7 ms (as a
    # subnet of SideFX's VOPs it re-translated 1100 nodes to USD, ~1 s; dropped
    # 2026-10-14); Karma reads karmaNodeGraphs.json, hdCycles the library (0137)
    import hou
    try:     # loaded at startup through the package: its section index would go stale
        hou.hda.uninstallFile(HDA_FILE)
    except hou.OperationFailed:
        pass
    run("mtlx2hda.py", "-l", "-o", HDA_FILE, "-x", libs, NODE)
    from mtlx_text import hda
    hda.edit(HDA_FILE)
    from mtlx_text import sop_hda
    sop_hda.build(os.path.join(HERE, "otls", "mtlx_text_layout.hda"))
    from mtlx_text import lop_hda
    lop_hda.build(os.path.join(HERE, "otls", "mtlx_text_layout_lop.hda"))
