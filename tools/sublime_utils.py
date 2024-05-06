import sublime
import sublime_plugin
import sublime_types
from pathlib import Path


class PromptNewFromClipboardCommand(sublime_plugin.WindowCommand):
    KNOWN_SUFFIXES = [".expenses", ".bill"]
    EXPENSES_TEMPLATE = "bill_split_template.expenses"

    @staticmethod
    def _stem_with_parent(path: Path):
        """return the "parent/filestem" for path"""
        return str(path.with_suffix(""))

    def _pick_path_from_known_file(self, path: Path):
        folders = self.window.folders()
        for fold in folders:
            fold = Path(fold)
            try:
                path.relative_to(fold)
            except ValueError:
                continue
            else:
                return self._stem_with_parent(path)
        else:
            # fallback to stem with parent
            return self._stem_with_parent(path)

    def run(self):
        default = ""
        if sheet := self.window.active_sheet():
            # if a file is open and it is a known file, use that as base
            if name := sheet.file_name():
                path = Path(name)
                if path.suffix in self.KNOWN_SUFFIXES:
                    default = self._stem_with_parent(path)
        if not default:
            # try to find the most recently opened file that we care about
            for file in self.window.file_history():
                path = Path(file)
                if path.suffix in self.KNOWN_SUFFIXES:
                    default = self._stem_with_parent(path)
                    break
            else:
                # TODO: Can also look at all files in project
                print("could not find a valid file path")
        self.window.show_input_panel("File path:", default, self.on_done, None, None)

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
        if not contents.strip().startswith("!paid"):
            self.window.status_message("Invalid contents! Should be a bill file.")
            return
        
        items = self.get_bill_items(contents)
        if not items:
            self.window.status_message("No items found!")
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        bill_path, expenses_path = path.with_suffix(".bill"), path.with_suffix(".expenses")
        # assign to proper groups if they are open side-by-side
        bill_group, expenses_group = ((-1, -1), (0, 1))[self.window.num_groups() == 2]

        # create {path}.bill from contents and open it
        bill_path.write_text(contents)
        _bill_view = self.window.open_file(str(bill_path), group=bill_group)
        # create {path}.expenses from template and open it
        expenses_path.write_text(self.get_expenses(items))
        _expenses_view = self.window.open_file(str(expenses_path), group=expenses_group)
