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
from datetime import date

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


def find_best_match(name, choices, cutoff=0.6):
    """Finds the best fuzzy match for a name from a list of choices."""
    matches = get_close_matches(name, choices, n=1, cutoff=cutoff)
    return matches[0] if matches else None


def create_expense(sw: Splitwise, group_name: str, user_amounts: dict[str, float], description: str, date_: date, notes: str | None = None):
    current_user = sw.getCurrentUser()
    logger.debug(f"Successfully authenticated as: {current_user.first_name} {current_user.last_name} (ID: {current_user.id})")

    total_expense = sum(user_amounts.values())
    logger.info(f"Total expense amount calculated: {total_expense:.2f}")

    # --- Find the Group ---
    groups = sw.getGroups()
    target_group = next((g for g in groups if g.name.lower() == group_name.lower()), None)

    if not target_group:
        logger.error(f"Group '{group_name}' not found.")
        return False
    logger.info(f"Found group: '{target_group.name}' (ID: {target_group.id})")

    # --- Match Names to Group Members ---
    group_members = target_group.members
    group_member_names = [f"{member.first_name.lower()}" for member in group_members]
    logger.debug(f"Available group members: {', '.join(group_member_names)}")

    # The user who paid the expense
    paid_user = ExpenseUser()
    paid_user.setId(current_user.id)
    paid_user.setPaidShare(f"{total_expense:.2f}")

    expense_users = []
    payer_handled_in_split = False
    for name, amount in user_amounts.items():
        best_match_name = find_best_match(name.lower(), group_member_names)
        if not best_match_name:
            logger.error(f"Could not find a match for '{name}' in the group. Aborting.")
            return False
        
        matched_member = next(
            (m for m in group_members if m.first_name.lower() == best_match_name), None
        )
        
        if matched_member:
            logger.info(f"Matched '{name}' -> '{best_match_name}' (User ID: {matched_member.id}) owes {amount:.2f}")
            user = ExpenseUser()
            user.setId(matched_member.id)
            user.setOwedShare(f"{amount:.2f}")
            if matched_member.id == current_user.id:
                user.setPaidShare(f"{total_expense:.2f}")
                payer_handled_in_split = True
                logger.info("Payer is part of the split. Their paid share is being added to their entry.")
            expense_users.append(user)

    # --- Create and Add the Expense ---
    # Assumption: The API user paid the full amount.
    expense = Expense()
    expense.setCost(f"{total_expense:.2f}")
    expense.setDescription(description)
    if date_:
        expense.setDate(date_)
    expense.setGroupId(target_group.id)
    # Add notes if provided
    if notes:
        expense.setDetails(notes)
        logger.debug(f"Adding notes to the expense:\n{notes}\n***")
    
    expense.setUsers(expense_users)
    # If the payer was not in the amounts_dict, create a separate entry for them.
    # This handles the case where the payer owes nothing.
    if not payer_handled_in_split:
        logger.info("Payer is not part of the split. Creating a separate entry for the payer.")
        paid_user = ExpenseUser()
        paid_user.setId(current_user.id)
        paid_user.setPaidShare(f"{total_expense:.2f}")
        
        # Add the payer to the list of users involved in the expense
        expense.addUser(paid_user)

    logger.info("Creating expense on Splitwise...")
    created_expense, errors = sw.createExpense(expense)

    if errors:
        logger.error(f"Failed to create expense. Errors: {errors.errors}")
        return False

    assert created_expense is not None
    logger.info(f"Successfully created expense! ID: {created_expense.id}, Description: '{created_expense.description}'")
    logger.info(f"View it here: https://secure.splitwise.com/expenses/{created_expense.id}")
    return created_expense.id


def main():
    """Main function to parse arguments and create a Splitwise expense."""
    parser = argparse.ArgumentParser(
        description="Add a new expense to a Splitwise group with exact amounts for each person.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("group_name", help="The name of the Splitwise group (e.g., 'Apartment').")
    parser.add_argument("description", help="A description for the expense (e.g., 'Groceries').")
    parser.add_argument(
        "amounts_dict",
        help="A Python dictionary string mapping short names to the exact amount they owe. \n"
             "Example: \"{'john': 10.50, 'jane': 15.75, 'bob': 5.00}\""
    )
    parser.add_argument("--notes", help="Optional notes or details for the expense.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="Optional date of the transaction.")
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
        if not create_expense(sw, args.group_name, user_amounts, args.description, args.date, args.notes):
            sys.exit(1)
    except SplitwiseNotFoundException:
        logger.error("Splitwise authentication failed. Check your API credentials.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
