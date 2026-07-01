from __future__ import annotations
import json
from pathlib import Path
from utils.interface import indexed_choice, menu_prompt
from utils.id_generator import generate_hex_segment
import datetime
from utils.id_generator import prompt_date_format

# Valid field type labels for user interface
VALID_FIELD_TYPES = ["number", "string", "date", "datetime"]
# Map user-friendly labels to internal storage types
TYPE_MAP = {"number": "float", "string": "string", "date": "date", "datetime": "datetime"}

class NounType:
    def __init__(self, name: str, schema: dict, noun_file: Path):
        self.name = name
        self.schema = schema
        self.noun_file = noun_file

    def validate_field_structure(self):
        valid_types = set(TYPE_MAP.values())
        fields = self.schema.get("fields", {})
        for field_name, field in fields.items():
            ftype = field.get("type")
            if ftype not in valid_types and ftype != "adjective":
                raise ValueError(
                    f"Invalid type '{ftype}' for field '{field_name}' in noun '{self.name}'"
                )
        pid = self.schema.get("primary_id_field")
        if pid and pid not in fields:
            raise ValueError(
                f"primary_id_field '{pid}' is not defined among fields for noun '{self.name}'"
            )

    def add_field(self, field_name: str, field_type: str, required: bool = False):
        internal_type = TYPE_MAP.get(field_type, field_type)
        entry = {"type": internal_type}
        if required:
            entry["required"] = True

        if internal_type == "date":
            from utils.id_generator import prompt_date_format
            fmt = prompt_date_format()
            if fmt:
                entry["format"] = fmt
            else:
                print("⚠️ No date format selected, defaulting to raw string.")
        
        self.schema.setdefault("fields", {})[field_name] = entry
        self._save()

    def edit_field(self, field_name: str, new_type: str = None, required: bool = None, format_override: str = None):
        fields = self.schema.get("fields", {})
        if field_name not in fields:
            raise KeyError(f"Field '{field_name}' not found in noun '{self.name}'")
        if new_type:
            internal_type = TYPE_MAP.get(new_type, new_type)
            fields[field_name]["type"] = internal_type
            if internal_type == "date":
                if format_override:
                    fields[field_name]["format"] = format_override
                else:
                    from utils.id_generator import prompt_date_format
                    fmt = prompt_date_format()
                    if fmt:
                        fields[field_name]["format"] = fmt
        if required is not None:
            if required:
                fields[field_name]["required"] = True
            else:
                fields[field_name].pop("required", None)
        self._save()

    def delete_field(self, field_name: str):
        fields = self.schema.get("fields", {})
        if field_name in fields:
            del fields[field_name]
            self._save()

    def rename_field(self, old_name: str, new_name: str):
        fields = self.schema.get("fields", {})
        if old_name not in fields:
            raise KeyError(f"Field '{old_name}' not found in noun '{self.name}'")
        if new_name in fields:
            raise KeyError(f"Field '{new_name}' already exists in noun '{self.name}'")

        # 1) Rename in noun schema
        fields[new_name] = fields.pop(old_name)

        # 1a) If this was the primary ID, update it
        if self.schema.get("primary_id_field") == old_name:
            self.schema["primary_id_field"] = new_name

        # Persist noun_types.json
        self._save()

        # 2) Update any adjective entry on this noun
        adjective_file = self.noun_file.parent / "adjective_types.json"
        if adjective_file.exists():
            with open(adjective_file, "r") as f:
                data = json.load(f)
            updated = False
            for entry in data:
                if entry.get("adjective") == old_name and self.name in entry.get("applies_to", []):
                    entry["adjective"] = new_name
                    updated = True
            if updated:
                with open(adjective_file, "w") as f:
                    json.dump(data, f, indent=2)

    def interactive_configure(self):
        """
        Interactive add/edit/rename/delete loop for noun fields,
        with primary-ID/autogen gated behind (p).
        """
        fields = self.schema.setdefault("fields", {})
        while True:
            print(f"\n🧬 Current fields for noun '{self.name}':")
            if not fields:
                print("  (none)")
            else:
                for i, (fname, fdata) in enumerate(fields.items()):
                    req = " (required)" if fdata.get("required") else ""
                    itype = fdata.get("type")
                    label = next((lbl for lbl, typ in TYPE_MAP.items() if typ == itype), itype)
                    print(f"[{i}] {fname} : {label}{req}")

            action = menu_prompt({
                'a': 'add',
                'e': 'edit',
                'r': 'rename',
                'd': 'delete',
                'p': 'primary ID',
                'q': 'quit'
            })

            if action == 'q':
                break

            if action == 'a':
                fname = input("➕ New field name: ").strip()
                if not fname or fname in fields:
                    print("❌ Invalid or duplicate name.")
                    continue
                ftype = self._ask_field_type()
                if not ftype:
                    continue
                req = input("Required? (y/n): ").strip().lower().startswith("y")
                self.add_field(fname, ftype, req)
                fields = self.schema["fields"]

            elif action == 'e':
                if not fields:
                    print("❌ No fields to edit.")
                    continue
                idx = input("Enter field index to edit: ").strip()
                if not idx.isdigit() or int(idx) not in range(len(fields)):
                    print("❌ Invalid index.")
                    continue
                fname = list(fields.keys())[int(idx)]
                print(f"✏️ Editing attributes of field '{fname}':")
                ftype = self._ask_field_type(default=None)
                fmt_override = None
                if ftype == "date":
                    fmt_override = prompt_date_format()
                req_in = input("Required? (y/n/blank to keep): ").strip().lower()
                required = None if req_in == "" else req_in.startswith("y")
                self.edit_field(fname, new_type=ftype, required=required, format_override=fmt_override)
                fields = self.schema["fields"]

            elif action == 'r':
                if not fields:
                    print("❌ No fields to rename.")
                    continue
                idx = input("Enter field index to rename: ").strip()
                if not idx.isdigit() or int(idx) not in range(len(fields)):
                    print("❌ Invalid index.")
                    continue
                old = list(fields.keys())[int(idx)]
                new = input(f"🔄 New name for '{old}': ").strip()
                if not new:
                    print("❌ Name cannot be blank.")
                    continue
                try:
                    self.rename_field(old, new)
                except Exception as e:
                    print(f"❌ {e}")
                fields = self.schema["fields"]

            elif action == 'd':
                if not fields:
                    print("❌ No fields to delete.")
                    continue
                idx = input("Enter field index to delete: ").strip()
                if not idx.isdigit() or int(idx) not in range(len(fields)):
                    print("❌ Invalid index.")
                    continue
                fname = list(fields.keys())[int(idx)]
                confirm = input(f"🗑 Are you sure you want to delete '{fname}'? (y/n): ").strip().lower()
                if confirm.startswith("y"):
                    self.delete_field(fname)
                    fields = self.schema["fields"]

            elif action == 'p':
                flist = list(self.schema["fields"].keys())
                if not flist:
                    print("❌ No fields to choose from.")
                    continue

                idx = indexed_choice(flist, "Select primary ID field")
                if idx is None:
                    continue

                chosen = flist[idx]
                self.schema["primary_id_field"] = chosen
                print(f"🔑 Set primary ID field to: '{chosen}'")

                autogen_choice = menu_prompt({
                    'y': 'yes, autogenerate new IDs',
                    'n': 'no, user must enter manually',
                    'k': 'keep existing settings'
                })

                if autogen_choice == 'y':
                    self.schema["autogenerate_id"] = True
                    print("⚙️  Configuring ID generation logic...")
                    configure_autogenerate_id(self)

                elif autogen_choice == 'n':
                    self.schema["autogenerate_id"] = False
                    self.schema["autogenerate_segments"] = []
                    print("✍️  Manual entry enabled for primary ID.")

                elif autogen_choice == 'k':
                    print("⚙️  Keeping existing autogeneration settings.")

        print(f"✅ Configuration complete for noun '{self.name}'")

    def interactive_edit(self):
        print(f"\n🛠 Editing noun '{self.name}'")
        self.interactive_configure()

        pid = self.schema.get("primary_id_field")
        ag = self.schema.get("autogenerate_id", False)
        print(f"\nℹ️  Primary ID: '{pid}' — autogenerate is {'enabled' if ag else 'disabled'}")

        # Only invoke configure_autogenerate_id() if autogenerate_id=True
        # AND there is no existing 'autogenerate_segments' key in the schema:
        if ag and "autogenerate_segments" not in self.schema:
            print("⚙️  Configuring ID generation logic...")
            configure_autogenerate_id(self)
        else:
            print("ℹ️  Skipping ID configuration (already defined).")

        self._save()
        print(f"✅ Noun '{self.name}' updated.")

    def _ask_field_type(self, default="string"):
        idx = indexed_choice(VALID_FIELD_TYPES, f"Field type index [default: {default}]")
        if idx is None:
            return default
        return VALID_FIELD_TYPES[idx]

    def _save(self):
        with open(self.noun_file, "r") as f:
            all_nouns = json.load(f)
        all_nouns[self.name] = self.schema
        with open(self.noun_file, "w") as f:
            json.dump(all_nouns, f, indent=2)

    def interactive_register_from_context(self, existing_nouns: dict, alias_path: Path) -> bool:
        if self.name in existing_nouns:
            print(f"❌ Noun '{self.name}' already exists.")
            return False

        desc = input(f"📄 Description for noun '{self.name}': ").strip()
        self.schema["description"] = desc
        self.schema["fields"] = {}

        self.interactive_configure()

        fields = list(self.schema["fields"].keys())
        if fields:
            idx = indexed_choice(fields, "Select primary ID field")
            self.schema["primary_id_field"] = fields[idx] if idx is not None else fields[0]

        auto = input("Autogenerate ID? (y/n) [y]: ").strip().lower() or "y"
        self.schema["autogenerate_id"] = auto.startswith("y")

        # Only ask to configure segments if autogenerate_id=True
        # AND no existing autogenerate_segments key in schema:
        if self.schema["autogenerate_id"] and "autogenerate_segments" not in self.schema:
            print("⚙️  Configuring ID generation logic...")
            configure_autogenerate_id(self)
        else:
            print("ℹ️  Skipping ID configuration (already defined or disabled).")

        self._save()

        print(f"\n✅ Registered noun: {self.name}")
        return True

def configure_autogenerate_id(noun_type: NounType):
    from utils.interface import indexed_choice, menu_prompt

    print("\n⚙️  Configure autogenerated ID segments.")
    segments = []

    while True:
        # Top‐level prompt for adding segments / viewing / quitting
        print("\n⚙️  Configure autogenerated ID segments.")
        action = menu_prompt({
            's': 'static string segment',
            'd': 'dynamic segment',
            'v': 'view current format',
            'q': 'done'
        })

        if action == 'q':
            break

        if action == 'v':
            if not segments:
                print("🔍 No segments configured yet.")
            else:
                preview = []
                for seg in segments:
                    if seg["type"] == "static":
                        preview.append(seg["value"])
                    elif seg["type"] == "date":
                        preview.append(datetime.datetime.now().strftime(seg["format"]))
                    elif seg["type"] == "number":
                        preview.append(f"{int(seg['start']):0{seg['length']}d}")
                    elif seg["type"] == "letter":
                        preview.append(chr(65 + int(seg['start'])))
                    elif seg["type"] == "hex":
                        preview.append(generate_hex_segment(seg['start'], 0, seg['length']))
                print(f"🧪 Preview format: {''.join(preview)}")
            continue

        if action == 's':
            val = input("Enter static string (e.g., SMP): ").strip()
            if val:
                segments.append({"type": "static", "value": val})
            else:
                print("❌ Static value cannot be empty.")
            continue

        if action == 'd':
            # Use indexed_choice here instead of menu_prompt for numeric choices
            dyn_options = [
                "Date component",
                "Number counter",
                "Letter counter",
                "Hexadecimal counter"
            ]
            idx = indexed_choice(dyn_options, "Choose dynamic segment type")
            if idx is None:
                # User pressed 'q' at the indexed_choice prompt
                continue

            seg = {}
            if idx == 0:
                seg["type"] = "date"
                fmt = prompt_date_format()
                if fmt:
                    seg["format"] = fmt
                else:
                    print("❌ No format selected. Skipping this segment.")
                    continue

            elif idx == 1:
                seg["type"] = "number"
                start_val = input("Start number [default: 0]: ").strip() or "0"
                if not start_val.isdigit():
                    print("❌ Invalid start number. Skipping this segment.")
                    continue
                seg["start"] = int(start_val)

                length_val = input("How many digits? (e.g., 4 → 0001): ").strip() or "4"
                if not length_val.isdigit():
                    print("❌ Invalid digit length. Skipping this segment.")
                    continue
                seg["length"] = int(length_val)

            elif idx == 2:
                seg["type"] = "letter"
                start_val = input("Starting letter index (0=A) [default: 0]: ").strip() or "0"
                if not start_val.isdigit():
                    print("❌ Invalid letter index. Skipping this segment.")
                    continue
                seg["start"] = int(start_val)

            elif idx == 3:
                seg["type"] = "hex"
                start_hex = input("Start hex value [default: 0]: ").strip() or "0"
                try:
                    int(start_hex, 16)
                except ValueError:
                    print("❌ Invalid hex value. Skipping this segment.")
                    continue
                seg["start"] = start_hex

                length_val = input("How many characters wide? (e.g., 4 → 00A3): ").strip() or "4"
                if not length_val.isdigit():
                    print("❌ Invalid hex length. Skipping this segment.")
                    continue
                seg["length"] = int(length_val)

            segments.append(seg)
            continue

        print("❌ Invalid action. Choose 's', 'd', 'v', or 'q'.")

    # Save the segments list into the schema
    noun_type.schema["autogenerate_segments"] = segments
    if segments:
        print("✅ Autogeneration format saved.")
    else:
        print("⚠️ No segments saved. Format chain is empty.")

def register_noun_type(project_path: Path, noun_name: str, noun_schema: dict):
    noun_file = project_path / "noun_types.json"
    noun_dir = project_path / "nouns" / noun_name
    noun_schema_file = noun_dir / f"{noun_name}.json"
    items_file = noun_dir / "items.jsonl"

    if noun_file.exists():
        with open(noun_file) as f:
            data = json.load(f)
    else:
        data = {}

    if noun_name in data:
        raise ValueError(f"Noun '{noun_name}' already exists")

    noun_type = NounType(noun_name, noun_schema, noun_file)
    noun_type.validate_field_structure()
    data[noun_name] = noun_schema

    with open(noun_file, "w") as f:
        json.dump(data, f, indent=2)

    noun_dir.mkdir(parents=True, exist_ok=True)
    with open(noun_schema_file, "w") as f:
        json.dump(noun_schema, f, indent=2)

    # ❌ DO NOT write [] to a JSONL file
    # ✅ Create an empty file without writing invalid JSON
    with open(items_file, "w") as f:
        pass

    print(f"✅ Registered noun: {noun_name}")
