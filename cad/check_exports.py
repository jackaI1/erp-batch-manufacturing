"""Check real, reopened FCStd models against the committed parts lists."""
import json
from pathlib import Path
import sys
import tempfile

import build_example
from erp_demo.freecad_export import export_parts


def check():
    expected = Path(__file__).resolve().parents[1] / "examples/cad"
    with tempfile.TemporaryDirectory(prefix="cad-export-") as temp:
        output = Path(temp)
        for revision in ("A", "B"):
            build_example.build(output, revision)
            wanted = json.loads((expected / f"filler-{revision}.json").read_text())
            assert json.loads((output / f"filler-{revision}.json").read_text()) == wanted
            # Verify both the freshly generated and the published FCStd files open and export.
            for index, model in enumerate((output / f"filler-{revision}.FCStd", expected / f"filler-{revision}.FCStd")):
                doc = build_example.App.openDocument(str(model))
                try:
                    for obj in doc.Objects:
                        if obj.TypeId == "Part::Feature":
                            assert obj.Shape.isValid() and obj.Shape.Volume > 0
                    roots = [doc.getObject(name) for name in ("Lotion", "Balm")]
                    assert export_parts(roots, output / f"reopened-{revision}-{index}.json") == wanted
                finally:
                    build_example.App.closeDocument(doc.Name)
    print("FreeCAD verified: 2 regenerated models, 4 reopened FCStd files, matching JSON and valid solid geometry")


if __name__ == "__main__":
    check()
