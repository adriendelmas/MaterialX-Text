"""Command line: hython -m mtlx_text <command> ...

  bake   bake the atlas of a font set (for a farm, or ahead of a session)
         hython -m mtlx_text bake --font a.ttf --font b.otf#1 [--charset western ...]
                [--custom "..."] [--quality 48] [--out path.exr] [--backend auto|numpy|msdfgen]
         Without --out the atlas goes where the node would put it
         ($MTLX_TEXT_CACHE, else ~/.mtlx_text outside a Houdini session),
         so a node with the same fonts and settings finds it.
  fonts  list the installed fonts (reference, family, style)
  table  lay texts out into a text table, for a MaterialX document written
         without the Houdini node; prints JSON: the node's input values (atlas
         metrics, table) and, per text, its uv / lines (inputs uv and lines with
         textsource 0, or primvars text_uv / text_lines with textsource 1)
         hython -m mtlx_text table --atlas a.exr --text "..." [--text "..."]
                [--font 0] [--width 0] [--align left] [--tracking 0]
                [--style '{"size": 0.1, "origin": [0.05, 0.9]}'] --out table.exr
         --style sets the per-text values read in primvars mode (textsource 1);
         inputs it does not name take their nodedef defaults.
"""
import argparse
import json
import sys

from . import atlas, fonts


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mtlx_text", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bake", help="bake a font set's atlas")
    b.add_argument("--font", action="append", required=True, help="font file, or file.ttc#N")
    b.add_argument("--charset", action="append", choices=list(atlas.CHARSETS),
                   help="character sets (default: western)")
    b.add_argument("--custom", default="", help="extra characters")
    b.add_argument("--quality", type=int, default=48, choices=(32, 48, 64, 96, 128, 192), help="pixels per em")
    b.add_argument("--out", help="atlas path (.exr); default: the cache path the node uses")
    b.add_argument("--backend", choices=("auto", "numpy", "msdfgen"), default=None,
                   help="distance field generator (default: MTLX_TEXT_MSDF, else auto: msdfgen when found)")
    sub.add_parser("fonts", help="list the installed fonts")
    t = sub.add_parser("table", help="lay texts out into a text table (prints the node's input values)")
    t.add_argument("--atlas", required=True, help="a baked atlas (.exr)")
    t.add_argument("--text", action="append", required=True, help="a text (\\n for new lines); repeat for more")
    t.add_argument("--font", type=int, default=0)
    t.add_argument("--width", type=float, default=0.0, help="wrapping width in em, 0 = none")
    t.add_argument("--align", default="left", choices=("left", "center", "right", "justify"))
    t.add_argument("--tracking", type=float, default=0.0)
    t.add_argument("--style", default="{}", help="JSON {input: value} of the per-text values")
    t.add_argument("--out", required=True, help="table path (.exr)")
    a = ap.parse_args(argv)
    if a.cmd == "table":
        from . import layout
        fs = layout.FontSet.from_atlas(a.atlas)
        style = layout.style_vector(json.loads(a.style))
        tab, keys = layout.Table(), []
        for text in a.text:
            text = text.replace("\\n", "\n")
            keys.append(tab.add(layout.text_rows(text, fs, a.font, a.width, a.align, a.tracking), style))
        tab.write(a.out)
        m = atlas.load_metrics(a.atlas)
        inputs = {"atlas": a.atlas, "atlas_sdf": atlas.sdf_path(a.atlas), "atlas_cols": float(m["cols"]),
                  "atlas_rows": float(m["rows"]), "table": a.out}
        inputs.update({k: float(m[k]) for k in ("cell", "baseline", "ascender", "descender", "range")})
        texts = [{"text": s, "uv": list(tab.primvars(k)[0]), "lines": list(tab.primvars(k)[1])}
                 for s, k in zip(a.text, keys)]
        print(json.dumps({"inputs": inputs, "texts": texts}, indent=1, ensure_ascii=False))
        return 0
    if a.cmd == "bake":
        charsets = tuple(a.charset or ("western",))
        out = atlas.bake(a.font, charsets=charsets, custom=a.custom, px_per_em=a.quality, out=a.out,
                         backend=a.backend)
        print(out)
    elif a.cmd == "fonts":
        for ref, fam, sty in fonts.scan_fonts():
            print("%s\t%s\t%s" % (ref, fam, sty))
    return 0


if __name__ == "__main__":
    sys.exit(main())
