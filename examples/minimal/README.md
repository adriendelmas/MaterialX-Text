# Minimal MaterialX use, without Houdini's node

A two-line marking on uv, wobbled by a noise, written as a plain `.mtlx` that
any Karma render can load (`scene.usda` references it).

    hython examples/minimal/make_example.py --font path/to/font.ttf
    husk --renderer BRAY_HdKarmaXPU --settings /Render/rs -o marking.png examples/minimal/scene.usda

`make_example.py` bakes the atlas (`hython -m mtlx_text bake`), lays the text
out (`hython -m mtlx_text table`, which prints the node's input values as
JSON) and writes `marking.mtlx` with those values. Read the template in it to
see every input the node takes.
