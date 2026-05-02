#!/usr/bin/python3
"""Auto creates a split expense on Splitwise.

Uses bill_split.py to do actual splitting.
And sw.py to do the posting.
"""

import re
import bill_split
import subprocess as sp
import random
from pathlib import Path
from pprint import pprint, pformat

SW_SCRIPT = Path(__file__).parent / "tools" / "sw.py"
assert SW_SCRIPT.exists(), SW_SCRIPT


def main():
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
    info = {m['key']: m['val'] for m in PAT.finditer(expenses_data)}
    sw_cmd = [
        SW_SCRIPT,
        info.get("desc", "Bill Split"),
        str(totals),
        "--notes",
        f"{'\n'.join(line for line in expenses_data.splitlines() if not PAT.match(line))}\n\n{detail_pp}"
    ]
    if group := info.get("group"):
        sw_cmd += ["-g", group]
    sp.check_call(sw_cmd)



if __name__ == '__main__':
    main()
