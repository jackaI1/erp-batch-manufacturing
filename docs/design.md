# Workshop design and operating notes

I use ERPNext's standard document model instead of a second inventory database. The Python scripts talk only to the REST API. There is no custom Frappe app, direct SQL, or replacement stock ledger.

## Records and flow

The demo company is Demo Compounding (DCP), with CAD accounting and separate Raw Materials, Work In Progress, and Finished Goods warehouses. Quantities use kilograms. Generated raw batch IDs end in A or B and carry a one-year expiry. Each incoming lot receives 100 kg through one submitted Material Receipt stock entry. Two submitted BOMs describe 10 kg formulas.

A manufacture command submits a Work Order with direct raw-material consumption, calls ERPNext's work-order-to-stock-entry method, selects each ingredient batch explicitly, and creates a named finished batch linked to the work order. The BOM requires an inspection and the site stops stock submission for missing, draft, or rejected inspections. It saves the draft stock entry, creates and submits its Quality Inspection from the matching template, links that inspection, then submits the Manufacture entry. ERPNext generates Serial and Batch Bundles, its records of exact lot quantities on each movement. It updates the work order's produced quantity and stock/accounting ledgers.

The recall scans submitted Manufacture entries for this company and joins their submitted batch bundles. It returns the outputs only where the selected input lot actually appears. It ignores draft and cancelled stock entries. The `trace` command reverses the lookup: it finds a finished lot and returns the posted ingredient lots, quantities, work order, stock entry, and inspection. This is a one-stage recall; multi-stage blends and downstream deliveries need a recursive extension. One line may use only one raw batch per run in the current CLI.

The balance command sums signed batch quantities from non-cancelled stock ledger movements and their submitted bundles. Reconciliation uses a standard Stock Reconciliation document with the selected batch, counted quantity, stock adjustment account, and cost center. ERPNext writes the correcting ledger movements. The demo assumes these raw lots stay in Raw Materials; a multi-warehouse deployment needs a warehouse-aware balance/count interface.

## Setup decisions

I chose the standard Docker ERPNext image over a custom app build so the example exercises the ERP directly and can be reproduced with Compose. Version 15.124.5 is pinned to keep batch bundle behavior stable. The service arrangement is adapted from [Frappe's Docker example](https://github.com/frappe/frappe_docker/blob/main/pwd.yml), with the MIT notice in `NOTICE`. ERPNext itself is GPL-3.0.

Local passwords are randomly generated, never committed. Docker exposes only the frontend on 127.0.0.1:8080. Python uses a login session by default. For an existing HTTPS demo site, set `ERP_URL` and either `ERP_USER`/`ERP_PASSWORD` or `ERP_TOKEN=api_key:api_secret` in the shell. The token user needs permissions for manufacturing, stock, setup, and quality records. The HTTP client refuses all redirects so it cannot forward credentials to another endpoint. The seeded inspections use Administrator as the synthetic inspector; this is not an operator-permission model.

Use a dedicated, disposable site. Setup changes the site's stock batch-field setting, quality-rejection/submission policies, global defaults, setup-complete flag, and work-order batch-creation setting. It does not isolate those settings from other companies on the site. Master-data setup is sequential and not safe to run concurrently. Existing demo master data is reused, not silently overwritten; edit it in ERPNext or reset the site if changing formulas.

## Failure and restart

- Check container state with `docker compose ps -a`. The first site creation can take several minutes.
- Use `docker compose logs --tail=40 create-site backend` locally to diagnose startup. Do not share raw logs from a real site.
- If installation failed after creating a partial site, remove this demo's volumes with `docker compose down --volumes`, then start again. This deletes its database, files, and ledger history.
- If credentials changed after initial installation, `.env` does not reset the existing site's password. Restore the original `.env` or reset the disposable volumes.
- Each API request is a separate transaction. A failed manufacture may leave a submitted work order, batch, inspection, and draft stock entry. Inspect those records in ERPNext; do not retry the same run blindly. The command rejects a reused finished-batch name.
- ERPNext rejects insufficient batch stock on submission. A failed submission must not consume stock. The integration test verifies this using a deliberately oversized order.
- `seed` finds the marked opening receipt and existing master data. It can finish a draft opening receipt but does not top up stock after manufacturing or reconciliation. Cancelled opening receipts are not considered current setup.
- To stop only this project's containers: `docker compose down`. To erase only its named Compose volumes: add `--volumes`. Do not use global Docker prune commands.

## Test contract

The integration test needs a freshly initialized site with no prior manufacturing. It repeats setup, manufactures 10 kg of lotion from lots A, 5 kg of balm from lots A, and 5 kg of balm from lots B. Oil A drops from 100 to 94 kg; oil B drops to 96 kg. It proves a recall of A excludes the B output. A physical count of 93.75 kg posts a -0.25 kg adjustment without altering that recall. An out-of-range sample (pH 7.2) leaves its entry in draft without consuming stock. It also checks completed work-order quantities, submitted inspection status, finished-lot balances, and no stock loss from the rejected oversized order.

Local transport tests use a short-lived HTTP fixture for URL encoding, authentication headers, pagination, safe errors, and access-denied behavior. They do not simulate manufacturing. Each fixture binds an OS-selected loopback port and closes its server and thread after its test. CI's real Docker stack is scoped to the Compose project, and an always-run cleanup step removes containers and volumes and verifies no owned container remains.
