# Read an order before it reaches stock

I wanted to try copying a customer's order into the workshop without letting a
model invent a product or a price. The examples are fictional emails and text
PDFs. Every proposed field carries a page number, quoted source text and a
confidence score. The server compares these with the original file and the
workshop's customer and price records before writing a draft.

## Try the review without a server

Use Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m erp_demo.intake_benchmark --output artifacts/intake-parallel-run.json
cat artifacts/intake-parallel-run.json
```

The eight labelled orders include a two-page PDF, an email with a PDF attachment,
an unknown product, a changed price, a weak field, an invented quantity and an
omitted line. `examples/intake/labels.json` contains the hand-keyed answers.
`recordings/` includes **hand-authored synthetic protocol fixtures** for the
eight-order replay. `ollama-invalid-flat.json` is an actual failed tool response
from Linux CI, retained as a schema-rejection regression. `ollama-live-clean.json`
is a successful real tool response from the same model after the schema fix. The replay is a repeatable check of the review
pipeline. Its accuracy and timings do not measure a real model or a human's
productivity. The report counts missing and extra fields, all raw fields, fields
on accepted orders, held orders, decision accuracy and seconds per order.

## Try a real model and ERP draft

On a computer allowed to run the workshop containers, follow the README's setup
through `seed`, then install the requirements above and load `.env`:

```sh
set -a
source .env
set +a
.venv/bin/python -m erp_demo.intake_cli --setup
# Offline response replay against REAL ERP master data; review only:
.venv/bin/python -m erp_demo.intake_cli examples/intake/clean.eml --replay examples/intake/recordings/clean.json
# Explicit draft write, never submission:
.venv/bin/python -m erp_demo.intake_cli examples/intake/clean.eml --replay examples/intake/recordings/clean.json --create-draft
```

For Ollama, a local model service, install it on that computer and run
`ollama pull qwen3:1.7b`. Then use:

```sh
.venv/bin/python -m erp_demo.intake_cli examples/intake/clean.eml --backend ollama --model qwen3:1.7b
.venv/bin/python -m erp_demo.intake_benchmark --backend ollama --model qwen3:1.7b --output artifacts/live-parallel-run.json
```

For Anthropic's Messages API, set `ANTHROPIC_API_KEY` in the environment and use
`--backend anthropic --model YOUR_AVAILABLE_MODEL_ID`. Never commit a key.
The adapter sends a tool definition and requires exactly one `extract_order`
call. Both services go through the same schema and source checks. CI tests both
HTTP response formats and runs real CPU Ollama inference on Linux. It does not
call a paid Anthropic account. Real responses and the live parallel report are
uploaded separately as the `real-ollama-intake` Actions artifact.

## Connect the MCP tools

MCP, the Model Context Protocol, lets an assistant discover named tools and call
them with structured arguments. This is a real stdio server built with the
[official Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.25.0).
Configure an MCP host to launch this command from the repository root, using the
same ERP environment as the CLI:

```json
{
  "mcpServers": {
    "workshop": {
      "command": "/absolute/path/to/repository/.venv/bin/python",
      "args": ["-m", "erp_demo.mcp_server"],
      "env": {
        "ERP_URL": "http://127.0.0.1:8080",
        "ERP_USER": "Administrator",
        "ERP_PASSWORD": "SET_LOCALLY",
        "INTAKE_INBOX": "/absolute/path/to/repository/examples/intake"
      }
    }
  }
}
```

`search_items(query)` returns enabled products and their single applicable CAD
selling price. `get_customer(code)` requires an exact enabled customer ID.
`create_draft_sales_order(document, extraction)` reloads a source filename from
the configured inbox, validates the proposed fields, reads fresh ERP data and
returns either review issues or the newly created draft. It cannot read a path
outside the inbox or submit an order. The official SDK client integration test
initializes the server, discovers tools and calls the draft tool against real
ERPNext, then reads the resulting Sales Order back through REST.

A held order has **no ERP write**. I inspect the original file, correct the
proposal or deliberately fix the catalog in ERPNext, then rerun review. A price
change is never approved by changing a model confidence score. Draft orders
still need a person to check delivery dates, tax, shipping, terms and approval.
The example delivery date is a documented placeholder of today plus 14 days.
Stock lines use the demo Finished Goods warehouse; the draft does not reserve or move stock.

## Boundaries and recovery

- Exact codes, CAD prices and kilograms only; no fuzzy customer matches or unit conversions.
- The independent line-completeness check supports the included `ITEM | quantity | price`
  table layout. Other layouts are held for manual review, even if the model finds values.
- Plain email text and text PDFs only. Scans, encryption and empty pages are held
  for manual transcription. Documents are capped at 5 MB, 50 pages and 100,000 characters.
- Confidence is a model proposal, not a calibrated probability. Quotes and master
  data are checked independently; this is a small demonstration, not a fraud detector.
- Same customer/PO is blocked on sequential retries. Concurrent workers can race
  without an ERP uniqueness constraint; use a single writer for this demo.
- No automatic retry of ERP writes. After a lost response, search Sales Orders by
  `po_no` before retrying. ERPNext commits documents separately.
- Anthropic is contract-tested; no paid live run is claimed. Business Central is
  separately [contract-tested](business-central.md), with no live tenant claimed.

API references: [Anthropic tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview),
[Ollama chat](https://docs.ollama.com/api/chat), and
[Frappe REST](https://docs.frappe.io/framework/user/en/api/rest).

## Recorded regression result

The eight-order replay on 6 November 2026 compared 57 fields. It matched 53/57
raw fields (92.98%), held 5/8 orders (62.5%), and matched all 24 fields on the
three accepted orders. All eight ready/review decisions matched their labels.
The intentionally invented quantity and omitted line account for the four
incorrect or missing raw fields. A small local run averaged about 0.08 ms per
order for replay plus review. That is Python pipeline time, not inference time.
Use the live Actions report for model accuracy and seconds per order; no human
keying times were measured and I make no speedup claim.

## Live model result

The [recorded CPU Ollama run](reports/intake-ollama.json) using Qwen3 1.7B
matched all 57 hand-keyed field values across eight orders. It accepted five
orders and held three (37.5%), averaging 20.39 seconds per order. Unknown items
and a price conflict were correctly held. The attachment order was also held
because its page references were wrong, despite correct field values. All
accepted field values were correct. This is eight simple synthetic orders,
not an estimate of production accuracy.

The live report compares decisions with source-document labels: six are eligible
for a clean proposal and two need catalog review. The replay has separate
expected decisions because it deliberately injects a weak score, invented value
and missing line into otherwise valid documents. Those injected problems are
not facts about the source orders or expected mistakes from a real model.
