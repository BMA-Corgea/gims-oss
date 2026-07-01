# utils/spreadsheet_ui.py

from pathlib import Path
import json
from api.i_o import load_schema  # S3-aware *_types.json reader (replaces json.load(open(...)))
from utils.camera_webapp_host import host_camera_webapp
import webbrowser
import asyncio

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container
    from textual.widgets import DataTable, Footer, Header, ListView, ListItem, Label
    from textual.screen import ModalScreen
except Exception:  # pragma: no cover - textual may be absent in test env
    class App:
        def __init__(self, *a, **k):
            pass

    class ComposeResult:
        pass

    class Binding:
        def __init__(self, *a, **k):
            pass

    class Container:
        pass

    class DataTable:
        pass

    class Footer:
        pass

    class Header:
        pass

    class ListView:
        class Selected:
            def __init__(self, item=None):
                self.item = item
        def __init__(self, *items, id=None):
            self.items = items

    class ListItem:
        def __init__(self, label):
            self.label = label
        def query_one(self, cls):
            return self.label

    class Label:
        def __init__(self, text):
            self.renderable = text

    class ModalScreen:
        pass

def extract_headers(setup_schema: dict, project_path: Path) -> list[str]:
    """
    Extract column headers from 'noun_type_ref' or from an explicit 'fields' list.
    """
    if "noun_type_ref" in setup_schema:
        noun_type_name = setup_schema["noun_type_ref"]
        noun_path = project_path / "noun_types.json"
        if noun_path.exists():
            with open(noun_path) as f:
                noun_defs = json.load(f)
            try:
                return list(noun_defs[noun_type_name]["fields"].keys())
            except Exception as e:
                print(f"❌ Failed to extract headers from noun_type_ref: {e}")
                return []
    elif "fields" in setup_schema:
        return [f["name"] for f in setup_schema["fields"]]
    return []


class OptionSelectScreen(ModalScreen):
    """Modal screen that shows a list of options for the user to pick from."""

    def __init__(self, options: list[str], coord: tuple[int, int]):
        super().__init__()
        self.options = options
        self.coord = coord  # (row, col)

    def compose(self) -> ComposeResult:
        items = [ListItem(Label(opt)) for opt in self.options]
        yield ListView(*items, id="option-list")

    async def on_list_view_selected(self, message: ListView.Selected) -> None:
        selected_value = message.item.query_one(Label).renderable
        row, col = self.coord

        # 1) Dismiss the modal immediately
        self.dismiss()

        # 2) Record old value for undo
        prev = self.app.table.get_cell_at((row, col)) or ""
        if prev != selected_value:
            self.app._history.append(((row, col), prev))
            self.app._redo_stack.clear()

        # 3) Update the DataTable cell
        self.app.table.update_cell_at((row, col), selected_value)
        self.app.table.refresh()

        # 4) Return focus to the DataTable and clear any partial edit
        self.app.table.focus()
        self.app._edit_start_coord = None
        self.app._edit_start_value = None

class SpreadsheetApp(App):
    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+q", "quit", "Save + Quit"),
        Binding("ctrl+z", "undo", "Undo"),
        Binding("ctrl+y", "redo", "Redo"),
        Binding("ctrl+c", "copy_cell", "Copy"),
        Binding("ctrl+v", "paste_cell", "Paste"),
        Binding("ctrl+a", "add_rows", "Add 20 Rows"),
        Binding("f1", "open_dropdown", "Dropdown"),
        Binding("f2", "generate_id", "New ID"),
        Binding("enter", "move_down", "↓"),
        Binding("tab", "move_right", "→"),
        Binding("delete", "clear_cell", "Clear Cell"),
        Binding("ctrl+w", "launch_webcam", "Launch Camera WebApp"),
    ]

    CSS = """
    #table-container {
        height: 100%;
        width: 100%;
    }
    #option-list {
        height: 80%;
        width: 60%;
        border: solid white;
        margin: 2 2;
    }
    """

    def __init__(
        self,
        headers: list[str],
        output_path: Path,
        project_path: Path,
        setup_schema: dict,
        adjective_config: dict | None = None,
        run_id: str | None = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.headers = [h for h in headers if not h.startswith("_")]
        self.output_path = output_path
        self.project_path = Path(project_path)
        self.setup_schema = setup_schema
        self.adjective_config = adjective_config or {}
        self.run_id = run_id
        self.table = DataTable(zebra_stripes=True)
        self._clipboard = ""
        self._last_saved_snapshot: list[dict[str, str]] = []
        self._history: list[tuple[tuple[int, int], str]] = []
        self._redo_stack: list[tuple[tuple[int, int], str]] = []
        self._edit_start_coord: tuple[int, int] | None = None
        self._edit_start_value: str | None = None

        # derive noun_name from the setup_schema
        self.noun_name = self.setup_schema.get("noun_type_ref")
        if not self.noun_name:
            raise RuntimeError("Missing 'noun_type_ref' in setup_schema")
        noun_types = load_schema(project_path, "noun")
        self.noun_schema = noun_types[self.noun_name]
        self.noun_types_path = project_path / "noun_types.json"

        # Will be set in on_mount() once table columns exist:
        self.primary_id_field: str | None = None
        self.autogenerate_enabled = False
        self.primary_id_col: int | None = None

        # Debug log to confirm adjective config loaded
        self.console.log("📦 Loaded adjective_config:")
        if not self.adjective_config:
            self.console.log("   (none)")
        else:
            for k, v in self.adjective_config.items():
                self.console.log(f"   {k}: {v}")

    async def watch_refresh_signal(self) -> None:
        """
        Watches for the existence of 'refresh.signal' and triggers reload when detected.
        """
        signal_path = self.project_path / "refresh.signal"
        self.console.log(f"[watch_refresh_signal] Started watching {signal_path}")

        while True:
            if signal_path.exists():
                self.console.log("[watch_refresh_signal] Detected refresh.signal file. Reloading table...")
                self._load_or_create_table()  # or your table reload method

                # Remove signal file to acknowledge
                signal_path.unlink()
                self.console.log("[watch_refresh_signal] refresh.signal processed and deleted.")

            await asyncio.sleep(2)  # check every 2 seconds

    async def refresh_table_from_disk(self):
        try:
            with open(self.output_path) as f:
                data = json.load(f)

            # Clear existing table rows
            self.table.clear()

            # Rebuild rows from fresh data
            for entry in data:
                row = [entry.get(h, "") for h in self.headers]
                self.table.add_row(*row)

            self.console.log("[refresh_table_from_disk] Table updated from disk.")
            self.notify("✅ Data refreshed from disk.", title="Refresh", severity="info")

        except Exception as e:
            self.console.log(f"❌ Failed to refresh table: {e}")
            self.notify(f"Refresh error: {e}", title="Refresh Error", severity="error")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(self.table, id="table-container")
        yield Footer()

    async def on_mount(self) -> None:
        # 1) First, configure the DataTable columns and load existing rows
        self.table.cursor_type = "cell"
        self._load_or_create_table()
        self.table.focus()

        # 2) After columns are added, figure out which is the primary ID column
        noun_type_name = self.setup_schema.get("noun_type_ref")  
        noun_path = self.project_path / "noun_types.json"
        if noun_type_name and noun_path.exists():
            with open(noun_path) as f:
                noun_defs = json.load(f)
            if noun_type_name in noun_defs:
                noun_schema = noun_defs[noun_type_name]
                self.primary_id_field = noun_schema.get("primary_id_field")
                self.autogenerate_enabled = bool(noun_schema.get("autogenerate_id", False))

                if self.primary_id_field in self.headers:
                    self.primary_id_col = self.headers.index(self.primary_id_field)

        # 3) Start your UI refresh interval (if you had it) – adjust as needed
        # self.set_interval(1 / 60, self.refresh)

        # 4) Start the async watcher for refresh.signal
        asyncio.create_task(self.watch_refresh_signal())
        self.console.log("[on_mount] Launching watch_refresh_signal task...")

    def _load_or_create_table(self) -> None:
        """
        (1) Define columns only if empty.
        (2) Clear all rows before loading fresh data.
        (3) Load existing JSON rows.
        (4) Ensure at least 10 rows total.
        """
        _width = max(10, (self.size.width // max(1, len(self.headers))) - 3)

        # (1) Add columns only if none exist
        if not self.table.columns:
            for h in self.headers:
                col_width = max(10, len(h) + 2)  # minimum 10, header length + padding
                self.table.add_column(h.ljust(col_width))

        # (2) Clear existing rows before reloading
        self.table.clear(columns=False)

        # (3) Load existing data
        existing = []
        if self.output_path.exists():
            try:
                existing = json.loads(self.output_path.read_text())
            except Exception:
                pass  # silently ignore malformed JSON

        for row_data in existing:
            row = [str(row_data.get(h, "")) for h in self.headers]
            self.table.add_row(*row)

        # (4) Ensure at least 10 rows
        while self.table.row_count < 10:
            self.table.add_row(*["" for _ in self.headers])

        self.table.focus_coordinate = (0, 0)

    async def on_key(self, event) -> None:
        """
        Handle key events—including:
         • Blocking typing/backspace/delete in an autogenerated primary‐ID column
         • Blocking typing entirely in adjective‐controlled columns
         • Normal navigation (ENTER/TAB/ARROWS) should never show the toast
        """
        if not self.table.has_focus:
            return

        # 1a) If the user just pressed F2 (to “Generate ID”), do not show any read‐only toast.
        #     We will let the F2 binding fire action_generate_id without interference.
        if event.key.lower() == "f2":
            return

        row, col = self.table.cursor_coordinate
        header = self.headers[col]
        current_val = self.table.get_cell_at((row, col)) or ""
        config = self.adjective_config.get(header, {})
        valid_options = config.get("valid_options", [])

        # 1) NAVIGATION KEYS: ENTER / TAB / ARROWS → let them pass immediately
        if event.key in ("enter", "tab", "up", "down", "left", "right"):
            # Commit any pending edit‐history
            if self._edit_start_coord:
                prev = self._edit_start_value
                coord = self._edit_start_coord
                if self.table.get_cell_at(coord) != prev:
                    self._history.append((coord, prev))
                    self._redo_stack.clear()
                self._edit_start_coord = None
                self._edit_start_value = None

            # Now perform the actual move (for ENTER/TAB)
            if event.key == "enter":
                new_row = (row + 1) % self.table.row_count
                self.table.cursor_coordinate = (new_row, col)
                return
            if event.key == "tab":
                new_col = (col + 1) % len(self.headers)
                self.table.cursor_coordinate = (row, new_col)
                return
            # If it's simply an arrow key, do nothing more
            return

        # 2) IF WE’RE IN THE AUTOGENERATED PRIMARY‐ID COLUMN → block editing keys only
        if (
            self.primary_id_col is not None
            and col == self.primary_id_col
            and self.autogenerate_enabled
        ):
            # Only block “editing” keystrokes. But we already returned on F2 above.
            if event.key in ("backspace", "delete") or (len(event.key) == 1 and event.key.isprintable()):
                self.notify(
                    "🛑 This field is autogenerated and cannot be edited.",
                    title="Read-Only",
                    severity="warning",
                )
                return
            # Otherwise (arrow keys, function keys except F2, Ctrl+C, etc.) we do nothing
            # and allow normal behavior to continue.

        # 3) BACKSPACE ON A NON-PRIMARY COLUMN (normal edit)
        if event.key == "backspace":
            # Block backspace if this column has a dropdown (adjective with valid_options)
            if valid_options:
                self.notify(
                    "⛔ Cannot edit here. Use F1 dropdown to choose a value.",
                    title="Restricted",
                    severity="error",
                )
                return

            # record for undo
            if self._edit_start_coord != (row, col):
                self._edit_start_value = current_val
                self._edit_start_coord = (row, col)

            # perform deletion
            self.table.update_cell_at((row, col), current_val[:-1])
            self.table.refresh()
            return

        # 4) DELETE ON A NON-PRIMARY COLUMN (clear cell, record history)
        if event.key == "delete":
            # Always allow clearing the cell, even for dropdown fields. Record
            # the previous value so undo still works.

            # Otherwise, do a normal “clear cell” (with undo history):
            if current_val.strip():
                self._history.append(((row, col), current_val))
                self._redo_stack.clear()
                self.table.update_cell_at((row, col), "")
                self.table.refresh()
            return

        # 5) PRINTABLE CHARACTER TYPING (non-nav keys)
        if len(event.key) == 1 and event.key.isprintable():
            # If this column has valid_options, BLOCK typing entirely:
            if valid_options:
                self.notify(
                    "⛔ Cannot type here. Use F1 to select from dropdown.",
                    title="Restricted",
                    severity="error",
                )
                return
            # Normal typing in a non-autogenerated, non-adjective column:
            if self._edit_start_coord != (row, col):
                self._edit_start_value = current_val
                self._edit_start_coord = (row, col)
            self.table.update_cell_at((row, col), current_val + event.key)
            self.table.refresh()
            return

        # If we reach here, it was some other key (e.g., Ctrl+V, Ctrl+Z, etc.)
        # and we haven’t explicitly blocked it. Let the default handlers kick in.
        return

    def action_open_dropdown(self) -> None:
        """
        Bound to F1: open dropdown for current cell.
        If primary ID column + retest context, show retest options.
        Otherwise, show adjective valid_options as before.
        """
        row, col = self.table.cursor_coordinate
        header = self.headers[col]

        # 🚨 Check if we are on the primary ID column
        if self.primary_id_col is not None and col == self.primary_id_col:
            # Check if this is a retest data entry scenario
            retest_options = self.get_retest_options()
            if retest_options:
                self.console.log(f"🔽 Opening retest dropdown for primary ID with options: {retest_options}")
                self.push_screen(OptionSelectScreen(retest_options, (row, col)))
                return

        # 🔽 Normal adjective dropdown logic
        config = self.adjective_config.get(header, {})
        valid_options = config.get("valid_options", [])

        if valid_options:
            self.console.log(f"🔽 Opening dropdown for '{header}' at ({row},{col})")
            self.push_screen(OptionSelectScreen(valid_options, (row, col)))
        else:
            self.notify(
                f"⚠️ No dropdown choices defined for '{header}'.",
                title="No Options",
                severity="info"
            )

    def action_launch_webcam(self) -> None:
        # 1) Spin up Flask and get the URL
        url = host_camera_webapp(
            port=5001,
            project_name=self.project_path.name,
            noun_name=self.noun_name,
            run_id=self.run_id
        )

        # 2) Log it in the Textual console
        self.console.log(f"🌐 Camera WebApp running at {url}")

        # 3) (Optionally) open it in the user’s browser
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def action_clear_cell(self) -> None:
        """
        Bound to DELETE: clear the current cell (but record undo history first).
        """
        row, col = self.table.cursor_coordinate
        current_val = self.table.get_cell_at((row, col)) or ""
        if current_val.strip():
            self._history.append(((row, col), current_val))
            self._redo_stack.clear()
            self.table.update_cell_at((row, col), "")
            self.table.refresh()

    def action_add_rows(self) -> None:
        """Bound to Ctrl+A: append 20 empty rows."""
        for _ in range(20):
            self.table.add_row(*["" for _ in self.headers])
        self.console.log("➕ Added 20 rows.")

    def action_undo(self) -> None:
        if not self._history:
            return
        coord, old_val = self._history.pop()
        curr_val = self.table.get_cell_at(coord) or ""
        self._redo_stack.append((coord, curr_val))
        self.table.update_cell_at(coord, old_val)
        self.table.refresh()

    def action_redo(self) -> None:
        if not self._redo_stack:
            return
        coord, val = self._redo_stack.pop()
        prev_val = self.table.get_cell_at(coord) or ""
        self._history.append((coord, prev_val))
        self.table.update_cell_at(coord, val)
        self.table.refresh()

    def action_copy_cell(self) -> None:
        row, col = self.table.cursor_coordinate
        self._clipboard = self.table.get_cell_at((row, col)) or ""

    def action_paste_cell(self) -> None:
        """
        Paste from clipboard into the current cell—unless it's the autogenerated ID column
        or an adjective‐controlled column with restricted options.
        """
        row, col = self.table.cursor_coordinate

        # If we're on the primary‐ID column and it is autogenerated, block pasting
        if self.primary_id_col is not None and col == self.primary_id_col and self.autogenerate_enabled:
            self.notify(
                "🛑 This field is autogenerated and cannot be edited (even via Paste).",
                title="Read-Only",
                severity="warning",
            )
            return

        # If this column has a valid_options list (adjective dropdown), ensure the clipboard value is allowed
        header = self.headers[col]
        config = self.adjective_config.get(header, {})
        valid_options = config.get("valid_options", [])
        if valid_options and self._clipboard not in valid_options:
            self.notify(
                f"❌ '{self._clipboard}' not allowed in '{header}'. Use F1.",
                title="Paste Blocked",
                severity="error",
            )
            return

        # record for undo
        prev = self.table.get_cell_at((row, col)) or ""
        if prev != self._clipboard:
            self._history.append(((row, col), prev))
            self._redo_stack.clear()

        # paste
        self.table.update_cell_at((row, col), self._clipboard)
        self.table.refresh()

    def generate_autogenerated_id(self) -> str:
        """
        Build a new, unique ID by:
        • Scanning <project>/nouns/<noun_name>/items.jsonl for existing IDs
        • Scanning the live table for already‐typed IDs
        • Delegating to utils.id_generator.generate_autogenerated_id()
        """
        from utils.id_generator import generate_autogenerated_id as helper

        existing_ids: set[str] = set()

        # 1) Read disk IDs from items.jsonl
        items_path = self.project_path / "nouns" / self.noun_name / "items.jsonl"
        if items_path.exists():
            try:
                with open(items_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        record = json.loads(line)
                        val = record.get(self.primary_id_field)
                        if val:
                            existing_ids.add(str(val))
            except Exception as e:
                self.console.log(f"⚠️ Failed to read existing IDs from {items_path}: {e}")

        # 2) Scan table for typed-in IDs
        if self.primary_id_col is not None:
            for r in range(self.table.row_count):
                cell = (self.table.get_cell_at((r, self.primary_id_col)) or "").strip()
                if cell:
                    existing_ids.add(cell)

        # 3) Generate and return new ID
        return helper(
            noun_type_name=self.noun_name,
            noun_schema=self.noun_schema,
            noun_types_path=self.project_path / "noun_types.json",
            existing_ids=existing_ids
        )

    def get_retest_options(self) -> list[str]:
        """
        Returns a list of run_IDs being retested for dropdown insertion.
        Only applies in retest data entry contexts.
        """
        # Example logic: read from setup_schema or a prepared retest_targets list
        retest_targets = self.setup_schema.get("retest_targets", [])
        return retest_targets if isinstance(retest_targets, list) else []

    def action_save(self) -> None:
        import json

        try:
            entries: list[dict] = []
            noun_type_name = self.setup_schema.get("noun_type_ref")
            run_id = self.run_id

            # — Guard: ensure run_id is set
            if not run_id:
                self.console.log("❌ No run_id set — cannot assign _runID to entries")
                self.notify("Missing run ID", title="Save Error", severity="error")
                return

            # 1️⃣ Collect and validate entries from the UI table
            unique_sets: dict[str, set] = {
                field: set()
                for field, conf in self.adjective_config.items()
                if conf.get("unique_per_run")
            }

            for r in range(self.table.row_count):
                row_vals = [self.table.get_cell_at((r, c)) or "" for c in range(len(self.headers))]
                if not any(v.strip() for v in row_vals):
                    continue

                entry = {h: v.strip() for h, v in zip(self.headers, row_vals)}

                # Validate adjective constraints
                for h, val in entry.items():
                    config = self.adjective_config.get(h, {})
                    valid_options = config.get("valid_options", [])
                    if valid_options and val and val not in valid_options:
                        self.console.log(f"❌ Invalid '{val}' for '{h}'. Must be one of {valid_options}.")
                        self.notify(f"Invalid '{h}': '{val}'", title="Validation Error", severity="error")
                        return
                    if h in unique_sets and val:
                        if val in unique_sets[h]:
                            self.console.log(f"❌ Duplicate value '{val}' for unique field '{h}'.")
                            self.notify(
                                f"Duplicate '{h}' value '{val}' not allowed",
                                title="Validation Error",
                                severity="error",
                            )
                            return
                        unique_sets[h].add(val)

                # Attach _runID
                entry["_runID"] = run_id
                entries.append(entry)

            # 2️⃣ Save to DataEntry.json
            try:
                with open(self.output_path, "w") as f:
                    json.dump(entries, f, indent=2)
            except Exception as e:
                self.console.log(f"❌ Failed to write to {self.output_path}: {e}")
                self.notify(f"Write error: {e}", title="Save Error", severity="error")
                return

            # 3️⃣ Update items.jsonl if applicable
            if noun_type_name:
                self.console.log(f"[action_save] Updating items.jsonl for noun_type='{noun_type_name}'")
                noun_types_path = self.project_path / "noun_types.json"
                try:
                    with open(noun_types_path) as f:
                        noun_defs = json.load(f)
                except Exception as e:
                    self.console.log(f"❌ Failed to read noun_types.json: {e}")
                    self.notify(f"Schema error: {e}", title="Save Error", severity="error")
                    return

                noun_schema = noun_defs.get(noun_type_name)
                if not noun_schema:
                    self.notify(f"Missing noun_type: {noun_type_name}", title="Save Error", severity="error")
                    return

                pid_field = noun_schema.get("primary_id_field")
                if not pid_field:
                    self.notify("Schema error: missing primary_id_field", title="Save Error", severity="error")
                    return

                items_path = self.project_path / "nouns" / noun_type_name / "items.jsonl"
                items_path.parent.mkdir(exist_ok=True, parents=True)

                # Load existing entries & index by (pid, _runID)
                existing: list[dict] = []
                existing_keys: set[tuple[str, str]] = set()

                if items_path.exists():
                    with open(items_path) as f:
                        for idx, line in enumerate(f, start=1):
                            raw = line.strip()
                            if not raw:
                                continue
                            try:
                                rec = json.loads(raw)
                                pid = str(rec.get(pid_field, "")).strip().lower()
                                rid = str(rec.get("_runID", "")).strip().lower()
                                if pid and rid:
                                    existing_keys.add((pid, rid))
                                existing.append(rec)
                            except Exception:
                                self.console.log(f"⚠️ Skipped malformed line {idx}: {raw}")

                # Determine which new entries to append
                new_entries: list[dict] = []
                for entry in entries:
                    pid = str(entry.get(pid_field, "")).strip().lower()
                    rid = str(entry.get("_runID", "")).strip().lower()
                    if not pid:
                        self.console.log(f"⚠️ Skipping entry: Missing primary ID. Entry = {entry}")
                        continue
                    if not rid:
                        self.console.log(f"⚠️ Skipping entry: Missing _runID. Entry = {entry}")
                        continue

                    key = (pid, rid)
                    if key in existing_keys:
                        self.console.log(f"⚠️ Skipping duplicate (pid, runID) entry: {key}")
                        continue

                    existing_keys.add(key)
                    new_entries.append(entry)

                # Debug: show what will be appended
                self.console.log(f"[debug] Will append {len(new_entries)} new entries to items.jsonl:")
                for e in new_entries:
                    self.console.log(f" ➕ {e}")

                # Write back all: existing + new
                with open(items_path, "w") as f:
                    for rec in existing + new_entries:
                        f.write(json.dumps(rec) + "\n")

                self.console.log(
                    f"[action_save] items.jsonl updated: total={len(existing) + len(new_entries)}, "
                    f"new={len(new_entries)}, skipped={len(entries) - len(new_entries)}"
                )

            # 4️⃣ Confirm and notify
            self._last_saved_snapshot = entries
            self.console.log(f"✅ Saved {len(entries)} entries to {self.output_path}")
            self.notify("✅ Saved!", title="Data Saved", severity="info")

        except Exception as e:
            self.console.log(f"❌ Unexpected error in action_save: {e}")
            self.notify(f"Unexpected error: {e}", title="Save Error", severity="error")

    def action_quit(self) -> None:
        self.action_save()
        self.exit()

    def action_force_quit(self) -> None:
        self.exit()

    def action_generate_id(self) -> None:
        """
        Bound to F2: generate a new unique primary‐ID and place it
        in the first empty cell of that column (or append a new row).
        """
        if self.primary_id_col is None or not self.autogenerate_enabled:
            self.notify(
                "⚠️ Autogeneration is not configured for this noun.",
                title="Cannot Generate ID",
                severity="warning",
            )
            return

        try:
            # Use your helper to build the ID
            new_id = self.generate_autogenerated_id()
        except Exception as e:
            self.notify(f"❌ Could not generate ID: {e}", title="Error", severity="error")
            return

        # Insert it in the first blank spot (or append a row)
        for r in range(self.table.row_count):
            if not (self.table.get_cell_at((r, self.primary_id_col)) or "").strip():
                # record old (empty) for undo
                self._history.append(((r, self.primary_id_col), ""))
                self._redo_stack.clear()

                # write new ID
                self.table.update_cell_at((r, self.primary_id_col), new_id)
                self.table.refresh()
                self.notify(f"✔️ New ID '{new_id}' at row {r}", title="ID Generated", severity="info")
                return

        # No blank rows? append one
        self.table.add_row(*["" for _ in self.headers])
        new_row = self.table.row_count - 1

        # record for undo (only cell change)
        self._history.append(((new_row, self.primary_id_col), ""))
        self._redo_stack.clear()

        self.table.update_cell_at((new_row, self.primary_id_col), new_id)
        self.table.refresh()
        self.notify(
            f"✔️ Added row {new_row} and inserted ID '{new_id}'",
            title="ID Generated",
            severity="info",
        )

def run_spreadsheet_ui(
    project_path: Path,
    setup_schema: dict,
    output_path: Path,
    adjective_config: dict,
    run_id: str | None = None
) -> None:
    """
    Launch the terminal‐based spreadsheet UI to edit DataEntry.json.

    Arguments:
      - project_path: Path to the root of the project (e.g. Path("projects/LIMS-System"))
      - setup_schema: the schema dict for this noun (from verb_types.json → data_entry_schema.set_up_inputs)
      - output_path:   Path to the DataEntry.json file for this run
      - adjective_config: loaded adjective UI constraints (e.g. dropdown options
        and uniqueness flags)
      - run_id:        the run identifier (e.g. "run 002"); if provided, _runID will be injected on save
    """
    project_path = Path(project_path)

    # 1) Determine headers for the spreadsheet based on setup_schema
    headers: list[str] = []

    # If setup_schema reuses a noun type
    noun_type_ref = setup_schema.get("noun_type_ref")
    if noun_type_ref:
        # Load noun_types.json to get that noun's fields
        noun_types_path = project_path / "noun_types.json"
        if not noun_types_path.exists():
            raise FileNotFoundError(f"No noun_types.json found in {project_path}")

        with open(noun_types_path) as f:
            noun_defs = json.load(f)

        noun_schema = noun_defs.get(noun_type_ref)
        if not noun_schema:
            raise ValueError(f"Noun type '{noun_type_ref}' not found in noun_types.json")

        # Use the schema's defined field order as headers
        # Expecting noun_schema["fields"] is a dict of { field_name: {type, required, ...}, ... }
        headers = list(noun_schema.get("fields", {}).keys())

    # Otherwise, if setup_schema defines custom "fields"
    elif "fields" in setup_schema:
        # setup_schema["fields"] is expected to be a list of field definitions
        fields_list = setup_schema.get("fields", [])
        # Each field is a dict like { "name": "FieldName", "type": "...", "required": true, ... }
        headers = [fld["name"] for fld in fields_list]

    else:
        # No noun_type_ref and no custom fields—nothing to edit
        raise ValueError("setup_schema must have either 'noun_type_ref' or 'fields' defined")

    # 2) Instantiate SpreadsheetApp with computed headers and run_id
    app = SpreadsheetApp(
        headers=headers,
        output_path=output_path,
        project_path=project_path,
        setup_schema=setup_schema,
        adjective_config=adjective_config,
        run_id=run_id
    )

    # 3) Launch the app
    app.run()
