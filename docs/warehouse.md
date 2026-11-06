# Receive, store and pick a synthetic lot

I added a small warehouse exercise because I wanted to learn what happens between receiving a lot and issuing it to a workshop. It uses a separate practice material, so the original lotion and balm records stay available for tracing.

Everything in this exercise is synthetic: the material, lot, locations, capacities and counted weights. The script makes real ERPNext records. It does not measure a real warehouse or provide evidence of operator productivity.

## Run the exercise

Start the workshop using the README, load `.env`, and run:

```sh
set -a
source .env
set +a
python3 -m erp_demo seed
python3 -m erp_demo.warehouse --output artifacts
```

Run this once per disposable site. The command refuses to repeat an existing putaway or pick rather than silently moving stock twice. Setup itself is safe to repeat. To start again, use a fresh demo site after saving any reports you want to keep; `docker compose down --volumes` deletes all workshop data.

The command receives **120 kg** of `WH-DEMO-BASE`, lot `WH-DEMO-BASE-LOT-A`, into **WH Receiving**. It asks ERPNext's own putaway rules, the rules for choosing an available storage location, to allocate **80 kg to WH A-01** and **40 kg to WH B-01**. A-01 has capacity 80 kg and priority 1; B-01 has capacity 60 kg and priority 2. It then submits a pick list for **10 kg from A-01**, maps that pick list to a stock transfer, and posts the transfer to **WH Dispatch**.

Each shelf is a leaf Warehouse under the group **WH Stores**. ERPNext's record named **Bin** means an item balance at a warehouse; it is not a shelf label. ERPNext creates those balance records as the movements post.

## Look at the records

In ERPNext's search bar:

1. Open **Warehouse**. Expand **All Warehouses**, then **WH Stores**, to see A-01 and B-01.
2. Open **Putaway Rule**. Filter Item Code to `WH-DEMO-BASE`; inspect capacities and priorities.
3. Open **Pick List**. Find the ID printed by the command. Its submitted item row identifies the lot, A-01, and the 10 kg picked quantity.
4. Open **Stock Entry**. The receiving, putaway and picking entries have remarks beginning `warehouse-`. The picking entry links back to its Pick List.
5. Open **Stock Ledger** and filter the same Item Code. The final balances are 0 kg in Receiving, 70 kg in A-01, 40 kg in B-01, and 10 kg in Dispatch.

The API asks ERPNext to apply its native putaway rules, rather than reimplementing the allocation in Python. It checks that the complete requested amount has a configured destination before saving anything. An overflow is held for review. Picking uses a submitted native Pick List and ERPNext's stock-entry mapper. The ERP enforces lot availability, and the script never edits balances directly.

## Read the report

`artifacts/warehouse-report.json` contains document IDs, final ledger balances and the measured API workflow time. `artifacts/warehouse-kpis.csv` is a flat table that Power BI can import using **Get data > Text/CSV**. Choose **Decimal number** for `value`, and filter `metric` for a chart. There are no spreadsheet formulas or private records in it.

The count example in [stocktake.json](../examples/warehouse/stocktake.json) intentionally counts A-01 as 69 kg when its ledger says 70 kg. It is a labelled test case, not a measured stocktake.

| Metric | Calculation in this exercise | Expected result |
| --- | --- | --- |
| Stock accuracy | `100 × (1 − sum(abs(count − ledger)) / sum(ledger at counted positions))`, clamped at zero | 99.1667% across 4 counted positions |
| Bin utilization | stock in the two storage bins / their combined capacity | 110 / 140 = 78.5714% |
| Mean pick seconds | mean elapsed time from starting the Pick List API workflow until its transfer is submitted | Measured on each run |

Pick seconds measure a scripted API operation, including network and server work. They are **not a human walking or handling time**. I would need actual operator start and finish events before using this as a labour measure. The timestamp helper accepts timezone-aware observed events and rejects reversed or timezone-free timestamps. Missing counts and pick observations produce blank metrics, not perfect scores. `counted_positions` exposes coverage when I use a partial count. This example uses one item in kg; capacity utilization cannot combine items with incompatible units without a volume or pallet conversion.

## Recover from a failed request

ERPNext commits each document separately. If a POST times out, inspect Stock Entry and Pick List before retrying. The script does not retry writes, cancel submitted records, delete drafts, or assume that a network error means the ERP rejected a document. A failed pick may leave a submitted pick list without a completed transfer; finish or cancel it deliberately through ERPNext after inspecting it.

## Checks and implementation

```sh
python3 -m unittest discover -s tests -p 'test_warehouse*.py' -v
# Against a newly started disposable ERPNext site with .env loaded:
ERP_INTEGRATION=1 python3 -m unittest discover -s tests -p 'test_warehouse*.py' -v
```

The local checks cover transfer ledger arithmetic, count variance, missing observations, capacity and invalid measurements, timezone-aware durations, CSV shape and refusal of partial allocation. The server test verifies native Putaway Rules, the exact 80/40 kg allocation, the lot-linked Pick List, its linked posted Stock Entry, all four ledger and Bin balances, refusal to rerun, overflow handling, and ERPNext's insufficient-stock rejection. It writes the two reports into the CI evidence artifact.

The contract follows the pinned ERPNext source: [Putaway Rule](https://github.com/frappe/erpnext/blob/v15.124.5/erpnext/stock/doctype/putaway_rule/putaway_rule.py), [Pick List](https://github.com/frappe/erpnext/blob/v15.124.5/erpnext/stock/doctype/pick_list/pick_list.py), and [Bin](https://github.com/frappe/erpnext/blob/v15.124.5/erpnext/stock/doctype/bin/bin.json). Business actions live in `erp_demo/warehouse.py`; they reuse the authenticated REST client in `erp_demo/api.py`.
