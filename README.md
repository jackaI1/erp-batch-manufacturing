# Batch compounding notebook

I built a small batch-tracking system because I wanted to understand how a compounding line keeps track of every ingredient after it becomes part of a finished product. A spreadsheet can tell me how much oil I bought. I wanted to answer a harder question: if one oil lot has a problem, which batches of lotion and balm need to be checked?

I set up an open-source ERP, a system that connects production and inventory, using ERPNext. This project runs a fictional skincare workshop. It receives ingredient lots, stores two formulas, makes finished batches, records quality checks, and compares the stock record with a physical count. Everything is generated demo data, including the readings. These are bookkeeping examples, not tested cosmetic recipes.

To try it, start the local workshop with the commands below. Then make a batch and ask which finished products contain a particular ingredient lot. The same records are visible in ERPNext at [localhost:8080](http://localhost:8080).

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
python3 -m erp_demo balance RM-OIL-A
```

Each `make` command creates and completes a work order, the instruction to manufacture that amount. It consumes the selected ingredient lots and adds a named finished batch to stock. I use direct consumption from the raw-material warehouse, suitable for a small line without a separate material-transfer step. The API returns the work order, stock entry, finished batch, and quality inspection IDs.

The recall should find `FG-LOTION-first-lotion` and `FG-BALM-first-balm`. They used 2 kg and 4 kg of `RM-OIL-A`. The separate balm used oil lot B, so it stays out of that recall. Oil lot A now has **94 kg** left. This report reads ERPNext's posted lot records, rather than guessing from the formula.

In the ERPNext search bar, open **Work Order**, **Stock Entry**, **Batch**, **Quality Inspection**, or **Stock Ledger** to inspect the underlying records. The lotion inspection records a generated pH reading of 5.5 against a permitted range of 5 to 6, plus a uniform appearance. The balm checks appearance. These checks demonstrate recorded release decisions; I have not measured real samples.

## Compare a physical count

If I count 93.75 kg of oil lot A after those three orders:

```sh
python3 -m erp_demo count RM-OIL-A --kg 93.75
python3 -m erp_demo balance RM-OIL-A
```

This posts a stock reconciliation, an adjustment from the recorded amount to the counted amount. It reports **-0.25 kg**, updates the batch balance, and keeps the manufacturing history available for the recall. A matching count is rejected because it needs no adjustment. Counts in this demo apply to raw lots in the Raw Materials warehouse.

I can rerun `seed` without adding the opening stock again. Each manufacturing run needs a new `--run` name. If a request fails midway, I inspect its draft records before retrying: ERPNext commits each document separately, so the whole command is not one transaction.

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

The recall covers one-stage production from raw ingredients to finished goods. It does not yet follow intermediate blends, shipments, or customer deliveries. The generated quality readings are examples; real release controls would need measured results and permissions for operators and reviewers.

## Checks

```sh
python3 -m unittest discover -s tests -v
# On a freshly started, empty demo site with .env loaded:
ERP_INTEGRATION=1 python3 -m unittest discover -s tests -v
```

The first command runs 11 local tests and skips the server test. GitHub Actions also starts a real ERPNext Docker stack and runs the integration test without a mock ERP. It verifies 3 completed work orders, 20 kg of finished product, all 12 ingredient lots, accepted inspections, exact ingredient consumption, exclusion of an unrelated lot, setup reruns, an insufficient-stock rejection, and a -0.25 kg reconciliation. Synthetic recall and balance reports are saved as the `compounding-evidence` CI artifact. Cleanup runs even if a check fails.
