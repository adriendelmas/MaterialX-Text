"""Font access for MtlX Text: outlines, metrics, kerning, installed fonts.

A font is named by a *font reference*: a file path, or ``path#N`` for face N
of a collection (.ttc / .otc). Everything is returned in em (units per em).
"""
import importlib.util
import json
import os
import sys

_HEAD = "fontTools.ttLib.tables._h_e_a_d"


def _fix_fonttools():
    """Houdini 22.0 ships fontTools 4.55.4 without its 'head' table module;
    every font load needs it. Register the vendored copy when it is missing."""
    try:
        importlib.import_module(_HEAD)
    except ImportError:
        path = os.path.join(os.path.dirname(__file__), "_fonttools_head.py")
        spec = importlib.util.spec_from_file_location(_HEAD, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_HEAD] = mod
        spec.loader.exec_module(mod)
        import fontTools.ttLib.tables as tables
        tables._h_e_a_d = mod                   # ttFont.getTableModule reads the attribute


_fix_fonttools()

from fontTools.ttLib import TTFont  # noqa: E402

from . import msdf  # noqa: E402


def split_ref(ref):
    path, _, n = ref.partition("#")
    return path, int(n) if n else 0


def open_font(ref):
    path, n = split_ref(ref)
    return TTFont(path, fontNumber=n, lazy=True)


class Font:
    """One face: cmap, advances, outlines and kerning in em."""

    def __init__(self, ref):
        self.ref = ref
        self.tt = open_font(ref)
        self.upm = float(self.tt["head"].unitsPerEm)
        self.cmap = self.tt.getBestCmap() or {}
        self.glyphset = self.tt.getGlyphSet()
        hhea = self.tt["hhea"]
        os2 = self.tt["OS/2"] if "OS/2" in self.tt else None
        if os2 is not None and getattr(os2, "sTypoAscender", 0):
            self.ascender = os2.sTypoAscender / self.upm
            self.descender = os2.sTypoDescender / self.upm      # negative
        else:
            self.ascender = hhea.ascent / self.upm
            self.descender = hhea.descent / self.upm
        self.family, self.style = names(self.tt)

    def has(self, cp):
        return cp in self.cmap

    def advance(self, cp):
        return self.glyphset[self.cmap[cp]].width / self.upm

    def contours(self, cp):
        """Outline of a code point: contours of edges, in em."""
        pen = msdf.OutlinePen(self.glyphset)
        self.glyphset[self.cmap[cp]].draw(pen)
        s = 1.0 / self.upm
        return [[[(x * s, y * s) for (x, y) in e] for e in c] for c in pen.contours]

    def bounds(self, cp):
        """(xmin, ymin, xmax, ymax) in em of a code point's outline, or None."""
        pts = [p for c in self.contours(cp) for e in c for p in e]
        if not pts:
            return None
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def kerning(self, cps):
        """Pair kerning in em between the given code points: {(cp1, cp2): em}.
        GPOS 'kern' feature (pair adjustment, formats 1 and 2, extension
        lookups), else the legacy 'kern' table."""
        by_name = {}
        for cp in cps:
            if cp in self.cmap:
                by_name.setdefault(self.cmap[cp], []).append(cp)
        names_ = set(by_name)
        pairs = {}
        if "GPOS" in self.tt:
            pairs = _gpos_kerning(self.tt["GPOS"].table, names_)
        if not pairs and "kern" in self.tt:
            for sub in getattr(self.tt["kern"], "kernTables", []):
                for (l, r), v in getattr(sub, "kernTable", {}).items():
                    if l in names_ and r in names_ and v:
                        pairs.setdefault((l, r), v)
        out = {}
        for (l, r), v in pairs.items():
            for a in by_name[l]:
                for b in by_name[r]:
                    out[(a, b)] = v / self.upm
        return out


def _gpos_kerning(gpos, names_):
    if not gpos or not gpos.FeatureList or not gpos.LookupList:
        return {}
    ids = set()
    for fr in gpos.FeatureList.FeatureRecord:
        if fr.FeatureTag == "kern":
            ids.update(fr.Feature.LookupListIndex)
    total = {}
    for li in sorted(ids):
        lookup = gpos.LookupList.Lookup[li]
        subtables = []
        for st in lookup.SubTable:
            lt = lookup.LookupType
            if lt == 9:
                lt, st = st.ExtensionLookupType, st.ExtSubTable
            if lt == 2:
                subtables.append(st)
        done = set()             # pairs a subtable of this lookup already matched
        for st in subtables:
            cov = [g for g in st.Coverage.glyphs if g in names_]
            if st.Format == 1:
                index = {g: i for i, g in enumerate(st.Coverage.glyphs)}
                for first in cov:
                    for rec in st.PairSet[index[first]].PairValueRecord:
                        second = rec.SecondGlyph
                        if second not in names_ or (first, second) in done:
                            continue
                        done.add((first, second))
                        xa = getattr(rec.Value1, "XAdvance", 0) if rec.Value1 else 0
                        if xa:
                            total[(first, second)] = total.get((first, second), 0) + xa
            elif st.Format == 2:
                cd1 = st.ClassDef1.classDefs if st.ClassDef1 else {}
                cd2 = st.ClassDef2.classDefs if st.ClassDef2 else {}
                for first in cov:
                    rec1 = st.Class1Record[cd1.get(first, 0)]
                    for second in names_:
                        if (first, second) in done:
                            continue
                        done.add((first, second))       # a covered first glyph always matches
                        v = rec1.Class2Record[cd2.get(second, 0)].Value1
                        xa = getattr(v, "XAdvance", 0) if v else 0
                        if xa:
                            total[(first, second)] = total.get((first, second), 0) + xa
    return total


def names(tt):
    """(family, style) from the name table, typographic names first."""
    name = tt["name"] if "name" in tt else None

    def get(*ids):
        for i in ids:
            rec = name.getName(i, 3, 1, 0x409) or name.getName(i, 1, 0, 0) if name else None
            if rec is not None:
                return rec.toUnicode().strip()
        return ""
    return get(16, 1) or "Unknown", get(17, 2) or "Regular"


# ---------------------------------------------------------------- installed fonts

FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")


def font_dirs():
    dirs = []
    if os.name == "nt":
        windir = os.environ.get("WINDIR", "C:/Windows")
        dirs += [os.path.join(windir, "Fonts")]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    elif sys.platform == "darwin":
        dirs += ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    else:
        dirs += ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
                 os.path.expanduser("~/.local/share/fonts")]
    try:
        import hou
        dirs += list(hou.findDirectories("fonts"))
    except Exception:
        pass
    extra = os.environ.get("MTLX_TEXT_FONT_PATH", "")
    dirs += [d for d in extra.split(os.pathsep) if d]
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]


def scan_fonts(cache_path=None):
    """[(ref, family, style)] of every font found, sorted by family and style.
    The scan is cached in a JSON keyed by file size and mtime."""
    cache = {}
    if cache_path and os.path.isfile(cache_path):
        try:
            with open(cache_path) as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    out, fresh = [], {}
    for d in font_dirs():
        for root, _, files in os.walk(d):
            for fn in files:
                if not fn.lower().endswith(FONT_EXTS):
                    continue
                path = os.path.join(root, fn).replace("\\", "/")
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                key = "%s|%d|%d" % (path, st.st_size, int(st.st_mtime))
                faces = cache.get(key)
                if faces is None:
                    faces = []
                    n = 1
                    if fn.lower().endswith((".ttc", ".otc")):
                        try:
                            from fontTools.ttLib import TTCollection
                            n = len(TTCollection(path, lazy=True).fonts)
                        except Exception:
                            n = 0
                    for i in range(n):
                        ref = path if n == 1 and not fn.lower().endswith((".ttc", ".otc")) else "%s#%d" % (path, i)
                        try:
                            fam, sty = names(open_font(ref))
                        except Exception:
                            continue
                        faces.append([ref, fam, sty])
                fresh[key] = faces
                out += [tuple(x) for x in faces]
    if cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w") as f:
                json.dump(fresh, f)
        except OSError:
            pass
    return sorted(set(out), key=lambda x: (x[1].lower(), x[2].lower(), x[0]))
