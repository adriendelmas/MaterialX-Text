"""Multi-channel signed distance fields of glyph outlines (MTSDF), in numpy.

Follows msdfgen (Viktor Chlumsky, MIT licence): a contour is split into edges,
a corner is where the direction turns, edges are coloured so that the two
edges meeting at a corner share exactly one of the R, G, B channels, and each
channel holds the signed pseudo-distance to the closest edge of its colour.
The median of the three channels reproduces sharp corners at any
magnification; the A channel holds the true signed distance, for effects that
reach far from the outline.

Curves are flattened into line pieces for the distance evaluation; an edge's
line is extended (pseudo-distance) only beyond the edge's own two ends.
Distances are in the outline's units, positive inside (non-zero winding).
"""
import math

import numpy as np
from fontTools.pens.basePen import BasePen

RED, GREEN, BLUE = 1, 2, 4
YELLOW, MAGENTA, CYAN, WHITE = 3, 5, 6, 7

ANGLE_THRESHOLD = 3.0           # msdfgen's default: a turn sharper than ~8 degrees is a corner
PIECES = {2: 1, 3: 12, 4: 16}   # line pieces per line / quadratic / cubic edge
FAR_TEXELS = 3.0                # beyond this distance to the outline, every channel holds the true distance


class OutlinePen(BasePen):
    """Collects a glyph's contours as lists of edges; an edge is a list of
    control points: 2 = line, 3 = quadratic, 4 = cubic Bezier."""

    def __init__(self, glyphset=None):
        super().__init__(glyphset)
        self.contours = []
        self._cur = None
        self._start = self._last = None

    def _moveTo(self, p):
        self._flush()
        self._cur = []
        self._start = self._last = p

    def _lineTo(self, p):
        if p != self._last:
            self._cur.append([self._last, p])
        self._last = p

    def _qCurveToOne(self, p1, p2):
        self._cur.append([self._last, p1, p2])
        self._last = p2

    def _curveToOne(self, p1, p2, p3):
        self._cur.append([self._last, p1, p2, p3])
        self._last = p3

    def _closePath(self):
        if self._cur is not None and self._last != self._start:
            self._cur.append([self._last, self._start])
        self._flush()

    def _endPath(self):
        self._closePath()           # fonts have no open contours; close a stray one

    def _flush(self):
        if self._cur:
            self.contours.append(self._cur)
        self._cur = None


# ---------------------------------------------------------------- Bezier helpers

def _eval(e, t):
    """Points of edge e at parameters t (array) -> (len(t), 2)."""
    P = np.asarray(e, dtype=np.float64)
    t = np.asarray(t, dtype=np.float64)[:, None]
    s = 1.0 - t
    if len(P) == 2:
        return P[0] * s + P[1] * t
    if len(P) == 3:
        return P[0] * s * s + P[1] * 2 * s * t + P[2] * t * t
    return P[0] * s ** 3 + P[1] * 3 * s * s * t + P[2] * 3 * s * t * t + P[3] * t ** 3


def _direction(e, end):
    """Tangent at the start (end=False) or the end of edge e, skipping control
    points that coincide with the end point."""
    P = [np.asarray(p, dtype=np.float64) for p in e]
    if end:
        P = P[::-1]
    for q in P[1:]:
        d = q - P[0]
        if d @ d > 1e-18:
            return -d if end else d
    return np.zeros(2)


def _split(e, t):
    """de Casteljau split of edge e at t -> (left, right)."""
    pts = [np.asarray(p, dtype=np.float64) for p in e]
    left, right = [pts[0]], [pts[-1]]
    while len(pts) > 1:
        pts = [a * (1 - t) + b * t for a, b in zip(pts[:-1], pts[1:])]
        left.append(pts[0])
        right.append(pts[-1])
    return [tuple(p) for p in left], [tuple(p) for p in right[::-1]]


def _thirds(e):
    a, rest = _split(e, 1.0 / 3.0)
    b, c = _split(rest, 0.5)
    return [a, b, c]


def _unit(v):
    n = math.hypot(v[0], v[1])
    return v / n if n > 0 else v


def _is_corner(a, b, cross_threshold):
    a, b = _unit(a), _unit(b)
    return a @ b <= 0 or abs(a[0] * b[1] - a[1] * b[0]) > cross_threshold


# ---------------------------------------------------------------- edge colouring

def _switch_color(color, seed, banned=0):
    combined = color & banned
    if combined in (RED, GREEN, BLUE):
        return combined ^ WHITE, seed
    if color in (0, WHITE):
        return (CYAN, MAGENTA, YELLOW)[seed % 3], seed // 3
    shifted = color << (1 + (seed & 1))
    return (shifted | (shifted >> 3)) & WHITE, seed >> 1


def _trichotomy(position, n):
    return int(3 + 2.875 * position / (n - 1) - 1.4375 + 0.5) - 3


def color_edges(contours, seed=0):
    """msdfgen's edgeColoringSimple. Returns a list of contours, each a list of
    (edge, colour); teardrop contours with fewer than three edges are split."""
    cross_threshold = math.sin(ANGLE_THRESHOLD)
    color, seed = (CYAN, MAGENTA, YELLOW)[seed % 3], seed // 3
    out = []
    for edges in contours:
        if not edges:
            continue
        m = len(edges)
        corners = []
        prev = _direction(edges[-1], True)
        for i, e in enumerate(edges):
            if _is_corner(prev, _direction(e, False), cross_threshold):
                corners.append(i)
            prev = _direction(e, True)
        if not corners:
            color, seed = _switch_color(color, seed)
            out.append([(e, color) for e in edges])
        elif len(corners) == 1:
            color, seed = _switch_color(color, seed)
            colors = [color, WHITE, 0]
            color, seed = _switch_color(color, seed)
            colors[2] = color
            corner = corners[0]
            if m >= 3:
                res = [None] * m
                for i in range(m):
                    res[(corner + i) % m] = (edges[(corner + i) % m], colors[1 + _trichotomy(i, m)])
                out.append(res)
            else:
                parts = []
                for i in range(m):
                    parts += _thirds(edges[(corner + i) % m])
                k = len(parts)
                out.append([(p, colors[min(2, j * 3 // k)]) for j, p in enumerate(parts)])
        else:
            n = len(corners)
            spline = 0
            start = corners[0]
            color, seed = _switch_color(color, seed)
            initial = color
            res = [None] * m
            for i in range(m):
                index = (start + i) % m
                if spline + 1 < n and corners[spline + 1] == index:
                    spline += 1
                    color, seed = _switch_color(color, seed, initial if spline == n - 1 else 0)
                res[index] = (edges[index], color)
            out.append(res)
    return out


# ---------------------------------------------------------------- distances

def _pieces(colored):
    """Flatten coloured contours into line pieces.
    Returns a (N, 2), b (N, 2), colour (N,), start-of-edge (N,), end-of-edge (N,),
    edge index (N,)."""
    A, B, C, S, E, I = [], [], [], [], [], []
    eid = 0
    for contour in colored:
        for e, color in contour:
            k = PIECES[len(e)]
            pts = _eval(e, np.linspace(0.0, 1.0, k + 1))
            for j in range(k):
                A.append(pts[j])
                B.append(pts[j + 1])
                C.append(color)
                S.append(j == 0)
                E.append(j == k - 1)
                I.append(eid)
            eid += 1
    return (np.array(A), np.array(B), np.array(C, dtype=np.int32),
            np.array(S, dtype=bool), np.array(E, dtype=bool), np.array(I, dtype=np.int32))


def _median3(a, b, c):
    return np.maximum(np.minimum(a, b), np.minimum(np.maximum(a, b), c))


def mtsdf(contours, xs, ys, rng, chunk=4096):
    """MTSDF of an outline sampled at the grid xs (w,) x ys (h,).

    contours: list of contours, each a list of edges (point lists).
    Returns (h, w, 4) float32: RGB = multi-channel, A = true signed distance,
    all clamped to +-rng. An empty outline gives -rng everywhere.
    """
    h, w = len(ys), len(xs)
    out = np.full((h, w, 4), -rng, dtype=np.float32)
    colored = color_edges([c for c in contours if c])
    if not colored:
        return out
    a, b, col, is_s, is_e, _ = _pieces(colored)
    ab = b - a
    l2 = np.maximum((ab * ab).sum(1), 1e-30)
    ln = np.sqrt(l2)
    gx, gy = np.meshgrid(xs, ys)
    P = np.stack([gx.ravel(), gy.ravel()], 1)
    res = np.empty((len(P), 4), dtype=np.float64)
    for c0 in range(0, len(P), chunk):
        p = P[c0:c0 + chunk]
        apx = p[:, 0:1] - a[None, :, 0]
        apy = p[:, 1:2] - a[None, :, 1]
        t = (apx * ab[None, :, 0] + apy * ab[None, :, 1]) / l2[None]
        tc = np.clip(t, 0.0, 1.0)
        dx = apx - tc * ab[None, :, 0]
        dy = apy - tc * ab[None, :, 1]
        dist = np.sqrt(dx * dx + dy * dy)
        cross = ab[None, :, 0] * apy - ab[None, :, 1] * apx
        perp = np.abs(cross) / ln[None]
        # non-zero winding number of each point
        ay, by = a[None, :, 1], b[None, :, 1]
        py = p[:, 1:2]
        up = (ay <= py) & (by > py) & (cross > 0)
        down = (by <= py) & (ay > py) & (cross < 0)
        inside = (up.sum(1) - down.sum(1)) != 0
        dmin = dist.min(1)
        res[c0:c0 + chunk, 3] = np.where(inside, dmin, -dmin)
        # pseudo-distance: an edge's line continues beyond its own two ends only
        pseudo = dist.copy()
        ext = ((t < 0) & is_s[None]) | ((t > 1) & is_e[None])
        pseudo = np.where(ext, np.minimum(perp, dist), pseudo)
        side = np.sign(cross)
        side[side == 0] = 1.0
        # closest piece per channel by true distance; near-ties go to the
        # piece seen most squarely (largest distance to its line)
        key = dist - 1e-7 * perp
        for ch, bit in enumerate((RED, GREEN, BLUE)):
            k = np.where((col & bit)[None] != 0, key, np.inf)
            j = k.argmin(1)
            r = np.arange(len(p))
            res[c0:c0 + chunk, ch] = side[r, j] * pseudo[r, j]
            res[c0:c0 + chunk, ch][~np.isfinite(k[r, j])] = -np.inf
    sd = res[:, 3]
    # orientation: font contours wind either way; make the channels agree with
    # the true inside / outside for the majority of clearly placed samples
    med = _median3(res[:, 0], res[:, 1], res[:, 2])
    clear = np.abs(sd) > 1e-3 * max(1.0, np.abs(sd).max())
    agree = (np.sign(med[clear]) == np.sign(sd[clear])).sum()
    if agree * 2 < clear.sum():
        res[:, :3] *= -1.0
        med = -med
    # error correction 1: a median on the wrong side of the outline
    bad = np.sign(med) != np.sign(sd)
    res[bad, :3] = sd[bad, None]
    res = res.reshape(h, w, 4)
    # error correction 2: channel clashes that bilinear filtering would turn
    # into a wrong-side midpoint between two neighbouring texels
    texel = min(abs(xs[1] - xs[0]) if w > 1 else 1.0, abs(ys[1] - ys[0]) if h > 1 else 1.0)
    fix = np.zeros((h, w), dtype=bool)
    for sl_a, sl_b in (((slice(None), slice(0, -1)), (slice(None), slice(1, None))),
                       ((slice(0, -1), slice(None)), (slice(1, None), slice(None)))):
        A_, B_ = res[sl_a], res[sl_b]
        m = (A_[..., :3] + B_[..., :3]) * 0.5
        mm = _median3(m[..., 0], m[..., 1], m[..., 2])
        s = (A_[..., 3] + B_[..., 3]) * 0.5
        clash = (np.sign(mm) != np.sign(s)) & (np.abs(s) > 0.5 * texel)
        fix[sl_a] |= clash
        fix[sl_b] |= clash
    res[fix, :3] = res[fix, 3:4]
    # far from the outline the multi-channel values serve nothing (sharp
    # corners live at the edge) and carry pseudo-distance artefacts, spikes an
    # outline or a glow would show: the true distance there
    far = np.abs(res[..., 3]) > FAR_TEXELS * texel
    res[far, :3] = res[far, 3:4]
    np.clip(res, -rng, rng, out=res)
    out[:] = res.astype(np.float32)
    return out


def median(rgb):
    return _median3(rgb[..., 0], rgb[..., 1], rgb[..., 2])
