"""Run with python3 -m erp_demo. Only init writes local credentials."""
import argparse
import json
import os
from pathlib import Path
import secrets
import time
from urllib.error import URLError

from .api import Client, ERPError
from .workflow import seed, manufacture, recall, reconcile, batch_balance, trace


def init(path=Path(".env")):
    # Exclusive creation preserves both existing credentials and restrictive permissions.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(f"ERP_PASSWORD={secrets.token_hex(24)}\nDB_PASSWORD={secrets.token_hex(24)}\n")
    return {"message": "Created .env. Keep it local; load it with: set -a; source .env; set +a"}


def main():
    parser = argparse.ArgumentParser(description="A disposable skincare compounding demo in ERPNext")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Generate local credentials without overwriting .env")
    sub.add_parser("wait", help="Wait up to 15 minutes for the real ERPNext API")
    sub.add_parser("seed", help="Create synthetic items, lots, formulas and opening stock")
    make = sub.add_parser("make", help="Post a work order, accepted inspection and manufacturing stock entry")
    make.add_argument("formula", choices=["lotion", "balm"])
    make.add_argument("--kg", type=float, required=True)
    make.add_argument("--lot", choices=["A", "B"], default="A")
    make.add_argument("--run", required=True, help="Unique letters/numbers/hyphens for the finished batch")
    make.add_argument("--ph", type=float, default=5.5, help="Lotion sample pH (default is a synthetic example)")
    make.add_argument("--appearance", default="Uniform", help="Observed appearance (default is synthetic)")
    recall_parser = sub.add_parser("recall", help="Find finished batches containing an ingredient lot")
    recall_parser.add_argument("batch")
    trace_parser = sub.add_parser("trace", help="Read a finished batch's actual ingredient lots and inspection")
    trace_parser.add_argument("batch")
    balance = sub.add_parser("balance", help="Read current batch quantity from the posted stock ledger")
    balance.add_argument("batch")
    count = sub.add_parser("count", help="Post a raw lot physical count in Raw Materials")
    count.add_argument("batch")
    count.add_argument("--kg", type=float, required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = init()
        elif args.command == "wait":
            deadline = time.monotonic() + 900
            while True:
                try:
                    api = Client.from_env()
                    api.get("Stock Settings", "Stock Settings")
                    result = {"ready": True}
                    break
                except (ERPError, URLError, TimeoutError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("ERPNext did not become ready within 15 minutes") from None
                    time.sleep(5)
        else:
            api = Client.from_env()
            if args.command == "seed":
                result = seed(api)
            elif args.command == "make":
                result = manufacture(api, args.formula, args.kg, args.lot, args.run, ph=args.ph, appearance=args.appearance)
            elif args.command == "recall":
                result = recall(api, args.batch)
            elif args.command == "trace":
                result = trace(api, args.batch)
            elif args.command == "balance":
                result = {"batch": args.batch, "kg": batch_balance(api, args.batch)}
            else:
                result = reconcile(api, args.batch, args.kg)
        print(json.dumps(result, indent=2))
    except (ERPError, ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
