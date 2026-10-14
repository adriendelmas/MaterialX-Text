"""The fonts the tests use: MTLX_TEXT_TEST_FONTS="first.ttf;second.ttf" (two
font files of your own; the second one should lack some Latin Extended
characters the first one has, to exercise the fallback)."""
import os


def test_fonts():
    fonts = [f for f in os.environ.get("MTLX_TEXT_TEST_FONTS", "").split(";") if f]
    if len(fonts) < 2 or not all(os.path.isfile(f) for f in fonts[:2]):
        raise SystemExit("set MTLX_TEXT_TEST_FONTS to two font files, separated by ';' (got %r)" % fonts)
    return fonts[:2]
