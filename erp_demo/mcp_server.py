"""Official SDK stdio MCP server. Source documents stay in the configured inbox."""
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .intake import draft_order, load_document
from .order_backends import from_env

mcp = FastMCP('Synthetic workshop order intake', log_level='ERROR')


def erp():
    return from_env()


@mcp.tool()
def search_items(query: str) -> list[dict]:
    """Search enabled items in the selected ERP; missing or ambiguous prices stay null."""
    if not query.strip() or len(query) > 100:
        raise ValueError('Use a nonempty item query under 100 characters')
    return erp().search_items(query)


@mcp.tool()
def get_customer(code: str) -> dict | None:
    """Look up an exact enabled ERP customer ID, without fuzzy substitution."""
    return erp().get_customer(code)


@mcp.tool()
def create_draft_sales_order(document: str, extraction: dict) -> dict:
    """Validate proposed fields against a local source and current ERP data; write only a draft.

    document is a filename in INTAKE_INBOX. Review holds require a person to correct
    the extraction or ERP catalog and rerun. This tool cannot submit a sales order.
    """
    inbox = Path(os.getenv('INTAKE_INBOX', 'examples/intake')).resolve()
    source = (inbox / document).resolve()
    if source.parent != inbox or Path(document).name != document:
        raise ValueError('Source must be a filename directly inside INTAKE_INBOX')
    pages = load_document(source)
    return draft_order(extraction, pages, erp())


if __name__ == '__main__':
    mcp.run(transport='stdio')
