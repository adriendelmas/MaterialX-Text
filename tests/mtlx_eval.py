"""Reference evaluator of the MtlX Text nodegraph, in numpy.

Interprets materialx/libraries/mtlx_text/mtlx_text.mtlx over arrays of
texture coordinates, with the same node semantics as MaterialX's stdlib
(the subset the graph uses) and bilinear, clamped image lookups. It renders
the node's outputs without a renderer: for debugging the graph, and as the
expected values the renders are compared to.
"""
import os

import MaterialX as mx
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MTLX = os.path.join(os.path.dirname(HERE), "materialx", "libraries", "mtlx_text", "mtlx_text.mtlx")

_images = {}


def _image(path):
    if path not in _images:
        import OpenImageIO as oiio
        oiio.attribute("threads", 1)
        i = oiio.ImageInput.open(path)
        if i is None:
            raise IOError(oiio.geterror())
        a = i.read_image("float")
        i.close()
        _images[path] = np.asarray(a)[::-1].copy()        # row 0 = v 0 (bottom)
    return _images[path]


def _sample(path, uv, default, filtertype="linear"):
    img = _image(path)
    h, w, c = img.shape
    if filtertype == "closest":
        x = np.clip(np.floor(uv[..., 0] * w).astype(int), 0, w - 1)
        y = np.clip(np.floor(uv[..., 1] * h).astype(int), 0, h - 1)
        return img[y, x]
    x = np.clip(uv[..., 0] * w - 0.5, 0, w - 1)
    y = np.clip(uv[..., 1] * h - 0.5, 0, h - 1)
    x0 = np.minimum(np.floor(x).astype(int), w - 2)
    y0 = np.minimum(np.floor(y).astype(int), h - 2)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    a, b = img[y0, x0], img[y0, x0 + 1]
    c_, d = img[y0 + 1, x0], img[y0 + 1, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c_ * (1 - fx) + d * fx) * fy


def _parse(value, typ):
    if typ in ("float",):
        return float(value)
    if typ in ("integer",):
        return int(value)
    if typ in ("string", "filename"):
        return value
    return np.array([float(v) for v in value.split(",")])


def _rotl(x, k):
    return ((x << np.uint32(k)) | (x >> np.uint32(32 - k))).astype(np.uint32)


def cell_noise3(p):
    """MaterialX mx_cell_noise_float(vec3): Jenkins' lookup3 final mix of the
    floored integer coordinates, as genglsl / genosl implement it."""
    with np.errstate(over="ignore"):
        ix, iy, iz = (np.floor(p[..., k]).astype(np.int64).astype(np.uint32) for k in range(3))
        init = np.uint32(0xDEADBEEF + (3 << 2) + 13)
        a = (init + ix).astype(np.uint32)
        b = (init + iy).astype(np.uint32)
        c = (init + iz).astype(np.uint32)
        c ^= b; c = (c - _rotl(b, 14)).astype(np.uint32)
        a ^= c; a = (a - _rotl(c, 11)).astype(np.uint32)
        b ^= a; b = (b - _rotl(a, 25)).astype(np.uint32)
        c ^= b; c = (c - _rotl(b, 16)).astype(np.uint32)
        a ^= c; a = (a - _rotl(c, 4)).astype(np.uint32)
        b ^= a; b = (b - _rotl(a, 14)).astype(np.uint32)
        c ^= b; c = (c - _rotl(b, 24)).astype(np.uint32)
    return c.astype(np.float64) / float(0xFFFFFFFF)


class Evaluator:
    def __init__(self, interface, primvars=None, mtlx=MTLX):
        """interface: {input name: value}; primvars: {name: value}."""
        self.doc = mx.createDocument()
        mx.readFromXmlFile(self.doc, mtlx)
        self.nd = self.doc.getNodeDefs()[0]
        self.ng = self.doc.getNodeGraphs()[0]
        self.iface = {}
        for i in self.nd.getInputs():
            v = interface.get(i.getName())
            if v is None and i.getValueString():
                v = _parse(i.getValueString(), i.getType())
            self.iface[i.getName()] = v
        self.primvars = primvars or {}

    def run(self, uv, outputs=("mask",)):
        self.uv = uv
        self.iface["texcoord"] = uv
        self.cache = {}
        res = {}
        for o in self.ng.getOutputs():
            if o.getName() in outputs:
                res[o.getName()] = self._node_out(o.getNodeName(), o.getOutputString())
        return res

    def _input(self, node, name, default=None):
        i = node.getInput(name)
        if i is None:
            return default
        if i.getInterfaceName():
            v = self.iface[i.getInterfaceName()]
            if v is None and i.getInterfaceName() == "texcoord":
                return self.uv
            return v
        if i.getNodeName():
            return self._node_out(i.getNodeName(), i.getOutputString())
        return _parse(i.getValueString(), i.getType())

    def _node_out(self, name, output=""):
        key = (name, output or "out")
        if key in self.cache:
            return self.cache[key]
        node = self.ng.getNode(name)
        val = self._eval(node)
        if isinstance(val, dict):
            for k, v in val.items():
                self.cache[(name, k)] = v
            return val[output or "out"]
        self.cache[key] = val
        return val

    def _eval(self, n):
        c, t = n.getCategory(), n.getType()
        g = lambda k, d=None: self._input(n, k, d)        # noqa: E731
        if c in ("add", "subtract", "multiply", "divide"):
            x, y = np.asarray(g("in1"), dtype=float), np.asarray(g("in2"), dtype=float)
            if t != "float":            # vector op: a per-point float operand gets a channel axis
                if n.getInput("in1") is not None and n.getInput("in1").getType() == "float" and x.ndim:
                    x = x[..., None]
                if n.getInput("in2") is not None and n.getInput("in2").getType() == "float" and y.ndim:
                    y = y[..., None]
            return {"add": np.add, "subtract": np.subtract, "multiply": np.multiply,
                    "divide": np.divide}[c](x, y)
        if c == "floor":
            return np.floor(g("in"))
        if c == "max":
            return np.maximum(g("in1"), g("in2"))
        if c == "min":
            return np.minimum(g("in1"), g("in2"))
        if c == "clamp":
            return np.clip(g("in"), g("low", 0.0), g("high", 1.0))
        if c == "sin":
            return np.sin(g("in"))
        if c == "cos":
            return np.cos(g("in"))
        if c == "convert":
            return np.asarray(g("in"), dtype=float)
        if c == "magnitude":
            v = g("in")
            return np.sqrt((v * v).sum(-1))
        if c == "combine3":
            a, b, d = np.broadcast_arrays(np.asarray(g("in1", 0.0), dtype=float), np.asarray(g("in2", 0.0), dtype=float),
                                          np.asarray(g("in3", 0.0), dtype=float))
            return np.stack([a, b, d], -1)
        if c == "normalize":
            v = g("in")
            return v / np.maximum(np.sqrt((v * v).sum(-1, keepdims=True)), 1e-30)
        if c == "cellnoise3d":
            return cell_noise3(np.asarray(g("position"), dtype=float))
        if c == "combine2":
            a, b = np.broadcast_arrays(g("in1"), g("in2"))
            return np.stack([a, b], -1)
        if c in ("separate2", "separate3"):
            v = g("in")
            names = ("outx", "outy", "outz")[: 2 if c == "separate2" else 3]
            return {k: v[..., j] for j, k in enumerate(names)}
        if c == "ifgreater":
            cond = np.asarray(g("value1", 1.0) > g("value2", 0.0))
            a, b = g("in1", 0.0), g("in2", 0.0)
            if t != "float":
                cond = cond[..., None]
            return np.where(cond, a, b)
        if c == "ifequal":
            same = g("value1") == g("value2")
            return g("in1") if same else g("in2")
        if c == "switch":
            # MaterialX: in1 if which < 1, in2 if which < 2, ... in10 otherwise
            which = np.asarray(g("which", 0.0), dtype=float)
            ins = [np.asarray(g("in%d" % k, 0.0), dtype=float) for k in range(1, 11)]
            res = ins[9]
            for k in range(9, 0, -1):
                cond = which < k
                if t != "float":
                    cond = cond[..., None]
                res = np.where(cond, ins[k - 1], res)
            return res
        if c == "geompropvalue":
            v = self.primvars.get(g("geomprop"))
            return np.asarray(v, dtype=float) if v is not None else g("default")
        if c == "image":
            path = g("file")
            s = _sample(path, g("texcoord"), g("default"), g("filtertype", "linear"))
            return s[..., 0] if t == "float" else s[..., :3]
        if c == "smoothstep":
            lo, hi, x = g("low", 0.0), g("high", 1.0), g("in")
            span = np.where(np.asarray(hi - lo) == 0, 1e-12, hi - lo)
            tt = np.clip((x - lo) / span, 0, 1)
            step = np.where(np.asarray(hi - lo) == 0, (x >= lo).astype(float), tt * tt * (3 - 2 * tt))
            return step
        if c == "mix":
            m = np.asarray(g("mix"))[..., None]
            return g("fg") * m + g("bg") * (1 - m)
        raise NotImplementedError(c)


def node_interface(node):
    """Interface values of a mtlxtextsdf HDA node, by nodedef input name."""
    vals = {}
    for pt in node.parmTuples():
        vals[pt.name()] = pt.eval() if len(pt) == 1 else np.array(pt.eval(), dtype=float)
    out = {}
    for k, v in vals.items():
        if isinstance(v, tuple) and len(v) == 1:
            v = v[0]
        out[k] = v
    return out
