# Equipment drawings to workshop records

I use this connector to keep a filling fixture's engineering parts list beside the workshop's ingredient and batch records. The equipment is a simplified example, with real FreeCAD solids and real ERPNext documents. It does not certify a machine design, measure dimensions, or manage drawing files in a PDM server. PDM means product data management: keeping engineering files, part identities, configurations and revisions under control. Here the interface to that process is an explicit exported parts list.

## Try the included drawings

Start and seed the workshop using the README. Load `.env` into the shell, then:

```sh
python3 -m erp_demo cad-setup examples/cad/catalog.json
mkdir -p artifacts
python3 -m erp_demo cad-plan examples/cad/filler-A.json > artifacts/cad-plan.json
cat artifacts/cad-plan.json
# Read the actions, issues and shared parts. Copy the approval value:
python3 -m erp_demo cad-sync examples/cad/filler-A.json --approve PASTE_APPROVAL_VALUE
```

`cad-setup` is an explicit master-data import from a separate approved catalog. It adds eight stock items, one equipment template and the `CAD Configuration` attribute with `Lotion` and `Balm` values. Those are native ERPNext variants, meaning two separately stocked versions of the same family. Setup also adds the read-only `custom_cad_sync` long-text field to BOMs. Setup needs System Manager permissions and must follow `seed` on this disposable site. Normal plans and syncs never create unknown stock items or guess part mappings. An existing item with different units or configuration causes an error rather than being overwritten.

Plans are read-only. `cad-plan` returns JSON and exits 2 if there are review issues, or 0 if it can proceed. `cad-sync` requires the current plan's exact SHA-256 approval value and exits 1 without writing if issues or a stale approval exist. Missing credentials and network failures also exit nonzero. Credentials come from `ERP_PASSWORD` or `ERP_TOKEN`, as in the original demo. Remote URLs require HTTPS; errors hide server response bodies.

Repeat the commands with `filler-B.json` to add revision B. The head mount and both fixtures move to B together. Six bolts replace four in the mount, so the balm fixture needs twelve instead of eight. Old BOMs stay submitted and active. The connector preserves the original default BOM rather than changing manufacturing behaviour automatically. In ERPNext, search **Item** for `EQ-FILLER-BALM` to see its family and configuration; search **BOM** for its two revisions and open the linked `EQ-HEAD` row. Revision metadata is in the CAD sync field.

## Export my own FreeCAD assembly

The exporter uses `App::Part` containers and `App::Link` occurrences. Each definition needs these string properties, added with `addProperty` in the Python console or through FreeCAD's property editor:

| Property | Meaning | Example |
| --- | --- | --- |
| `BOMPartNumber` | Exact ERPNext stock item code | `EQ-HEAD` |
| `BOMRevision` | Engineering revision, a label rather than a sortable number | `A` |
| `BOMName` | Stable readable name shared by all occurrences | `Filling head mount` |
| `BOMTemplate` | Optional family code, paired with configuration | `EQ-FILLER` |
| `BOMConfiguration` | Optional family version | `Lotion` |
| `BOMQuantity` | Optional integer on an occurrence; defaults to one | `2` |

For example, with a part selected as `obj`:

```python
obj.addProperty("App::PropertyString", "BOMPartNumber", "Parts list")
obj.BOMPartNumber = "EQ-HEAD"
```

Set the other required properties the same way. Every child in a container must be a controlled part or link; the exporter skips FreeCAD's internal Origin object. It follows linked definitions and aggregates repeated occurrences of one part number. Conflicting definitions, cycles or missing properties stop the export. Geometric features inside a `PartDesign::Body` belong to that one part and are not separate purchased components. Other assembly workbenches need an adapter to this container/link structure. Visibility does not determine the parts list; remove suppressed parts from the exported configuration's structure explicitly.

In FreeCAD, open **View > Panels > Python console** and run, replacing the repository and output paths with my own:

```python
import sys
sys.path.insert(0, "/path/to/erp-batch-manufacturing")
from erp_demo.freecad_export import export_parts
doc = App.ActiveDocument
export_parts([doc.getObject("Lotion"), doc.getObject("Balm")], "/path/to/parts.json")
```

I can open the included `.FCStd` files to inspect the custom properties, solids, containers and links. To regenerate them on a Mac with FreeCAD installed:

```sh
/Applications/FreeCAD.app/Contents/Resources/bin/python cad/build_example.py
/Applications/FreeCAD.app/Contents/Resources/bin/python cad/check_exports.py
```

On Ubuntu 22.04, install `freecad-python3` and run `/usr/bin/python3 cad/check_exports.py`. The scripts import FreeCAD's real modelling library and close each document in `finally` blocks. They start no GUI or background server. The models use boxes and cylinders as illustrative parts, not dimensioned manufacturing drawings.

## File contract and review decisions

The JSON has `schema: 1`, `roots` (top-level assembly codes), and `parts` (one definition per exact part number). Each part has `part`, `revision`, `name`, `uom: "Nos"`, and optional `components` containing `part` and `qty`. A variant also has `template` and `configuration`. See the included exports for complete examples. The schema deliberately accepts whole counted pieces only. It rejects unknown fields, duplicate rows, nonfinite or fractional quantities, missing parts, disconnected definitions and cycles. There is no approximate matching, geometry-based identity, CSV column guessing, or inferred conversion between pieces and mass.

The connector processes smaller assemblies before their parents and submits each through the existing REST client. A parent row's `bom_no` pins the exact submitted child BOM, as required by [ERPNext's multi-level BOM model](https://docs.frappe.io/erpnext/managing-multi-level-bom). Concrete configurations use [native Item Variants](https://docs.frappe.io/erpnext/v14/user/manual/en/stock/item-variants). Purchased leaves explicitly have `do_not_explode` set, so an unrelated default BOM cannot be pulled into the drawing's structure.

The custom metadata stores the normalized incoming specification, direct child revisions, source fingerprint and actual child BOM IDs. The connector compares both metadata and actual ERP rows, including quantities, units, explosion flags and linked BOMs. An unmanaged BOM, manual edit, duplicate revision, disabled item, wrong configuration, or missing item blocks the complete plan. It never treats an access-denied response as a missing record.

A quantity or component change under an existing revision is a hard review issue. Correct the source or ERP record after investigation, or assign a new engineering revision. A child's revision change also requires revising every affected parent. A new revision gets its own proposed specification beside the previous specifications in the review plan; quantities are not silently accepted just because a revision changed. The approval hash covers the desired graph and observed ERP state and is recomputed before any write. Labels have no implied ordering, so an old A export can still resolve its original BOM after B exists. I do not automatically select a latest revision or retire earlier ones.

The reuse report multiplies quantities along every path and totals each shared component separately for each root. Counts are demand for one of each top-level fixture, not a stock balance or purchase order. It includes shared subassemblies as well as their parts; do not add both together as one purchasing total.

## Failure recovery and limits

ERPNext commits one document per request. The connector does not retry mutations automatically, and a multi-BOM sync is not one transaction. If a connection drops after an insert, run a fresh plan before retrying. An exact existing draft is shown as `resume_draft`; approval submits it before continuing. An already submitted matching revision is reused. Any conflicting draft blocks the graph for inspection. Never delete or overwrite manufacturing history to force a sync through.

Run only one connector writer at a time on this demo. The approval check detects drift before application, but REST does not provide a lock spanning the whole multi-document sync. A production deployment would need a server-side transaction/lock, role-based release workflow, backups, and a mapping from a real PDM system's approved exports. This connector does not transfer CAD files or link individual finished product lots to equipment serial numbers.

All committed examples are synthetic. Review reports are ignored under `artifacts/`. No passwords, supplier records, engineering files from an employer, or personal data belong in this repository. CI uses ephemeral credentials and removes the entire disposable site after testing.
