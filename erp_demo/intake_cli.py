"""Extract, inspect and optionally create a draft on a disposable ERPNext site."""
import argparse
import json
from pathlib import Path

from .api import Client
from .intake import ERPNextOrders, draft_order, load_document, review, setup_orders
from .models import ModelAdapter, ReplayAdapter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('document',type=Path,nargs='?')
    parser.add_argument('--backend',choices=['anthropic','ollama'],default='ollama')
    parser.add_argument('--model')
    parser.add_argument('--replay',type=Path)
    parser.add_argument('--create-draft', action='store_true')
    parser.add_argument('--setup',action='store_true',help='Seed synthetic customer and selling prices on a seeded site')
    args = parser.parse_args()
    if args.setup:
        setup_orders(Client.from_env())
        print('Synthetic customer and prices ready')
        return
    if not args.document:
        parser.error('document is required unless --setup')
    pages = load_document(args.document)
    extraction = (ReplayAdapter(args.replay) if args.replay else ModelAdapter(args.backend,args.model)).extract(pages)
    erp = ERPNextOrders(Client.from_env())
    result = (draft_order if args.create_draft else review)(extraction.model_dump(), pages, erp)
    result['extraction'] = extraction.model_dump()
    print(json.dumps(result,indent=2))


if __name__ == '__main__': main()
