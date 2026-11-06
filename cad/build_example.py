"""Generate real FreeCAD demo models and their parts lists. No GUI or server stays open."""
import argparse
from pathlib import Path
import sys

# FreeCAD ships its own Python and shared libraries on macOS; Ubuntu installs these paths.
for library in ("/Applications/FreeCAD.app/Contents/Resources/lib", "/usr/lib/freecad/lib", "/usr/lib/freecad-python3/lib"):
    if Path(library).is_dir():
        sys.path.insert(0, library)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import FreeCAD as App
import Part
from erp_demo.freecad_export import export_parts


def build(directory, revision):
    doc = App.newDocument("WorkshopFiller")
    try:
        def properties(obj, code, name, rev="A"):
            for prop, value in (("BOMPartNumber", code), ("BOMRevision", rev), ("BOMName", name)):
                obj.addProperty("App::PropertyString", prop, "Parts list")
                setattr(obj, prop, value)
            obj.Label = name
            return obj

        def piece(code, name, shape):
            obj = properties(doc.addObject("Part::Feature", code.replace("-", "_")), code, name)
            obj.Shape = shape
            return obj

        def occurrence(parent, target, name, qty=1):
            obj = doc.addObject("App::Link", name)
            obj.setLink(target)
            obj.addProperty("App::PropertyInteger", "BOMQuantity", "Parts list")
            obj.BOMQuantity = qty
            parent.addObject(obj)
            return obj

        frame = piece("EQ-FRAME", "Filler base", Part.makeBox(200, 150, 10))
        bracket = piece("EQ-BRACKET", "Head bracket", Part.makeBox(50, 30, 5))
        bolt = piece("EQ-M6-BOLT", "M6 mounting bolt", Part.makeCylinder(3, 20))
        small = piece("EQ-NOZZLE-S", "Lotion nozzle", Part.makeCylinder(4, 40))
        large = piece("EQ-NOZZLE-L", "Balm nozzle", Part.makeCylinder(8, 40))
        head = properties(doc.addObject("App::Part", "Head"), "EQ-HEAD", "Filling head mount", revision)
        occurrence(head, bracket, "Bracket")
        # Four distinct occurrences exercise exporter aggregation in revision A.
        for index in range(4 if revision == "A" else 6):
            link = occurrence(head, bolt, f"Bolt{index}")
            link.Placement.Base = App.Vector((index % 2) * 35, (index // 2) * 10, 5)
        roots = []
        for config, nozzle, qty in (("Lotion", small, 1), ("Balm", large, 2)):
            root = properties(doc.addObject("App::Part", config), "EQ-FILLER-" + config.upper(),
                              config + " filling fixture", revision)
            for prop, value in (("BOMTemplate", "EQ-FILLER"), ("BOMConfiguration", config)):
                root.addProperty("App::PropertyString", prop, "Parts list")
                setattr(root, prop, value)
            occurrence(root, frame, config + "Base")
            link = occurrence(root, head, config + "Head", qty)
            link.Placement.Base = App.Vector(50, 50, 10)
            link = occurrence(root, nozzle, config + "Nozzle", qty)
            link.Placement.Base = App.Vector(70, 60, 15)
            roots.append(root)
        doc.recompute()
        directory.mkdir(parents=True, exist_ok=True)
        doc.saveAs(str(directory / f"filler-{revision}.FCStd"))
        export_parts(roots, directory / f"filler-{revision}.json")
    finally:
        App.closeDocument(doc.Name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("examples/cad"))
    args = parser.parse_args()
    for revision in ("A", "B"):
        build(args.output.resolve(), revision)
    print("Built and closed two FreeCAD assemblies; exported revisions A and B")
