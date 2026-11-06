"""Run inside FreeCAD's Python console or its bundled Python interpreter."""
import json
from pathlib import Path

from .cad import validate


def export_parts(roots, path):
    """Export App::Part containers and App::Link occurrences, never geometric guesses."""
    definitions, visiting = {}, set()

    def definition(occurrence):
        obj = occurrence.getLinkedObject(True) if occurrence.TypeId == "App::Link" else occurrence
        required = ("BOMPartNumber", "BOMRevision", "BOMName")
        if any(not hasattr(obj, prop) or not getattr(obj, prop) for prop in required):
            raise ValueError(f"Missing BOM properties on FreeCAD object {obj.Name}")
        code = obj.BOMPartNumber
        if code in visiting:
            raise ValueError(f"Assembly cycle at {code}")
        visiting.add(code)
        part = {"part": code, "revision": obj.BOMRevision, "name": obj.BOMName, "uom": "Nos"}
        if getattr(obj, "BOMTemplate", "") or getattr(obj, "BOMConfiguration", ""):
            part.update(template=getattr(obj, "BOMTemplate", ""),
                        configuration=getattr(obj, "BOMConfiguration", ""))
        components = {}
        for child in getattr(obj, "Group", []):
            if child.TypeId == "App::Origin":
                continue
            child_code = definition(child)
            qty = getattr(child, "BOMQuantity", 1)
            if isinstance(qty, bool) or int(qty) != qty or qty <= 0:
                raise ValueError("BOMQuantity must be a positive whole number")
            components[child_code] = components.get(child_code, 0) + int(qty)
        if components:
            part["components"] = [{"part": c, "qty": q} for c, q in sorted(components.items())]
        visiting.remove(code)
        if code in definitions and definitions[code] != part:
            raise ValueError(f"Conflicting definitions of part number {code}")
        definitions[code] = part
        return code

    data = {"schema": 1, "roots": sorted(definition(root) for root in roots),
            "parts": []}
    data["parts"] = [definitions[code] for code in sorted(definitions)]
    validate(data)
    Path(path).write_text(json.dumps(data, indent=2) + "\n")
    return data
