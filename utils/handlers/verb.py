import json
from pathlib import Path
from api.i_o import load_schema  # S3-aware *_types.json reader (replaces json.load(open(...)))
from utils.interface import menu_prompt, indexed_choice
from utils.handlers.conjunction import (
    prompt_status_overrides
)
from utils.file_ops import upload_parser_script

class VerbType:
    def __init__(self, verb_key: str, project_name: str):
        self.verb_key = verb_key
        self.project_name = project_name
        self.data = {
            "verb_name": verb_key,
            "verb_group": None,
            "description": "",
            "status_values": [],
            "data_entry_schema": {},
            "adverb_schema": {}
        }
        self.verb_path = Path("projects") / project_name / "verb_types.json"

    def interactive_register_from_context(self) -> bool:
        # Load or initialize existing verb_types.json
        if self.verb_path.exists():
            with open(self.verb_path) as f:
                existing_verbs = json.load(f)
        else:
            existing_verbs = {}

        # Step 1: Choose or create verb group
        all_groups = sorted(
            {v.get("verb_group") for v in existing_verbs.values() if v.get("verb_group")}
        )
        group_idx = indexed_choice(all_groups + ["➕ Create new group"], "Select a verb group")
        if group_idx is None:
            return False
        if group_idx == len(all_groups):
            self.data["verb_group"] = input("🆕 New verb group name: ").strip()
        else:
            self.data["verb_group"] = all_groups[group_idx]

        # Step 2: Description
        self.data["description"] = input("📝 Description of this verb: ").strip()

        # Step 3: Statuses
        self.data["status_values"] = prompt_status_overrides(existing_overrides=None, project_name=self.project_name)

        # Step 4: File Management — ensure folders, log, and config
        self.ensure_group_folders_and_log()

        # Step 5: Offer data-entry and adverb schema definition
        while True:
            action = menu_prompt({
                'd': 'define data entry schema',
                'a': 'define adverb schema',
                's': 'save and exit',
                'q': 'quit without saving'
            })

            if action == 'q':
                print("❌ Cancelled.")
                return False
            elif action == 'd':
                self.configure_data_entry_schema()
            elif action == 'a':
                self.configure_adverb_schema()
            elif action == 's':
                set_up_inputs = self.data.get("data_entry_schema", {}).get("set_up_inputs")
                if not isinstance(set_up_inputs, dict) or not set_up_inputs.get("noun_type_ref"):
                    print("❌ You must define setup inputs with a valid noun_type_ref before saving.")
                    self.configure_data_entry_schema()
                else:
                    break

        # Persist new verb definition
        existing_verbs[self.verb_key] = self.data
        with open(self.verb_path, 'w') as f:
            json.dump(existing_verbs, f, indent=2)
        print(f"✅ Verb '{self.verb_key}' registered.")
        return True

    def configure_data_entry_schema(self):
        """
        Build or edit self.data["data_entry_schema"] using a menu-driven, indexed interface.
        """
        schema = self.data.setdefault("data_entry_schema", {})

        while True:
            print("\n🔧 Data Entry Schema Configuration")

            options = {
                "i": "instructions: edit instructions",
                "r": "raw data inputs: edit raw_data_inputs",
                "s": "set up inputs: edit set_up_inputs.fields",
                "t": "interpretation: edit interpretation settings",
                "q": "quit to main menu"
            }

            choice = menu_prompt(options)

            if choice == 'q':
                break
            elif choice == 'i':
                self._configure_instructions(schema)
            elif choice == 'r':
                self._configure_raw_data_inputs(schema)
            elif choice == 's':
                self._configure_set_up_inputs(schema)
            elif choice == 't':
                self._configure_interpretation(schema)

    def _configure_instructions(self, schema: dict):
        """
        Let user edit the "instructions" list of strings.
        """
        instr = schema.setdefault("instructions", [])
        while True:
            print("\n📋 Current Instructions:")
            if not instr:
                print("  (none yet)")
            else:
                for idx, line in enumerate(instr):
                    print(f"[{idx}] {line}")
            action = menu_prompt({
                'a': 'add instruction line',
                'e': 'edit instruction line',
                'd': 'delete instruction line',
                'q': 'finish instructions'
            })
            if action == 'q':
                break
            elif action == 'a':
                new_line = input("➕ Enter new instruction: ").strip()
                if new_line:
                    instr.append(new_line)
            elif action == 'e':
                if not instr:
                    print("❌ No lines to edit.")
                    continue
                idx = indexed_choice(instr, "Select line to edit")
                if idx is None:
                    continue
                new_line = input(f"✏️ New text for instruction [{idx}]: ").strip()
                if new_line:
                    instr[idx] = new_line
            elif action == 'd':
                if not instr:
                    print("❌ No lines to delete.")
                    continue
                idx = indexed_choice(instr, "Select line to delete")
                if idx is None:
                    continue
                instr.pop(idx)
            else:
                print("❌ Invalid action.")

    def _configure_raw_data_inputs(self, schema: dict):
        """
        Let user define the list of raw_data_inputs (e.g. ["Labels", "Data", …]).
        """
        raw_inputs = schema.setdefault("raw_data_inputs", [])
        while True:
            print("\n📂 Current raw_data_inputs:")
            if not raw_inputs:
                print("  (none yet)")
            else:
                for i, name in enumerate(raw_inputs):
                    print(f"[{i}] {name}")
            action = menu_prompt({
                'a': 'add raw data tab',
                'e': 'edit raw data tab',
                'd': 'delete raw data tab',
                'q': 'finish raw_data_inputs'
            })
            if action == 'q':
                break
            elif action == 'a':
                new_tab = input("➕ Enter new raw data tab name: ").strip()
                if new_tab and new_tab not in raw_inputs:
                    raw_inputs.append(new_tab)
            elif action == 'e':
                if not raw_inputs:
                    print("❌ No tabs to edit.")
                    continue
                idx = indexed_choice(raw_inputs, "Select tab to edit")
                if idx is None:
                    continue
                new_tab = input(f"✏️ New name for tab [{raw_inputs[idx]}]: ").strip()
                if new_tab:
                    raw_inputs[idx] = new_tab
            elif action == 'd':
                if not raw_inputs:
                    print("❌ No tabs to delete.")
                    continue
                idx = indexed_choice(raw_inputs, "Select tab to delete")
                if idx is None:
                    continue
                raw_inputs.pop(idx)
            else:
                print("❌ Invalid action.")

    def _configure_set_up_inputs(self, schema: dict):
        """
        Build schema["set_up_inputs"]: you must pick an existing noun type
        that defines at least one Reference or ReferenceList adjective field.
        """
        su_inputs = schema.setdefault("set_up_inputs", {})

        # Load noun types
        noun_types_path = Path("projects") / self.project_name / "noun_types.json"
        if noun_types_path.exists():
            with open(noun_types_path) as f:
                all_noun_defs = json.load(f)
        else:
            all_noun_defs = {}

        # Only include noun types whose schema.fields contains
        # at least one adjective with adjective_class Reference or ReferenceList
        valid_noun_types = sorted(
            name
            for name, noun_schema in all_noun_defs.items()
            if any(
                field_props.get("type") == "adjective"
                and field_props.get("adjective_class") in ("Reference", "ReferenceList")
                for field_props in noun_schema.get("fields", {}).values()
            )
        )

        while True:
            print("\n⚙️ Set Up Inputs Configuration:")
            if "noun_type_ref" in su_inputs:
                print(f"🔁 Reusing noun type: {su_inputs['noun_type_ref']}")

            action = menu_prompt({
                'r': 'reference an eligible noun type',
                'x': 'clear set_up_inputs',
                'q': 'finish'
            })

            if action == 'r':
                if not valid_noun_types:
                    print("❌ No noun types with Reference‐type adjectives defined in this project.")
                    continue

                print("ℹ️ Select a noun type that has a Reference or ReferenceList adjective.")
                idx = indexed_choice(valid_noun_types, "Choose noun type to reuse")
                if idx is not None:
                    su_inputs.clear()
                    su_inputs["noun_type_ref"] = valid_noun_types[idx]

            elif action == 'x':
                su_inputs.clear()

            elif action == 'q':
                if "noun_type_ref" not in su_inputs:
                    print("❌ You must select a noun type to reference. This verb must be tied to a noun.")
                    continue
                break

            else:
                print("❌ Invalid action.")

    def _configure_interpretation(self, schema: dict):
        """
        Configure interpretation method, output tabs, and Docker parser runners.
        Stores to:
        schema["interpretation"] = {
            "method": "parsed" | "uploaded",
            "tabs": [list of tab names],
            "parsers": [ordered list of docker folder names]
        }
        """
        interp = schema.setdefault("interpretation", {})

        # --- 1. Select method ---
        methods = ["parsed", "uploaded"]
        m_idx = indexed_choice(methods, "Select interpretation mode")
        if m_idx is None:
            return
        interp["method"] = methods[m_idx]

        # --- 2. Configure output tabs ---
        tabs = interp.setdefault("tabs", [])
        while True:
            print("\n📑 Current interpretation tabs:")
            if not tabs:
                print("  (none yet)")
            else:
                for i, t in enumerate(tabs):
                    print(f"[{i}] {t}")
            action = menu_prompt({
                'a': 'add tab',
                'e': 'edit tab',
                'd': 'delete tab',
                'q': 'done editing tabs'
            })
            if action == 'q':
                break
            elif action == 'a':
                new_tab = input("➕ New tab name: ").strip()
                if new_tab and new_tab not in tabs:
                    tabs.append(new_tab)
            elif action == 'e':
                if not tabs:
                    print("❌ No tabs to edit.")
                    continue
                idx = indexed_choice(tabs, "Select tab to rename")
                if idx is not None:
                    new_name = input(f"✏️ Rename '{tabs[idx]}' to: ").strip()
                    if new_name:
                        tabs[idx] = new_name
            elif action == 'd':
                if not tabs:
                    print("❌ No tabs to delete.")
                    continue
                idx = indexed_choice(tabs, "Select tab to delete")
                if idx is not None:
                    tabs.pop(idx)

        # --- 3. Configure parser sequence if method == "parsed" ---
        if interp["method"] == "parsed":
            parser_list = interp.setdefault("parsers", [])

            docker_dir = Path(__file__).resolve().parents[2] / "docker" / "Parsers"
            docker_dir.mkdir(parents=True, exist_ok=True)

            while True:
                print("\n🤖 Current parser execution order:")
                if not parser_list:
                    print("  (none yet)")
                else:
                    for i, p in enumerate(parser_list):
                        print(f"[{i}] {p}")

                action = menu_prompt({
                    'a': 'add existing parser',
                    'u': 'upload new parser',
                    'm': 'move parser',
                    'd': 'delete parser',
                    'q': 'done editing parsers'
                })
                if action == 'q':
                    break

                elif action == 'a':
                    existing = sorted([
                        f.name for f in docker_dir.iterdir()
                        if f.is_dir() and any(p.suffix == ".py" and p.name != "entrypoint.py" for p in f.glob("*.py"))
                    ])
                    if not existing:
                        print("❌ No parser folders found in docker/Parsers/")
                        continue

                    idx = indexed_choice(existing, "Select parser to add")
                    if idx is not None:
                        parser_name = existing[idx]
                        if parser_name not in parser_list:
                            parser_list.append(parser_name)

                elif action == 'u':
                    parser_name = upload_parser_script(docker_dir)
                    if parser_name and parser_name not in parser_list:
                        parser_list.append(parser_name)

                elif action == 'm':
                    if len(parser_list) < 2:
                        print("⚠️ Not enough parsers to reorder.")
                        continue
                    idx = indexed_choice(parser_list, "Select parser to move")
                    if idx is None:
                        continue
                    new_pos = indexed_choice(parser_list, f"Move '{parser_list[idx]}' to position")
                    if new_pos is not None and new_pos != idx:
                        parser = parser_list.pop(idx)
                        parser_list.insert(new_pos, parser)

                elif action == 'd':
                    idx = indexed_choice(parser_list, "Select parser to delete")
                    if idx is not None:
                        parser_list.pop(idx)

                else:
                    print("❌ Invalid action.")

    def configure_adverb_schema(self):
        """Interactively build ``adverb_schema`` entries.

        The implementation is intentionally minimal.  For each adverb the user
        is prompted for a reference noun type.  If one is supplied the entry
        will contain ``{"reference_noun": <type>}``; otherwise it defaults to a
        simple string field.  ``self.data['adverb_schema']`` is created if
        needed so repeated calls remain safe.
        """
        # shorthand for the verb's name
        verb_name = self.verb_key

        # In-memory schema
        adv_schema = self.data.setdefault("adverb_schema", {})

        # Path to the adverb index file
        proj_dir = Path("projects") / self.project_name
        adv_file = proj_dir / "adverb_types.json"
        adv_file.parent.mkdir(parents=True, exist_ok=True)

        # Load existing entries via the back-compat reader (tolerates legacy list OR migrated
        # keyed dict, Phase 3) so a migrated file isn't silently treated as empty on re-edit.
        from core.words.reader import load_descriptor_list
        all_adverbs = load_descriptor_list(proj_dir, "adverb")

        # Prompt loop
        self.data.setdefault("adverb_schema", {})
        while True:
            name = input("Adverb name (or blank to finish): ").strip()
            if not name:
                break

            # Reference noun? (blank → freeform string)
            ref = input(f"  Reference noun for '{name}' (blank → string): ").strip()
            cfg = {"reference_noun": ref} if ref else {"type": "string"}
            adv_schema[name] = cfg

            # Remove any prior definition for this verb+adverb
            all_adverbs = [
                e for e in all_adverbs
                if not (e.get("verb") == verb_name and e.get("adverb") == name)
            ]

            # Append the new, full entry
            entry = {"verb": verb_name, "adverb": name, **cfg}
            all_adverbs.append(entry)

        # Persist adverb_types.json
        adv_file.write_text(json.dumps(all_adverbs, indent=2))

        # pull in the external editor for status overrides
        existing = self.data.get("status_values", [])
        overrides = prompt_status_overrides(existing, self.project_name)

        # finally write back
        self.data['status_values'] = overrides

    def ensure_group_folders_and_log(self):
        verb_group_folder = Path("projects") / self.project_name / "verbs" / self.data["verb_group"]
        data_dumps_folder = verb_group_folder / "data_dumps"
        log_file = verb_group_folder / f"{self.data['verb_group']}_log.jsonl"

        verb_group_folder.mkdir(parents=True, exist_ok=True)
        data_dumps_folder.mkdir(parents=True, exist_ok=True)
        if not log_file.exists():
            log_file.touch()

        self.create_log_config_if_missing(verb_group_folder, self.data["verb_group"])

    def create_log_config_if_missing(self, group_path: Path, group_name: str):
        """
        Build <group>_log_config.json if it’s missing (unchanged from before).
        """
        config_path = group_path / f"{group_name}_log_config.json"
        if config_path.exists():
            return
        print(f"\n🧪 Creating log config for verb group '{group_name}'")
        schema = self.prompt_log_schema_fields_with_primary()
        with open(config_path, 'w') as f:
            json.dump(schema, f, indent=2)

    def prompt_log_schema_fields_with_primary(self, existing: dict = None) -> dict:
        """
        Build or edit the log schema (fields + primary_id). Unchanged from before,
        except slight renaming to align style.
        """
        schema = existing.get("fields", {}) if existing else {}
        primary_id = existing.get("primary_id") if existing else None

        while True:
            print("\n📋 Current Fields:")
            if not schema:
                print("  (none yet)")
            else:
                for i, (field, cfg) in enumerate(schema.items()):
                    req = "required" if cfg.get("required") else "optional"
                    print(f"[{i}] {field} ({cfg['type']}, {req})")

            action = menu_prompt({
                'a': 'add field',
                'e': 'edit field',
                'd': 'delete field',
                'q': 'finish and select primary ID'
            })

            if action == 'q':
                if not schema:
                    print("❌ You must define at least one field.")
                    continue
                break

            elif action == 'a':
                field = input("➕ Field name: ").strip()
                if field in schema:
                    print("❌ Already exists.")
                    continue
                valid_types = ['string', 'int', 'float', 'date']
                idx = indexed_choice(valid_types, "Field type")
                if idx is None:
                    print("❌ Cancelled.")
                    continue
                ftype = valid_types[idx]
                required = input("Required? (y/n): ").strip().lower() == 'y'
                schema[field] = {"type": ftype, "required": required}

            elif action == 'e':
                if not schema:
                    print("❌ No fields to edit.")
                    continue
                idx_input = input("✏️ Index of field to edit: ").strip()
                if not idx_input.isdigit() or not (0 <= int(idx_input) < len(schema)):
                    print("❌ Invalid index.")
                    continue
                field = list(schema.keys())[int(idx_input)]
                valid_types = ['string', 'int', 'float', 'date']
                type_idx = indexed_choice(valid_types, "Field type")
                if type_idx is None:
                    print("❌ Cancelled.")
                    continue
                ftype = valid_types[type_idx]
                required = input("Required? (y/n): ").strip().lower() == 'y'
                schema[field] = {"type": ftype, "required": required}

            elif action == 'd':
                if not schema:
                    print("❌ No fields to delete.")
                    continue
                idx_input = input("🗑 Index of field to delete: ").strip()
                if not idx_input.isdigit() or not (0 <= int(idx_input) < len(schema)):
                    print("❌ Invalid index.")
                    continue
                fld = list(schema.keys())[int(idx_input)]
                del schema[fld]

            else:
                print("❌ Invalid action.")

        # Prompt for primary ID
        field_names = list(schema.keys())
        print("\n🔑 Select the primary ID field (must be unique per run):")
        idx = indexed_choice(field_names, "Primary ID field")
        if idx is None:
            return existing or {}
        primary_id = field_names[idx]

        return {
            "primary_id": primary_id,
            "fields": schema
        }

    def edit_verb_group(self):
        current_group = self.data.get("verb_group")
        print(f"\n🧪 Current verb group: {current_group}")
        choice = indexed_choice([
            "Change which group this verb belongs to",
            "Edit the group's log schema"
        ], "Verb group action")

        if choice is None:
            return

        group_path = Path("projects") / self.project_name / "verbs" / current_group

        if choice == 0:
            existing_groups = {
                v.get("verb_group")
                for v in load_schema(self.verb_path.parent, "verb").values()
                if v.get("verb_group")
            }
            existing_groups.discard(current_group)
            options = sorted(existing_groups) + ["➕ Create new group"]
            idx = indexed_choice(options, "Select new group")
            if idx is None:
                return
            if idx == len(options):
                new_group = input("🆕 New group name: ").strip()
            else:
                new_group = options[idx]
            self.data["verb_group"] = new_group
            self.ensure_group_folders_and_log()
            print(f"✅ Verb reassigned to group '{new_group}'.")

        elif choice == 1:
            config_path = group_path / f"{current_group}_log_config.json"
            if not config_path.exists():
                print("❌ No log config found for this group.")
                return
            with open(config_path) as f:
                config = json.load(f)

            print(f"\n📋 Current schema for '{current_group}':")
            fields = config.get("fields", {})
            for k, v in fields.items():
                print(f" - {k} ({v['type']}, {'required' if v.get('required') else 'optional'})")

            updated = self.prompt_log_schema_fields_with_primary(config)
            with open(config_path, "w") as f:
                json.dump(updated, f, indent=2)
            print(f"✅ Updated log config for group '{current_group}'.")

    @classmethod
    def edit_existing(cls, project_name: str):
        verb_path = Path("projects") / project_name / "verb_types.json"
        if not verb_path.exists():
            print("❌ No verbs defined yet.")
            return
        with open(verb_path) as f:
            verbs = json.load(f)

        keys = list(verbs.keys())
        idx = indexed_choice(keys, "Select a verb to edit")
        if idx is None:
            return

        key = keys[idx]
        instance = cls(key, project_name)
        instance.data = verbs[key]

        while True:
            action = menu_prompt({
                'd': 'edit description',
                's': 'edit statuses',
                'e': 'edit data schema',
                'a': 'edit adverb schema',
                'v': 'edit verb group',
                'q': 'quit and save'
            })

            if action == 'q':
                break
            elif action == 'd':
                instance.data['description'] = input("📝 New description: ").strip()
            elif action == 's':
                # ---- “s” branch: edit status_overrides ----
                existing = instance.data.get('status_values', [])
                new_list = prompt_status_overrides(existing, project_name)
                instance.data['status_values'] = new_list
            elif action == 'e':
                instance.configure_data_entry_schema()
            elif action == 'a':
                instance.configure_adverb_schema()
            elif action == 'v':
                instance.edit_verb_group()

        verbs[key] = instance.data
        with open(verb_path, 'w') as f:
            json.dump(verbs, f, indent=2)
        print(f"✅ Verb '{key}' updated.")

def load_full_verb_def(project_path: Path, verb_key: str) -> dict:
    """
    Loads the entire verb definition (not just data_entry_schema)
    from verb_types.json.
    """
    with open(project_path / "verb_types.json") as vf:
        all_verbs = json.load(vf)
    return all_verbs.get(verb_key, {})