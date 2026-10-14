"""Writes the minimal example with a font of your own:
    hython examples/minimal/make_example.py --font path/to/font.ttf
then renders it:
    husk --renderer BRAY_HdKarmaXPU --settings /Render/rs -o marking.png scene.usda

It bakes the atlas and the text table next to this file (`hython -m mtlx_text
bake` / `table`) and writes marking.mtlx: a two-line marking on uv, wobbled by
a noise, with the atlas metrics and the table position the table step prints.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEXT = "NPL-07\\n400 V  50 Hz"

MTLX = """<?xml version="1.0"?>
<!-- Minimal use of the MtlX Text node (ND_textsdf_v2) without Houdini's node:
     a two-line marking on uv, wobbled by a noise, painted on a plate.
     Written by make_example.py from the values `hython -m mtlx_text table` printed. -->
<materialx version="1.39">
  <nodegraph name="NG_marking">
    <texcoord name="st" type="vector2" />
    <!-- a little wobble: noise on 8x the uv, 0.004 uv of amplitude -->
    <multiply name="st_freq" type="vector2">
      <input name="in1" type="vector2" nodename="st" />
      <input name="in2" type="float" value="8.0" />
    </multiply>
    <noise2d name="wobble" type="vector2">
      <input name="texcoord" type="vector2" nodename="st_freq" />
      <input name="amplitude" type="vector2" value="0.004, 0.004" />
    </noise2d>
    <add name="st_wobbled" type="vector2">
      <input name="in1" type="vector2" nodename="st" />
      <input name="in2" type="vector2" nodename="wobble" />
    </add>
    <textsdf name="text" type="multioutput" nodedef="ND_textsdf_v2">
      <input name="texcoord" type="vector2" nodename="st_wobbled" />
      <!-- the atlas: `table`'s "inputs" -->
      <input name="atlas" type="filename" value="{atlas}" />
      <input name="atlas_sdf" type="filename" value="{atlas_sdf}" />
      <input name="atlas_cols" type="float" value="{atlas_cols}" />
      <input name="atlas_rows" type="float" value="{atlas_rows}" />
      <input name="cell" type="float" value="{cell}" />
      <input name="baseline" type="float" value="{baseline}" />
      <input name="ascender" type="float" value="{ascender}" />
      <input name="descender" type="float" value="{descender}" />
      <input name="range" type="float" value="{range}" />
      <!-- the text: the table, and where the text sits in it ("texts"[0]) -->
      <input name="table" type="filename" value="{table}" />
      <input name="textsource" type="integer" value="0" />
      <input name="uv" type="vector2" value="{uv}" />
      <input name="lines" type="vector2" value="{lines}" />
      <!-- placement in uv: anchor (top centre here), 1 em in uv, em height / width -->
      <input name="origin" type="vector2" value="0.5, 0.8" />
      <input name="size" type="float" value="0.12" />
      <input name="aspect" type="float" value="2" />
      <input name="weight" type="float" value="0.01" />
    </textsdf>
    <mix name="paint" type="color3">
      <input name="fg" type="color3" value="0.9, 0.75, 0.1" />
      <input name="bg" type="color3" value="0.12, 0.13, 0.14" />
      <input name="mix" type="float" nodename="text" output="mask" />
    </mix>
    <output name="base_color" type="color3" nodename="paint" />
  </nodegraph>
  <standard_surface name="SR_marking" type="surfaceshader">
    <input name="base_color" type="color3" nodegraph="NG_marking" output="base_color" />
    <input name="specular_roughness" type="float" value="0.4" />
  </standard_surface>
  <surfacematerial name="M_marking" type="material">
    <input name="surfaceshader" type="surfaceshader" nodename="SR_marking" />
  </surfacematerial>
</materialx>
"""


def hython(*args):
    exe = os.path.join(os.environ["HFS"], "bin", "hython")
    return subprocess.run([exe, "-m", "mtlx_text"] + list(args), check=True, capture_output=True, text=True,
                          cwd=HERE).stdout


def main():
    if "--font" not in sys.argv:
        raise SystemExit(__doc__)
    font = os.path.abspath(sys.argv[sys.argv.index("--font") + 1])
    hython("bake", "--font", font, "--out", "atlas.exr")
    out = hython("table", "--atlas", "atlas.exr", "--text", TEXT, "--align", "center", "--out", "marking.exr")
    data = json.loads(out[out.index("{"):])
    values = dict(data["inputs"])
    values["uv"] = "%g, %g" % tuple(data["texts"][0]["uv"])
    values["lines"] = "%g, %g" % tuple(data["texts"][0]["lines"])
    for k in ("atlas", "atlas_sdf", "table"):
        values[k] = os.path.basename(values[k])        # next to the .mtlx
    with open(os.path.join(HERE, "marking.mtlx"), "w", encoding="utf-8") as fh:
        fh.write(MTLX.format(**values))
    print("wrote", os.path.join(HERE, "marking.mtlx"))


if __name__ == "__main__":
    main()
