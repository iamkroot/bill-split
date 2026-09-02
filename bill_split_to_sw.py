#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "falx",
# ]
# [tool.uv.sources]
# falx = { path = "../fin/falx", editable = true }
# ///
"""Auto creates a split expense on Splitwise.

Uses bill_split.py to do actual splitting.
And falx util sw to do the posting.
"""

import os
import sys
import re
import argparse
import bill_split
import subprocess as sp
import random
from datetime import date
from pathlib import Path
from pprint import pprint, pformat


def get_falx_config():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "-c",
        "--falx-config",
        "--config",
        dest="falx_config",
        type=Path,
        default=Path(os.environ["FALX_CONFIG"]) if "FALX_CONFIG" in os.environ else None,
        help="Path to falx.toml",
    )
    parser.add_argument(
        "--fin-dir",
        dest="fin_dir",
        type=Path,
        default=Path(os.environ["FIN_DIR"]) if "FIN_DIR" in os.environ else None,
        help="Path to fin directory containing falx.toml",
    )
    args, remaining_argv = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining_argv]

    if "-h" in sys.argv or "--help" in sys.argv:
        return None

    if args.falx_config:
        config = Path(args.falx_config).expanduser().resolve()
    elif args.fin_dir:
        config = (Path(args.fin_dir).expanduser().resolve() / "falx.toml")
    else:
        raise KeyError("FALX_CONFIG or FIN_DIR (via flag or environment variable) is required")

    assert config.exists(), f"Falx config not found at: {config}"
    return config


def get_info_from_path(bill_path: Path):
    m = re.search(r'/(?P<yyyy>20[0-9]{2})-(?P<mm>[0-9]{2})/(?P<desc>.*?)(-(?P<dd>[0-9]+))?.bill$', str(bill_path))
    if not m:
        return {"desc": None, "date": None}
    desc: str = m['desc'].replace('-', " ").title()
    if m.group('dd'):
        return {"desc": desc, "date": date(int(m['yyyy']), int(m['mm']), int(m['dd'])).isoformat()}
    else:
        return {"desc": desc, "date": None}


def main():
    falx_config = get_falx_config()
    bill_path, expenses_data, beannames = bill_split.parse_args()
    # make the RNG consistent for a given bill
    random.seed(str(bill_path))
    total_paid, bill, bill_sum = bill_split.parse_bill(bill_path)
    print(f"bill sum: {float(bill_sum):.2f}")

    items = bill_split.parse_expenses(expenses_data)
    # reset the seed so that shares assignment is deterministic
    random.seed(str(bill_path))
    totals, details = bill_split.assign_shares(items, bill)
    print(f"total: {sum(totals.values()):.2f}")
    pprint(totals)
    detail_pp = pformat({
        p: {n: round(float(v), 2) for n, v in items.items()}
        for p, items in details.items()
    })
    print(detail_pp)
    if beannames.exists():
        bill_split.gen_beancount_postings(total_paid, totals, beannames.read_text())

    PAT = re.compile(r"!\s*sw-(?P<key>.*)\s*:\s*(?P<val>.*)")
    pathinfo = get_info_from_path(bill_path)
    info = pathinfo | {m['key']: m['val'] for m in PAT.finditer(expenses_data)}
    sw_cmd = [
        "falx",
        "util",
        "sw",
        "-c",
        str(falx_config),
        "create-expense",
        info.get("desc", "Bill Split"),
        str(totals),
        "--notes",
        f"{'\n'.join(line for line in expenses_data.splitlines() if not PAT.match(line))}\n\n{detail_pp}",
    ]
    if group := info.get("group"):
        sw_cmd += ["-g", group]
    if d := info.get("date"):
        sw_cmd += ["--date", d]
    print(sw_cmd)
    sp.check_call(sw_cmd)


if __name__ == '__main__':
    main()
