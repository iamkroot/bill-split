"""
This is a simple script to split bill expenses among various people.
Its main inputs are
1. A TSV file of Bill with columns- quantity,name,price. Usually OCR'd from some service
    The first line of the bill should look like "!paid: 1234.00"
    This will be used to account for any taxes/discounts in the final paid amount.
    The price column of the items should therefore be the ORIGINAL price (before taxes.)
2. A description of the people who consumed each item in the bill. Sample-

    # drinks
    lemonade: Killua, Gon
    Rose punch: Leorio x2 (leorio had two servings)
    Tea: Kurapika

    # starters: @everyone
    nachos
    fries
    (the above two items will be split across all the people named here)

    # main course
    pizza: -Gon, Ging x3 (Gon didn't eat it, Ging had thrice as much as others)
    pasta: everyone
    fried rice: -Kurapika (everyone except Kurapika ate this)

The names of items in the description should closely match the names in the bill.

Final output will be each person's share to the total amount in the bill.
"""

import csv
import re
from collections import Counter, defaultdict
from csv import DictReader
from dataclasses import dataclass
from difflib import get_close_matches
from fractions import Fraction
from pathlib import Path
from pprint import pprint
from typing import Iterable

bill_path = Path("./bill.txt")
expenses_data = Path("./expenses.txt").read_text()


@dataclass
class BillItem:
    name: str
    price: Fraction
    quantity: int = 1

    def scale_price(self, multiplier: Fraction):
        return BillItem(self.name, self.price * multiplier, self.quantity)


def parse_bill(path: Path):
    bill_data = path.read_text()
    lines = bill_data.splitlines()

    # first parse the !paid directive
    assert lines[0].strip().startswith("!paid"), \
        "First line should be paid amount directive. Eg: '!paid: 1234.00'"
    total_paid = Fraction(lines[0].split(":")[1].strip())

    # now parse the item lines
    bill_data2 = DictReader(
        [line for line in lines if line.strip() and not line.startswith("!")],
        fieldnames=["quantity", "name", "price"],
        dialect=csv.excel_tab)

    items = [BillItem(
                r['name'],
                Fraction(r['price'].replace(',', '')),
                int(r['quantity']))
             for r in bill_data2]
    # adjust the prices based on actual amount paid
    item_sum = sum(item.price for item in items)
    price_mult = total_paid / item_sum
    return [item.scale_price(price_mult) for item in items]


EVERYONE_NAME = "@everyone"
MULT_PAT = re.compile(r'(?P<name>.*?)\s+x(?P<mult>\d+)$')


@dataclass
class Person:
    name: str
    negate: bool = False
    multiplier: int = 1

    @staticmethod
    def from_names(names: Iterable[str]):
        return [Person(name) for name in names]

    def expand_alias(self, names: set[str]):
        return [Person(name, self.negate, self.multiplier) for name in names]


EVERYONE = Person(EVERYONE_NAME)


def parse_people(names_str: str) -> tuple[list[Person], list[Person]]:
    people: list[Person] = []
    aliases: list[Person] = []
    for person in names_str.strip(", ").split(","):
        person = person.strip()
        if person == EVERYONE_NAME:
            aliases.append(EVERYONE)
            continue
        neg = False
        if person.startswith("-"):
            neg = True
            person = person.lstrip("-").lstrip()
        collection = aliases if '@' in person else people
        if match := MULT_PAT.match(person):
            collection.append(Person(match['name'], neg, int(match['mult'])))
        else:
            collection.append(Person(person, neg))
    return people, aliases


def parse_expenses(data: str):
    cat_people = None
    cat_aliases = None    
    # people = set()
    aliases = defaultdict(set)
    items: dict[str, list[Person]] = {}
    for line in data.splitlines():
        if not line:
            continue

        if line.startswith('@'):
            # parsing a group alias
            split = line.split(":")
            alias = split[0].strip()
            persons, parsed_aliases = parse_people(split[1].strip())
            aliases[alias].update(name.name for name in persons)
            # we will have another pass to resolve all aliases
            # for now, we don't allow alias negations, multipliers
            assert not any(a.negate or a.multiplier != 1 for a in parsed_aliases)
            aliases[alias].update(a.name for a in parsed_aliases)
            continue

        if line.startswith("#"):
            # new category
            split = line.split(":")
            if len(split) > 1:
                # names of people
                cat_people, cat_aliases = parse_people(split[1].strip())
                aliases[EVERYONE_NAME].update(name.name for name in cat_people)
            else:
                # reset the cat_people
                cat_people = None
                cat_aliases = None
            continue
        # now at a food line
        split = line.split(":")
        item_name = split[0].strip()
        if len(split) == 1:
            assert cat_people is not None and cat_aliases is not None and (cat_people or cat_aliases), f"no category people/aliases defined for food item {line}"
            cur_all = cat_people + cat_aliases
        else:
            cur_people, cur_aliases = parse_people(split[1].strip())
            aliases[EVERYONE_NAME].update(name.name for name in cur_people)
            cur_all = cur_people + cur_aliases
        items[item_name] = cur_all

    aliases = resolve_aliases(aliases)
    return finalize_names(items, aliases)


def resolve_aliases(aliases: dict[str, set[str]]):
    """Expand all aliases recursively till they only contain names.
    Plms don't give cyclic.
    """
    if all(all('@' not in name for name in v) for v in aliases.values()):
        # Done!
        return aliases
    new_aliases = {}
    for name, people in aliases.items():
        new_people = people.copy()
        for alias in [n for n in people if '@' in n]:
            new_people.remove(alias)
            new_people.update(aliases[alias])
        new_aliases[name] = new_people
    return resolve_aliases(new_aliases)


def finalize_names(items: dict[str, list[Person]], aliases: dict[str, set[str]]):
    # do a second pass to handle negations and "@everyone"
    # our final return value will only have the names and their multipliers
    final_items: dict[str, Counter] = {}
    for item, names in items.copy().items():
        final_names: Counter[str] = Counter()
        removed_names = Counter()
        if any(name.negate for name in names) and not any(('@' in name.name) for name in names):
            # if there are negations, and no alias has been provided, 
            # need to add EVERYONE implicitly. the negations will be removed later
            final_names.update(aliases[EVERYONE_NAME])
        # first, expand all the aliases
        expanded_names = []
        for person in names:
            if '@' in person.name:
                people = aliases[person.name]
                expanded_names.extend(person.expand_alias(people))
            else:
                expanded_names.append(person)

        for person in expanded_names:
            if person.negate:
                removed_names[person.name] += person.multiplier
            else:
                final_names[person.name] += person.multiplier
        final_names -= removed_names
        assert not any(name.startswith("@") for name in final_names), "found alias in final_names"
        assert all(count >= 0 for count in final_names.values()), "got negative contribution"
        final_items[item] = final_names
    return final_items


def is_sampler(name):
    return name.lower().startswith("sampler")


def assign_shares(items: dict[str, Counter[str]], bill: list[BillItem]):
    samplers = [name for name in items.keys() if is_sampler(name)]
    shares = defaultdict(Fraction)
    details = defaultdict(dict)

    for bill_item in bill:
        candidates = items.keys()
        if is_sampler(bill_item.name):
            candidates = samplers
        matches = get_close_matches(bill_item.name, candidates, n=1, cutoff=0.3)
        assert matches, f"no match for {bill_item} in {', '.join(candidates)}"
        people = items[matches[0]]
        per_person = bill_item.price / Fraction(people.total())
        for person, mult in people.items():
            share = per_person * Fraction(mult)
            shares[person] += share
            details[person][bill_item.name] = share
    print("total", float(sum(shares.values())))
    pprint({name: round(float(share), 2) for name, share in shares.items()})
    pprint(dict({p: {n: round(float(v), 2) for n, v in items.items()} for p, items in details.items()}))


bill = parse_bill(bill_path)
items = parse_expenses(expenses_data)
assign_shares(items, bill)
