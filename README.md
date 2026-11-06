# Batch compounding notebook

I built a small workshop tracker because I wanted to see which finished batches and equipment builds need checking when an ingredient lot or a part changes. A spreadsheet can tell me how much oil I bought. I wanted to follow that oil into finished lotion, and keep the filling equipment's parts list consistent with its drawing.

I set up an open-source ERP, a system that connects production and inventory, using ERPNext. This project runs a fictional skincare workshop. It receives ingredient lots, makes finished batches, records quality checks, and compares the stock record with a physical count. It also reads a parts list from FreeCAD, a free drawing and modelling tool, and checks equipment revisions before copying them into the same system. Everything is generated demo data, including the readings and simplified equipment models. These are bookkeeping examples, not tested cosmetic recipes or validated machine designs.

To try it, start the local workshop with the commands below. Then make a batch and ask which finished products contain a particular ingredient lot. The same records are visible in ERPNext at [localhost:8080](http://localhost:8080).

For a quick look without starting a server, download this repository and run `python3 -m erp_demo cad-reuse examples/cad/filler-A.json`. It shows the parts shared by the lotion and balm filling fixtures, including 12 mounting bolts. [Sync the equipment parts](#keep-equipment-parts-and-drawings-together) after starting the workshop.

## Read orders, move stock and plan maintenance

I added order review because a copied quantity or price can change what the workshop
makes. The intake reads fictional emails and PDF orders, checks each field against
its source and the product catalog, and holds uncertain orders for a person. A model
can propose fields through Anthropic or a local Ollama service. The MCP server, a
standard way for assistants to call tools, can create an ERPNext sales order in draft.

For a quick review without any servers, use Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m erp_demo.intake_benchmark
```

This compares eight synthetic orders with hand-keyed answers. The default run uses
hand-authored response fixtures to test the review rules. GitHub Actions also runs
a real local model and saves its separate report. [Read an order and create a
draft](docs/order-intake.md) explains both paths and how to resolve a held order.
[The Business Central connector](docs/business-central.md) exercises Microsoft's
order interface against a local contract server; I have not connected a live tenant.

The [warehouse walkthrough](docs/warehouse.md) receives a separate synthetic lot,
allocates shelf space, picks it and posts a transfer. It exports a CSV that Power BI
can read, with stock-count accuracy, storage use and measured API workflow time.

I also wanted to learn how a maintenance system keeps a service plan attached to
each piece of equipment. The [fleet maintenance guide](docs/maintenance.md) uses a
fictional tug and barge fleet in openMAINT, a free maintenance record system. It
covers running-hour and calendar plans, survey dates, work-order cards, data audits,
legacy-record migration and cost forecasts. Public bearing vibration recordings
and clearly synthetic temperature and oil readings exercise the condition checks.
These are learning examples, not a vessel's approved maintenance program.

## Start the workshop

I use Docker with Compose, which packages ERPNext and its database together, and Python 3.10 or newer. Allow about 6 GB of Docker memory, 12 GB of disk space, and 5 to 15 minutes for the first start. The ERPNext image runs as `linux/amd64`; Apple Silicon uses Docker's emulation.

```sh
git clone https://github.com/jackaI1/erp-batch-manufacturing.git
cd erp-batch-manufacturing
python3 -m erp_demo init
set -a
source .env
set +a
docker compose up -d
python3 -m erp_demo wait
python3 -m erp_demo seed
```

`init` creates random passwords in a private, ignored `.env` file. It refuses to overwrite one that already exists. Log in as `Administrator` using `ERP_PASSWORD` from that file. This setup binds only to this computer and is for disposable demo data. It is not a production deployment.

The setup creates six ingredients, two lots of each ingredient with 100 kg per lot, and two bills of materials. A bill of materials, or BOM, is the ingredient list and quantities for a formula. Each formula makes 10 kg; smaller orders scale the quantities automatically.

| Formula | Ingredients for 10 kg |
| --- | --- |
| Lotion | 7 kg water, 0.5 kg glycerin, 2 kg oil, 0.4 kg emulsifying wax, 0.1 kg preservative |
| Balm | 8 kg oil, 2 kg beeswax |

## Make and trace a batch

```sh
python3 -m erp_demo make lotion --kg 10 --lot A --run first-lotion
python3 -m erp_demo make balm --kg 5 --lot A --run first-balm
python3 -m erp_demo make balm --kg 5 --lot B --run separate-balm
python3 -m erp_demo recall RM-OIL-A
python3 -m erp_demo trace FG-LOTION-first-lotion
python3 -m erp_demo balance RM-OIL-A
```

Each `make` command creates and completes a work order, the instruction to manufacture that amount. It consumes the selected ingredient lots and adds a named finished batch to stock. I use direct consumption from the raw-material warehouse, suitable for a small line without a separate material-transfer step. The API returns the work order, stock entry, finished batch, and quality inspection IDs.

The recall should find `FG-LOTION-first-lotion` and `FG-BALM-first-balm`. They used 2 kg and 4 kg of `RM-OIL-A`. The separate balm used oil lot B, so it stays out of that recall. Oil lot A now has **94 kg** left. This report reads ERPNext's posted lot records, rather than guessing from the formula.

In the ERPNext search bar, open **Work Order**, **Stock Entry**, **Batch**, **Quality Inspection**, or **Stock Ledger** to inspect the underlying records. The lotion inspection records a generated pH reading of 5.5 against a permitted range of 5 to 6, plus a uniform appearance. The balm checks appearance. These checks demonstrate recorded release decisions; I have not measured real samples. I can supply a different sample with `--ph 7.2` or `--appearance Separated`. A failed check leaves the manufacturing stock entry in draft and does not consume ingredients. The `trace` command works backward from a finished batch to its actual ingredient lots and inspection.

## Compare a physical count

If I count 93.75 kg of oil lot A after those three orders:

```sh
python3 -m erp_demo count RM-OIL-A --kg 93.75
python3 -m erp_demo balance RM-OIL-A
```

This posts a stock reconciliation, an adjustment from the recorded amount to the counted amount. It reports **-0.25 kg**, updates the batch balance, and keeps the manufacturing history available for the recall. A matching count is rejected because it needs no adjustment. Counts in this demo apply to raw lots in the Raw Materials warehouse.

I can rerun `seed` without adding the opening stock again. Each manufacturing run needs a new `--run` name. If a request fails midway, I inspect its draft records before retrying: ERPNext commits each document separately, so the whole command is not one transaction.

## Keep equipment parts and drawings together

The two filling fixtures use different nozzles but share a base and a head mount. A bill of materials can contain another bill of materials: each head mount has a bracket and four bolts. This is a multi-level BOM, a parts list with smaller assemblies inside it. I keep the lotion and balm fixtures as two configurations of one equipment family in ERPNext.

After `seed`, load the approved part numbers and review the first drawing revision:

```sh
python3 -m erp_demo cad-setup examples/cad/catalog.json
mkdir -p artifacts
python3 -m erp_demo cad-plan examples/cad/filler-A.json > artifacts/cad-plan.json
cat artifacts/cad-plan.json
```

The plan lists proposed BOMs, problems that need review, shared parts, and an `approval` value. Once I have checked it, I copy that value into this command:

```sh
python3 -m erp_demo cad-sync examples/cad/filler-A.json --approve PASTE_APPROVAL_VALUE
```

This submits three linked BOMs through ERPNext's REST API, the interface scripts use to read and write records. Running it again with a fresh plan creates no duplicates. To try a design change, repeat the plan and sync commands with `filler-B.json`. Revision B uses six bolts per head mount and keeps all three revision A BOMs available. New parents point to the exact new child revision; they never silently use an older default.

I do not let the connector guess a replacement for an unknown part number, convert units, or overwrite a quantity change carrying the same revision. It reports those problems and blocks the whole sync. A change after review invalidates the approval value. ERPNext selects the first submitted BOM as the item's default; later revisions keep that default until I change it deliberately in ERPNext.

The [FreeCAD models and exports](examples/cad/) are included, so no CAD installation is needed to try the connector. To export my own `App::Part` assembly, I set its part numbers and revisions in FreeCAD and run the exporter in its Python console. [The CAD guide](docs/cad-connector.md) covers the exact properties, export commands, revision review, and recovery after a failed request. This is a file-based engineering-to-ERP connector; it does not provide a separate drawing approval or file-locking service.

## Stop or reset

```sh
docker compose down                 # stop, keep the workshop data
docker compose down --volumes       # delete this demo's data when finished
```

Removing the volumes also removes the site. Keep `.env` if I want to reuse its passwords for a fresh start. More detail on recovery, API authentication, and the data model is in [docs/design.md](docs/design.md).

## How I put it together

- `compose.yaml` starts pinned ERPNext, MariaDB, Redis, and the background services.
- `erp_demo/api.py` handles REST requests, session or API-token authentication, and paginated reads.
- `erp_demo/workflow.py` sets up the workshop and posts real ERPNext documents. ERPNext enforces stock availability and maintains the inventory and accounting ledgers.
- `erp_demo/__main__.py` provides the setup, manufacturing, recall, balance, and count commands.
- `erp_demo/freecad_export.py` reads FreeCAD part containers and linked occurrences into a JSON parts list.
- `erp_demo/cad.py` checks part identities, plans revisions, reports shared parts, and submits explicitly linked BOMs.
- `cad/build_example.py` regenerates the included equipment models using real FreeCAD solid geometry.

The recall covers one-stage production from raw ingredients to finished goods. It does not yet follow intermediate blends, shipments, or customer deliveries. The generated quality readings are examples; real release controls would need measured results and permissions for operators and reviewers.

## Checks

```sh
.venv/bin/python -m unittest discover -s tests -v
# On a freshly started, empty demo site with .env loaded:
ERP_INTEGRATION=1 .venv/bin/python -m unittest discover -s tests -v
```

Install `requirements.txt` first. The local suite includes source-grounding, a real MCP client session, OData HTTP contracts, warehouse calculations and maintenance rules. Opt-in ERPNext, openMAINT and Ollama checks run against real systems in separate Linux CI jobs. The original CAD regression checks also remain in CI. The original manufacturing test verifies 3 completed work orders, 20 kg of finished product, all 12 ingredient lots, accepted and rejected inspections, exact ingredient consumption, exclusion of an unrelated lot, setup reruns, an insufficient-stock rejection, and a -0.25 kg reconciliation.

The CAD integration test verifies two native equipment variants, six submitted BOMs across revisions A and B, exact child-revision links, repeat syncs, and blocked part and quantity mismatches. ERPNext's own expanded parts list contains 8 bolts for the two-head balm fixture in A and 12 in B. Another CI job installs FreeCAD, regenerates both models, reopens four files, checks solid geometry, and compares their exported lists. Synthetic ledger, order and warehouse reports are saved as the `compounding-evidence` artifact. Separate jobs save live Ollama results and fleet maintenance records with real UI screenshots. All containers and generated site data are removed even if a check fails.
