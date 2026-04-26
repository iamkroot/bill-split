from ast import Index
from collections.abc import Iterable
import sublime
import sublime_plugin
import sublime_types
from pathlib import Path
import os


def _stem_with_parent(path: Path):
    """return the "parent/filestem" for path"""
    return path.with_suffix("")


def _pick_path_from_known_file(folders, path: Path):
    for fold in folders:
        fold = Path(fold)
        try:
            path.relative_to(fold)
        except ValueError:
            continue
        else:
            return _stem_with_parent(path)
    else:
        # fallback to stem with parent
        return _stem_with_parent(path)


BILL_SUFFIXES = [".expenses", ".bill"]


def _pick_with_suffix(files: Iterable[str | Path], allowed_suffixes: list[str] | None):
    """Find first file from list that has a known suffix"""
    for file in files:
        path = Path(file)
        if allowed_suffixes is None or path.suffix in allowed_suffixes:
            return _stem_with_parent(path)


def _find_expense_file(window: sublime.Window,  allowed_suffixes: list[str] | None):
    """Find an expense file stem in the project.
    
    Has heuristics to pick a very "relevant" file.
    """
    if sheet := window.active_sheet():
        # if a file is open and it is a known file, use that as base
        if name := sheet.file_name():
            if picked := _pick_with_suffix([name], allowed_suffixes):
                return picked
    # try to find the most recently opened file that we care about
    if picked := _pick_with_suffix(window.file_history(), allowed_suffixes):
        return picked
    # try to find from open files
    open_sheets = (sheet.file_name() for sheet in window.sheets())
    open_sheets = (name for name in open_sheets if name is not None)
    # TODO: Can also look at all files in project
    return _pick_with_suffix(open_sheets, allowed_suffixes)


def _select_base_path(window:sublime.Window):
    if picked := _find_expense_file(window, BILL_SUFFIXES):
        return picked
    # try again without suffix filter
    if picked := _find_expense_file(window, allowed_suffixes=None):
        return picked
    # TODO: try to find from project
    # fallback to home dir
    return Path.home()


class PromptNewFromClipboardCommand(sublime_plugin.WindowCommand):
    EXPENSES_TEMPLATE = "bill_split_template.expenses"

    def run(self):
        default = _find_expense_file(self.window, BILL_SUFFIXES)
        if default is None:
            print("could not find a valid file path")
            initial_text = ""
        else:
            initial_text = str(default)
        self.window.show_input_panel("File path", initial_text, self.on_done, None, None)

    def get_expenses_template(self) -> str:
        path = Path(sublime.packages_path()) / "User" / self.EXPENSES_TEMPLATE
        # TODO: Allow setting this path from a config
        if not path.exists():
            return ""
        return path.read_text()

    def get_expenses(self, items: sublime_types.List[str]) -> str:
        expenses_template = self.get_expenses_template()
        # TODO: Can put some smartness here to auto-categorize the 
        expenses_template += "\n".join(items)
        return expenses_template

    def on_done(self, path: str):
        sublime.get_clipboard_async(lambda d: self.on_bill_contents(Path(path), d))

    @staticmethod
    def get_bill_items(contents: str):
        items = []
        # parse it as tsv, get column 2
        for line in contents.strip().splitlines():
            if line and not line.startswith("!"):
                try:
                    item = line.split("\t")[1]
                except IndexError:
                    print(f"Weird line! {line}")
                else:
                    items.append(item)
        return items

    def on_bill_contents(self, path: Path, contents: str):
        """Create the new path.bill and path.expenses files based on contents of bill."""
        paid_present = False
        if contents.strip().startswith("!paid:"):
            # self.window.status_message("Invalid contents! Should be a bill file.")
            paid_present = True
            # return
        
        items = self.get_bill_items(contents)
        if not items:
            self.window.status_message("No items found!")
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        bill_path, expenses_path = path.with_suffix(".bill"), path.with_suffix(".expenses")
        # assign to proper groups if they are open side-by-side
        bill_group, expenses_group = ((-1, -1), (0, 1))[self.window.num_groups() == 2]

        # create {path}.bill from contents and open it
        bill_contents = ("!paid:\n" if not paid_present else "") + contents
        bill_path.write_text(bill_contents)
        _bill_view = self.window.open_file(str(bill_path), group=bill_group)
        # create {path}.expenses from template and open it
        expenses_path.write_text(self.get_expenses(items))
        _expenses_view = self.window.open_file(str(expenses_path), group=expenses_group)
        sublime.set_clipboard(str(path))


class NewEmptyExpenseCommand(sublime_plugin.WindowCommand):
    def run(self):
        if picked := _select_base_path(self.window):
            initial_text = str(picked.parent) + os.sep
        else:
            initial_text = ""
        self.window.show_input_panel("Bill path", initial_text, self.on_done, None, None)

    def on_done(self, base_path_str: str):
        base_path = Path(base_path_str)
        # just a heuristic to not make too many directories
        if not base_path.parent.parent.parent.exists():
            sublime.error_message(f"Weird base path!! {base_path}")
            # TODO: Should preserve user input and go back
            return
        base_path.parent.mkdir(parents=True, exist_ok=True)
        bill_path = base_path.with_suffix(".bill")
        self.window.open_file(str(bill_path))
        