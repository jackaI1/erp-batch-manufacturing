# Read and prepare orders with Business Central

I added a second order connector because I wanted to learn how the same workshop order would move through a different ERP. Business Central is Microsoft's business records system. This connector uses its documented API, the interface a script calls to read and change records.

**I have tested this connector against a local contract server, not a live Microsoft tenant.** The contract server checks the request paths, filters, draft header and line writes, paging, and edit protection. It cannot prove tenant permissions, tax setup, posting rules, or Microsoft's actual responses in a particular company.

## Try the contract checks

From the repository folder, run:

```sh
python3 -m unittest discover -s tests -p 'test_business_central.py' -v
```

These tests need only Python's standard library. Each test opens a temporary localhost HTTP server on an automatically assigned port. A `finally` block shuts it down, closes its socket and joins its worker thread. The test then verifies that the port is closed. Nothing runs in the background afterwards.

## Connect an existing sandbox

If I already have a Business Central sandbox and an OAuth bearer token with the company's read/write permissions, the setup takes these steps. Obtaining the Microsoft account, application consent and token is separate from this demo; the connector does not store or create tokens.

1. Set the three environment variables below. `BC_COMPANY_ID` is the company's GUID, its long identifier, not its display name. Keep secrets out of shell history and committed files.
2. Run the read-only check. The URL must end in `/api/v2.0`. For an online sandbox the preceding path includes the tenant and environment.

```sh
export BC_API_URL='https://api.businesscentral.dynamics.com/v2.0/YOUR-TENANT/YOUR-SANDBOX/api/v2.0'
export BC_COMPANY_ID='YOUR-COMPANY-GUID'
read -s BC_TOKEN
export BC_TOKEN
python3 - <<'PY'
from erp_demo.business_central import BusinessCentral
api = BusinessCentral.from_env()
print(api.search_items('OIL'))
print(api.get_customer('SYNTH-01'))
PY
```

`search_items` searches item numbers. `get_customer` requires an exact customer number and returns `None` for an unknown number. Ambiguous numbers are held for review.

## Use the same document review and MCP tools

`BusinessCentralOrders` connects this interface to the order intake. The customer
and item numbers still come from the document. The connector reads their real
IDs and catalog prices, then the shared review gate verifies every field before
any header or line write. The MCP server and intake CLI select this destination
with the same configuration:

```sh
export INTAKE_ERP=business-central
export BC_BASE_CURRENCY=CAD  # explicitly confirm the sandbox company's base currency
# BC_API_URL, BC_COMPANY_ID and BC_TOKEN are set as above
.venv/bin/python -m erp_demo.intake_cli examples/intake/clean.eml --backend ollama --model qwen3:1.7b
# After inspecting the review result, explicitly create an unposted draft:
.venv/bin/python -m erp_demo.intake_cli examples/intake/clean.eml --backend ollama --model qwen3:1.7b --create-draft
# An MCP host launches this server with those same environment variables:
.venv/bin/python -m erp_demo.mcp_server
```

The sandbox needs customer `SYN-CUSTOMER` and item `FG-LOTION` with an enabled
base-unit catalog price of 12.50 in CAD to match the sample. These are setup
examples, not invented tenant IDs. I use the local contract server for CI, so I
have not provisioned or connected a Microsoft tenant.

`search_items`, `get_customer` and `create_draft_sales_order` now use the selected
ERP. The adapter requires an explicit base currency; an empty customer currency
code means that configured base currency. It holds blocked records, unsupported
units or currencies, ambiguous prices and tax-inclusive catalog prices. Customer
price agreements, discounts and tax rules need separate tenant-specific review.

Before writing, it re-reads customer and item records and checks for an existing
customer/PO combination. It maps exact reviewed numbers to their actual GUIDs,
creates the unposted header and lines, then reads the draft back with its lines.
It also checks the created header's read-only `pricesIncludeTax` flag before writing any lines. A true or missing flag stops the operation and reports the partial header ID for review. It never calls shipping, invoicing or posting actions. A lost response still
requires inspection; the sequential duplicate check is not an atomic uniqueness
constraint across concurrent workers.

`tests/test_mcp_odata.py` opens a real official-SDK MCP session and a local OData
HTTP contract. It replays an actual recorded Ollama extraction from a source
email, proves unknown items, price conflicts and weak evidence cause no write,
then checks the posted draft header/line payloads and duplicate-PO rejection.
The low-level client remains available for deliberately reviewed callers:
`api.create_draft_sales_order(customer_id, purchase_order, reviewed_lines)`.

## Read pages and edit safely

OData v4 is the query format used by these API routes. `$filter` narrows the returned records; the adapter doubles quotes inside text values so an apostrophe stays part of an item number. `$expand=salesOrderLines` reads an order together with its lines.

A server can divide a collection into several result pages. The adapter follows `@odata.nextLink`, requests at most 200 rows per page, and allows at most 100 pages. It follows links only on the exact requested collection, company and origin. It rejects loops, redirects and responses above 8 MiB, so a paging response cannot forward the token to another site. Narrow the filter if a collection exceeds those limits. Expanded nested line collections are returned as supplied by the server; the adapter does not recursively follow nested continuation links.

An ETag is a record's edit version. I read it before changing a draft:

```python
current = api.get_order(order['id'])
updated = api.update_order(
    current['id'],
    {'externalDocumentNumber': 'SYNTH-PO-001-REVIEWED'},
    current['@odata.etag'],
)
```

The PATCH request sends that exact version in `If-Match`. If someone else has edited the record, the server can return HTTP 412 and the adapter raises `BusinessCentralError(status=412)`. It does not retry or overwrite the newer version. Wildcard `If-Match: *` is disabled.

## Recover an interrupted write

The header and each line are separate server transactions. If a line fails after the header was created, `PartialOrderError.order_id` identifies the draft to inspect and complete or delete manually in the sandbox. The connector does not silently delete it, because someone may have edited it already. If the header request itself times out, its outcome can be unknown: search the company's orders by purchase-order number before retrying. Writes are never automatically retried.

## Documented contracts

The paths and fields follow Microsoft's official documentation:

- [Create a sales order](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/api-reference/v2.0/api/dynamics_salesorder_create) and [create an order line](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/api-reference/v2.0/api/dynamics_salesorderline_create).
- [Get a customer](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/api-reference/v2.0/api/dynamics_customer_get) and [get a sales order](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/api-reference/v2.0/api/dynamics_salesorder_get).
- [Update a sales order with If-Match](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/api-reference/v2.0/api/dynamics_salesorder_update).
- [OData filter expressions](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/webservices/use-filter-expressions-in-odata-uris) and [server-driven paging](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/webservices/server-driven-paging-in-odata-web-services).

`erp_demo/business_central.py` contains the client; `tests/test_business_central.py` contains the HTTP contract server and checks. The test suite validates the adapter's requests and failure behavior. It is deliberately not presented as Microsoft certification or a live-tenant test.
