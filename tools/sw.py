#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "splitwise",
# ]
# ///
import json
import sys
import ast
import argparse
import logging
from dataclasses import dataclass
from difflib import get_close_matches
from splitwise import Splitwise, SplitwiseNotFoundException
from splitwise.expense import Expense
from splitwise.user import ExpenseUser
from pathlib import Path
from datetime import datetime as dt, timezone

# --- Setup Logging ---
# The default logging level is INFO. Use --debug for more verbose output.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Configuration ---
CONFIG_PATH = Path.home() / ".local/state/sw.json"


@dataclass
class Secrets:
    consumer_key: str
    consumer_secret: str
    api_key: str

    @classmethod
    def from_path(cls, config_path: Path):
        try:
            config = json.loads(config_path.read_text())
        except FileNotFoundError:
            logger.error(f"Configuration file not found at {config_path}.")
            sys.exit(1)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Error parsing configuration file at {config_path}: {e}")
            sys.exit(1)
        
        consumer_key = config.get("consumer_key")
        consumer_secret = config.get("consumer_secret")
        api_key = config.get("api_key")
        
        if not all([consumer_key, consumer_secret, api_key]):
            logger.error(f"One or more API credentials missing in {config_path}. Ensure 'consumer_key', 'consumer_secret', and 'api_key' are set.")
            sys.exit(1)
        return cls(consumer_key, consumer_secret, api_key)
    
    def sw(self):
        """Create a Splitwise object from the given secrets"""
        return Splitwise(self.consumer_key, self.consumer_secret, api_key=self.api_key)


def resolve_user(search_name: str, members: list):
    """Finds a user from a list of members, handling exact, prefix, and fuzzy matches."""
    search_name = search_name.lower().strip()

    def full_name(m):
        return f"{m.first_name or ''} {m.last_name or ''}".strip().lower()

    # 1. Exact match on full name
    exact_full = [m for m in members if full_name(m) == search_name]
    if len(exact_full) == 1: return exact_full[0]

    # 2. Exact match on first name
    exact_first = [m for m in members if (m.first_name or "").lower().strip() == search_name]
    if len(exact_first) == 1: return exact_first[0]
    if len(exact_first) > 1:
        conflicts = ", ".join([full_name(m).title() for m in exact_first])
        raise ValueError(f"Ambiguous name '{search_name}'. Matches multiple people: {conflicts}. Please use a full name or initial.")

    # 3. Prefix match (e.g., 'john d' -> 'john doe')
    prefix_matches = [m for m in members if full_name(m).startswith(search_name)]
    if len(prefix_matches) == 1: return prefix_matches[0]
    if len(prefix_matches) > 1:
        conflicts = ", ".join([full_name(m).title() for m in prefix_matches])
        raise ValueError(f"Ambiguous prefix '{search_name}'. Matches multiple people: {conflicts}.")

    # 4. Fuzzy match on full name
    name_map = {full_name(m): m for m in members}
    matches = get_close_matches(search_name, name_map.keys(), n=1, cutoff=0.5)
    
    return name_map[matches[0]] if matches else None


def create_expense(sw: Splitwise, user_amounts: dict[str, float], description: str, date_: dt, group_name: str | None = None, notes: str | None = None):
    current_user = sw.getCurrentUser()
    logger.debug(f"Successfully authenticated as: {current_user.first_name} {current_user.last_name} (ID: {current_user.id})")

    total_expense = sum(user_amounts.values())
    logger.info(f"Total expense amount calculated: {total_expense:.2f}")

    available_members = []
    target_group = None

    if group_name:
        # --- Find the Group ---
        groups = sw.getGroups()
        target_group = next((g for g in groups if g.name.lower() == group_name.lower()), None)

        if not target_group:
            logger.error(f"Group '{group_name}' not found.")
            return False
        logger.info(f"Found group: '{target_group.name}' (ID: {target_group.id})")
        available_members = target_group.members
    else:
        # --- Find Friends (Non-Group Expense) ---
        logger.info("No group specified. Fetching friends for direct expense...")
        available_members = sw.getFriends()
        # Ensure the current user is in the searchable pool so they can owe money too
        available_members.append(current_user)

    # --- Match Names to Members/Friends ---
    member_names_log = [f"{m.first_name or ''} {m.last_name or ''}".strip() for m in available_members if m.first_name]
    logger.debug(f"Available people to match: {', '.join(member_names_log)}")

    expense_users = []
    payer_handled_in_split = False
    
    for name, amount in user_amounts.items():
        try:
            matched_member = resolve_user(name, available_members) if name != "me" else current_user
        except ValueError as e:
            logger.error(str(e))
            return False

        if not matched_member:
            context = f"in the group '{group_name}'" if group_name else "among your friends"
            logger.error(f"Could not find a match for '{name}' {context}. Aborting.")
            return False
        
        full_matched_name = f"{matched_member.first_name or ''} {matched_member.last_name or ''}".strip()
        logger.info(f"Matched '{name}' -> '{full_matched_name}' (User ID: {matched_member.id}) owes {amount:.2f}")
        
        user = ExpenseUser()
        user.setId(matched_member.id)
        user.setOwedShare(f"{amount:.2f}")
        
        if matched_member.id == current_user.id:
            user.setPaidShare(f"{total_expense:.2f}")
            payer_handled_in_split = True
            logger.info("Payer is part of the split. Their paid share is being added to their entry.")
        else:
            user.setPaidShare("0.00")  # Explicitly state they paid nothing to avoid API errors
            
        expense_users.append(user)

    # --- Create and Add the Expense ---
    # Assumption: The API user paid the full amount.
    expense = Expense()
    expense.setCost(f"{total_expense:.2f}")
    expense.setDescription(description)
    
    if date_:
        expense.setDate(date_)
        
    if target_group:
        expense.setGroupId(target_group.id)
        
    # Add notes if provided
    if notes:
        expense.setDetails(notes)
        logger.debug(f"Adding notes to the expense:\n{notes}\n***")
    
    # If the payer was not in the amounts_dict, create a separate entry for them.
    if not payer_handled_in_split:
        logger.info("Payer is not part of the split. Creating a separate entry for the payer.")
        paid_user = ExpenseUser()
        paid_user.setId(current_user.id)
        paid_user.setPaidShare(f"{total_expense:.2f}")
        paid_user.setOwedShare("0.00")
        expense_users.append(paid_user)

    expense.setUsers(expense_users)

    logger.info("Creating expense on Splitwise...")
    created_expense, errors = sw.createExpense(expense)

    if errors:
        logger.error(f"Failed to create expense. Errors: {errors.errors}")
        return False

    assert created_expense is not None
    logger.info(f"Successfully created expense! ID: {created_expense.id}, Description: '{created_expense.description}'")
    logger.info(f"View it here: https://secure.splitwise.com/expenses/{created_expense.id}")
    return created_expense.id


def parse_datetime_arg(val: str) -> dt:
    """Parses an ISO date or datetime, assumes local if naive, returns UTC datetime."""
    try:
        dt_ = dt.fromisoformat(val)
        return dt_.astimezone(timezone.utc)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date/time format: '{val}'. Use ISO 8601 (e.g., YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS).")


def main():
    """Main function to parse arguments and create a Splitwise expense."""
    parser = argparse.ArgumentParser(
        description="Add a new expense to Splitwise (with a group or directly with friends) with exact amounts.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    parser.add_argument("description", help="A description for the expense (e.g., 'Dinner').")
    parser.add_argument(
        "amounts_dict",
        help="A Python dictionary string mapping short names to the exact amount they owe. \n"
             "Example: \"{'john': 10.50, 'jane': 15.75, 'bob': 5.00}\""
    )
    parser.add_argument(
        "-g", "--group", 
        help="Optional: The name of the Splitwise group (e.g., 'Apartment'). If omitted, checks against your Friends.",
        default=None
    )
    parser.add_argument("--notes", help="Optional notes or details for the expense.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    parser.add_argument("--date", type=parse_datetime_arg, default=dt.now(timezone.utc), help="Optional datetime of the transaction.")
    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    # --- Safely parse the amounts dictionary from the command line ---
    try:
        user_amounts = ast.literal_eval(args.amounts_dict)
        if not isinstance(user_amounts, dict):
            raise ValueError
    except (ValueError, SyntaxError):
        logger.error("Invalid format for amounts_dict. Please provide a valid Python dictionary string.")
        sys.exit(1)

    try:
        sw = Secrets.from_path(CONFIG_PATH).sw()
        if not create_expense(sw, user_amounts, args.description, args.date, args.group, args.notes):
            sys.exit(1)
    except SplitwiseNotFoundException:
        logger.error("Splitwise authentication failed. Check your API credentials.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
