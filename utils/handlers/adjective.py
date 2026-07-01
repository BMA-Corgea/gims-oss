# utils/handlers/adjective.py

from pathlib import Path
import json
from api.i_o import load_schema  # S3-aware *_types.json reader (replaces json.load(open(...)))
from utils import semantics as sem
from utils.word_registry import WordRegistry
from utils.interface import indexed_choice, menu_prompt
from typing import Optional, Dict

class BaseAdjective:
    def __init__(self, data, noun_type=None, verb_types=None, project_name=None):
        self.data = data
        self.noun_type = noun_type
        self.verb_names = list(verb_types.keys()) if verb_types else []
        self.project_name = project_name

    def interactive_configure(self):
        raise NotImplementedError("Each adjective class must implement interactive_configure().")

    def set_field(self, key: str, raw_input: str):
        # Default fallback: infer type (string, int, float, etc.)
        self.data[key] = sem.infer_type(raw_input)

    def demote_attribute(self):
        registry = WordRegistry(self.project_name)
        registry.enforce_disentanglement("adjective", self.data["adjective"])

        noun_file = Path("projects") / self.project_name / "noun_types.json"
        noun_data = sem.load_json(noun_file)
        noun_schema = noun_data.get(self.noun_type, {})
        fields = noun_schema.get("fields", {})

        attr = self.data["adjective"]
        old_info = fields.get(attr, {})
        # Build new field info, preserving metadata except adjective_class
        new_info = {"type": "string"}
        if old_info.get("required", False):
            new_info["required"] = True
        for k, v in old_info.items():
            if k not in ("type", "adjective_class", "required"):
                new_info[k] = v

        fields[attr] = new_info

        with open(noun_file, "w") as f:
            json.dump(noun_data, f, indent=2)

        print(f"🗑 Demoted adjective '{attr}' → plain attribute in noun '{self.noun_type}'")
        print(f"🔁 Field '{attr}' in noun '{self.noun_type}' demoted to plain attribute.")

    def prompt_filters(self, noun_type: str) -> dict:
        """
        Prompt user for filter conditions on the referenced noun.
        Returns a dict of {field_name: required_value}.
        """
        from utils.interface import indexed_choice
        noun_path = Path("projects") / self.project_name / "noun_types.json"
        if not noun_path.exists():
            print(f"❌ Could not find noun_types.json for project '{self.project_name}'")
            return {}

        noun_defs = sem.load_json(noun_path)
        if noun_type not in noun_defs:
            print(f"❌ Noun type '{noun_type}' not defined in noun_types.json")
            return {}

        schema = noun_defs[noun_type]
        fields = schema.get("fields", {})

        print(f"\n📌 Define filters to limit which '{noun_type}' items appear in the dropdown.")
        filters = {}

        field_names = list(fields.keys())
        while True:
            print("\nAvailable fields for filtering:")
            selected_index = indexed_choice(field_names, "Field to filter on")
            if selected_index is None:
                break

            selected_field = field_names[selected_index]
            val = input(f"Required value for '{selected_field}': ").strip()
            filters[selected_field] = val

        return filters

    def apply_filters_to_items(self, items: list[dict], filters: dict) -> list[dict]:
        """
        Given a list of item‐dicts and a filters dict, return only those that match.
        Simple equality check: item[field] == value.
        """
        if not filters:
            return items
        filtered = []
        for item in items:
            ok = True
            for fkey, fval in filters.items():
                # If the item lacks the field, or value doesn't match, skip it
                if str(item.get(fkey, "")).lower() != fval.lower():
                    ok = False
                    break
            if ok:
                filtered.append(item)
        return filtered

    def prompt_instance_edit(self, field_name, current_value):
        """
        Called by edit_instance.py to edit a single adjective field on an existing item.
        Each subclass overrides this if needed.
        """
        raise NotImplementedError("Subclasses must implement prompt_instance_edit()")

    def interactive_edit(self):
        protected = {
            "adjective", "adjective_class", "applies_to",
            "noun_type", "project_name", "verb_names"
        }
        keys = [k for k in self.data.keys() if k not in protected]
        if not keys:
            print(f"ℹ️ No editable fields for adjective: {self.data.get('adjective', '<unknown>')}")
            return False

        print(f"\n🧬 Editing adjective: {self.data.get('adjective', '<unknown>')}"
              f" (attached to noun: {self.noun_type})")
        for i, key in enumerate(keys):
            print(f"[{i}] {key}")
        choice = input("Select field index to edit (or 'q' to cancel): ").strip().lower()
        if choice == 'q':
            print("❎ Edit cancelled.")
            return False
        if not choice.isdigit() or not (0 <= int(choice) < len(keys)):
            print("❌ Invalid field selection.")
            return False

        field_key = keys[int(choice)]
        # Delegate to subclass‐specific editors if defined
        if field_key == "request_options" and hasattr(self, "edit_request_options"):
            self.edit_request_options()
            updated_val = self.data.get(field_key)
        elif field_key == "allowed_values" and hasattr(self, "edit_allowed_values"):
            self.edit_allowed_values()
            updated_val = self.data.get(field_key)
        else:
            newval = input(f"Enter new value for '{field_key}': ").strip()
            self.set_field(field_key, newval)

        print(f"✅ '{field_key}' updated to: {updated_val}")
        return True

    def interactive_register_from_context(self, noun_types: dict, verb_types: dict, existing_adjectives: list):
        from .adjective import get_adjective_class_handler  # avoid circular import

        noun_keys = list(noun_types.keys())

        # Step 1: Pick noun
        idx = indexed_choice(noun_keys, "Select a noun type (or 'q' to cancel)")
        if idx is None:
            return None
        self.noun_type = noun_keys[idx]

        # Step 2: Pick attribute to promote
        field_keys = list(noun_types[self.noun_type].get("fields", {}).keys())
        while True:
            idx = indexed_choice(field_keys, "Which attribute to promote? (or 'q' to cancel)")
            if idx is None:
                return None
            attribute_name = field_keys[idx]
            # Check if an adjective with this name already exists for this noun_type
            already_exists = False
            for adj in existing_adjectives:
                if adj["adjective"] == attribute_name and self.noun_type in adj.get("applies_to", []):
                    already_exists = True
                    break

            if already_exists:
                print(f"❌ Adjective for '{attribute_name}' already exists for noun '{self.noun_type}'.")
                continue
            self.data["adjective"] = attribute_name
            break

        # Step 3: Pick class

        # get the full handlers dict, then list its keys
        classes = list(get_adjective_class_handler().keys())
        idx = indexed_choice(classes, "Choose an adjective class (or 'q' to cancel)")
        if idx is None:
            return None
        adjective_class = classes[idx]

        # Step 4: Instantiate handler and configure
        self.data["adjective_class"] = adjective_class
        self.data["applies_to"] = [self.noun_type]
        handler_cls = get_adjective_class_handler(adjective_class)
        handler = handler_cls(
            self.data,
            noun_type=self.noun_type,
            verb_types=verb_types,
            project_name=self.project_name
        )
        handler.interactive_configure()

        # Update noun_types.json: mark field as adjective
        noun_file = Path("projects") / self.project_name / "noun_types.json"
        noun_data = sem.load_json(noun_file)
        fields = noun_data[self.noun_type].get("fields", {})
        existing = fields.get(self.data["adjective"], {})
        required = existing.get("required", False)

        fields[self.data["adjective"]] = {
            "type": "adjective",
            "adjective_class": adjective_class
        }
        if required:
            fields[self.data["adjective"]]["required"] = True

        noun_data[self.noun_type]["fields"] = fields
        with open(noun_file, "w") as f:
            json.dump(noun_data, f, indent=2)

        return handler


# ────────────────────────────────────────────────────────────────────────────

class ActionRequirementAdjective(BaseAdjective):
    def interactive_configure(self):
        print(f"\n🎯 Setting up `request_options` for adjective '{self.data['adjective']}'")
        self.edit_request_options()

    def set_field(self, key: str, raw_input: str):
        if key == "request_options":
            raise ValueError("`request_options` must be edited interactively.")
        self.data[key] = sem.infer_type(raw_input)

    def edit_request_options(self):
        current = self.data.get("request_options", {}) or {}
        print("\n📋 Format: one request per line → name: verb1, verb2, verb3")
        print(f"Available verbs: {', '.join(self.verb_names)}\n")

        while True:
            print("\nCurrent request_options:")
            if not current:
                print("  (none declared)")
            else:
                for i, (req, verbs) in enumerate(current.items()):
                    print(f"[{i}] {req}: {', '.join(verbs)}")

            action = menu_prompt({
                'a': 'add request',
                'e': 'edit request',
                'd': 'delete request',
                'q': 'quit'
            })
            if action == 'q':
                break
            if action == 'a':
                name = input("➕ New request name: ").strip()
                if name in current:
                    print("❌ Already exists.")
                    continue
                verbs = self._ask_verbs()
                if verbs:
                    current[name] = verbs
            elif action == 'e':
                reqs = list(current.keys())
                idx = indexed_choice(reqs, "Select request name to edit")
                if idx is None:
                    continue
                verbs = self._ask_verbs()
                if verbs:
                    current[reqs[idx]] = verbs
            elif action == 'd':
                reqs = list(current.keys())
                idx = indexed_choice(reqs, "Select request name to delete")
                if idx is None:
                    continue
                del current[reqs[idx]]

        self.data["request_options"] = current

    def _ask_verbs(self):
        line = input("Enter verbs (comma-separated): ").strip()
        verbs = [v.strip() for v in line.split(",") if v.strip()]
        for v in verbs:
            if v not in self.verb_names:
                print(f"❌ Invalid verb: '{v}'")
                return None
        return verbs

    def interactive_edit(self):
        print(f"\n🧬 Editing ActionRequirementAdjective: {self.data.get('adjective', '<unknown>')}")
        print("Current request_options:")
        self.edit_request_options()
        return True

    def prompt_instance_edit(self, field_name: str, current_value: str) -> str:
        options = list(self.data.get("request_options", {}).keys()) or []
        if not options:
            print("⚠️ No request_options defined.")
            return current_value

        idx = indexed_choice(options, f"Choose value for '{field_name}' (or 'q' to cancel)")
        return options[idx] if idx is not None else current_value

    def show_request_status(self, project_path: Path, instance_id: str):
        """
        For the given instance_id of the noun this adjective applies to,
        walk through each request label, call check_next_step, then evaluate_condition.
        """
        import json
        from utils.monitoring import check_next_step, evaluate_condition

        # Load noun schema & ensure this adjective is ActionRequirement
        noun_defs = load_schema(project_path, "noun")
        noun_schema = noun_defs[self.noun_type]
        if self.data["adjective_class"] != "ActionRequirement":
            raise RuntimeError(f"'{self.data['adjective']}' is not an ActionRequirement")

        # Load instance record
        items_path = project_path / "nouns" / self.noun_type / "items.jsonl"
        instance = None
        with open(items_path) as f:
            for line in f:
                obj = json.loads(line)
                if obj.get(noun_schema["primary_id_field"]) == instance_id:
                    instance = obj
                    break
        if not instance:
            print(f"No {self.noun_type} found with ID '{instance_id}'")
            return

        # Determine which request label was chosen
        request_label = instance.get(self.data["adjective"])
        verbs = self.data["request_options"].get(request_label, [])
        if not verbs:
            print(f"No verbs mapped for '{request_label}'")
            return

        print(f"\n{self.noun_type} {instance_id} requests: {request_label}\n")

        # Preload verb types
        verb_defs = load_schema(project_path, "verb")

        for verb in verbs:
            print(f"🔎 Verb: {verb}")
            try:
                steps = check_next_step(
                    project_path, self.noun_type, instance_id, verb
                )
            except Exception as e:
                print(f"  ❌ Error: {e}")
                continue

            # Get raw_inputs for evaluate_condition
            verb_def = verb_defs[verb]
            raw_inputs = (
                verb_def.get("data_entry_schema", {})
                        .get("raw_data_inputs", [])
            )
            for step in steps:
                linked = step["linked_id"]
                run_id = step["run_id"]
                print(f"  • Item {linked}: run {run_id or '(none)'}")
                if run_id:
                    vg = verb_def["verb_group"]
                    evaluate_condition(
                        project_path=project_path,
                        run_id=run_id,
                        verb_group=vg,
                        noun_schema=noun_schema,
                        raw_inputs=raw_inputs
                    )

class StateControlAdjective(BaseAdjective):
    def interactive_configure(self):
        allowed_values = []
        print(f"\n🧪 Define allowed values for `{self.data['adjective']}`")
        while True:
            val = input("Allowed value [enter to finish]: ").strip()
            if not val:
                break
            allowed_values.append(val)
        self.data["allowed_values"] = allowed_values

    def edit_allowed_values(self):
        current = self.data.get("allowed_values", []) or []
        while True:
            print("\nCurrent allowed_values:")
            if not current:
                print("  (none declared)")
            else:
                for i, val in enumerate(current):
                    print(f"[{i}] {val}")

            action = menu_prompt({
                'a': 'add value',
                'e': 'edit value',
                'd': 'delete value',
                'q': 'quit'
            })

            if action == 'q':
                break
            elif action == 'a':
                val = input("➕ New allowed value: ").strip()
                if val and val not in current:
                    current.append(val)
            elif action == 'e':
                idx = input("✏️ Index to edit: ").strip()
                if idx.isdigit() and 0 <= int(idx) < len(current):
                    new_val = input(f"New value for '{current[int(idx)]}': ").strip()
                    if new_val:
                        current[int(idx)] = new_val
                else:
                    print("❌ Invalid index.")
            elif action == 'd':
                idx = input("🗑 Index to delete: ").strip()
                if idx.isdigit() and 0 <= int(idx) < len(current):
                    current.pop(int(idx))
                else:
                    print("❌ Invalid index.")
            else:
                print("❌ Invalid action.")

        self.data["allowed_values"] = current

    def prompt_instance_edit(self, field_name: str, current_value: str) -> str:
        allowed = self.data.get("allowed_values", []) or []
        if not allowed:
            print("⚠️ No allowed_values defined.")
            return current_value

        idx = indexed_choice(allowed, f"Choose state for '{field_name}' (or 'q' to cancel)")
        return allowed[idx] if idx is not None else current_value


class TagAdjective(BaseAdjective):
    def interactive_configure(self):
        """Prompt user for definition and valid tag options."""
        desc = input("📖 Short definition (for hover/tooltips): ").strip()
        if desc:
            self.data["definition"] = desc

        self.edit_valid_options()

    def set_field(self, key: str, raw_input: str):
        if key in {"definition"}:
            self.data[key] = raw_input
        else:
            super().set_field(key, raw_input)

    def edit_valid_options(self):
        """Interactive menu to add/edit/delete tag options."""
        current = self.data.get("valid_options", []) or []

        while True:
            print("\nCurrent valid options:")
            if not current:
                print("  (none declared)")
            else:
                for i, opt in enumerate(current):
                    label = opt.get("value", "")
                    parts = []
                    expl = opt.get("explanation", "")
                    if expl:
                        parts.append(expl)
                    if opt.get("display_in_id"):
                        parts.append("display in ID")
                    extra = f" - {'; '.join(parts)}" if parts else ""
                    print(f"[{i}] {label}{extra}")

            action = menu_prompt({
                'a': 'add option',
                'e': 'edit option',
                'd': 'delete option',
                'q': 'quit'
            })

            if action == 'q':
                break
            if action == 'a':
                val = input("➕ Option value: ").strip()
                if not val or any(o.get('value') == val for o in current):
                    print("❌ Invalid or duplicate value.")
                    continue
                exp = input("📝 Add an explanation? ").strip()
                show = input("🏷️ Show in primary ID label? (y/n) ").strip().lower().startswith('y')
                current.append({
                    'value': val,
                    'explanation': exp,
                    'display_in_id': show
                })
            elif action == 'e':
                if not current:
                    print("❌ No options to edit.")
                    continue
                idx = input("✏️ Index to edit: ").strip()
                if not idx.isdigit() or int(idx) not in range(len(current)):
                    print("❌ Invalid index.")
                    continue
                opt = current[int(idx)]
                new_val = input(f"Value [{opt['value']}]: ").strip() or opt['value']
                new_exp = input(f"Explanation [{opt.get('explanation','')}] : ").strip()
                if new_exp == "" and opt.get('explanation'):
                    new_exp = opt['explanation']
                disp = input(f"Show in ID? (y/n) [{'y' if opt.get('display_in_id') else 'n'}]: ").strip().lower()
                if disp == '':
                    disp_flag = opt.get('display_in_id', False)
                else:
                    disp_flag = disp.startswith('y')
                opt.update({'value': new_val, 'explanation': new_exp, 'display_in_id': disp_flag})
            elif action == 'd':
                if not current:
                    print("❌ No options to delete.")
                    continue
                idx = input("🗑 Index to delete: ").strip()
                if idx.isdigit() and 0 <= int(idx) < len(current):
                    current.pop(int(idx))
                else:
                    print("❌ Invalid index.")

        self.data['valid_options'] = current

    def interactive_edit(self):
        print(f"\n🧬 Editing TagAdjective: {self.data.get('adjective', '<unknown>')}")
        action = menu_prompt({'d': 'edit definition', 'v': 'edit valid options', 'q': 'quit'})
        changed = False
        if action == 'd':
            newval = input("Enter new definition: ").strip()
            if newval:
                self.data["definition"] = newval
                print("✅ Definition updated.")
                changed = True
        elif action == 'v':
            self.edit_valid_options()
            changed = True
        else:
            print("❎ No change made.")
        return changed

    def prompt_instance_edit(self, field_name: str, current_value: str) -> str:
        options = [o.get('value') for o in self.data.get('valid_options', [])]
        if options:
            idx = indexed_choice(options, f"Choose value for '{field_name}' (or 'q' to cancel)")
            return options[idx] if idx is not None else current_value
        val = input(f"{field_name} (tag) [current: {current_value}]: ").strip()
        return val if val else current_value

    def get_display_suffix(self, value: str) -> str:
        """Return formatted suffix like ' (VALUE)' if display_in_id is True."""
        for opt in self.data.get("valid_options", []):
            if isinstance(opt, dict):
                if opt.get("value") == value and opt.get("display_in_id"):
                    return f" ({value})"
            elif isinstance(opt, str):
                if opt == value:
                    return f" ({value})"
        return ""

class ReferenceAdjective(BaseAdjective):
    def __init__(
        self,
        data: dict,
        noun_type: str = None,
        verb_types: dict | None = None,
        project_name: str | None = None
    ):
        super().__init__(
            data,
            noun_type=noun_type,
            verb_types=verb_types,
            project_name=project_name
        )

    def interactive_configure(self):
        _reference_noun = self.noun_type
        noun_file = Path("projects") / self.project_name / "noun_types.json"
        noun_data = sem.load_json(noun_file)
        noun_keys = list(noun_data.keys())

        idx = indexed_choice(noun_keys, "Select a noun type for reference")
        if idx is None:
            return
        self.data["reference_noun"] = noun_keys[idx]

        filters = self.prompt_filters(self.data["reference_noun"])
        self.data["filters"] = filters

    @staticmethod
    def prompt_uniqueness_flags() -> tuple[bool, bool]:
        print("🔧 Should values for this Reference field be unique?")
        print("  (1) No uniqueness enforcement")
        print("  (2) Unique per run only")
        print("  (3) Globally unique across all entries")
        while True:
            choice = input("Choose (1–3): ").strip()
            if choice == "1":
                return False, False
            if choice == "2":
                return True, False
            if choice == "3":
                return False, True

    def configure_uniqueness(self) -> None:
        up, ug = self.prompt_uniqueness_flags()
        self.data["unique_per_run"] = up
        self.data["unique_globally"] = ug

    def set_field(self, key: str, raw_input: str):
        # raw_input is expected to be a single valid ID (string or number)
        self.data[key] = sem.infer_type(raw_input)

    def interactive_edit(self):
        protected = {
            "adjective", "adjective_class", "applies_to",
            "noun_type", "project_name", "verb_names"
        }
        keys = [k for k in self.data.keys() if k not in protected]
        if not keys:
            print(f"ℹ️ No editable fields for adjective: {self.data.get('adjective', '<unknown>')}")
            return False

        print(f"\n🧬 Editing adjective: {self.data.get('adjective', '<unknown>')}" \
              f" (attached to noun: {self.noun_type})")
        for i, key in enumerate(keys):
            print(f"[{i}] {key}")
        choice = input("Select field index to edit (or 'q' to cancel): ").strip().lower()
        if choice == 'q':
            print("❎ Edit cancelled.")
            return False
        if not choice.isdigit() or not (0 <= int(choice) < len(keys)):
            print("❌ Invalid field selection.")
            return False

        field_key = keys[int(choice)]
        if field_key == "reference_noun":
            noun_file = Path("projects") / self.project_name / "noun_types.json"
            noun_data = sem.load_json(noun_file)
            noun_keys = list(noun_data.keys())
            idx = indexed_choice(noun_keys, "Select a noun type for reference")
            if idx is None:
                print("❎ Edit cancelled.")
                return False
            self.data["reference_noun"] = noun_keys[idx]
            updated_val = noun_keys[idx]
        elif field_key == "filters":
            filters = self.prompt_filters(self.data.get("reference_noun", ""))
            self.data["filters"] = filters
            updated_val = filters
        else:
            newval = input(f"Enter new value for '{field_key}': ").strip()
            self.set_field(field_key, newval)
            updated_val = self.data.get(field_key)

        print(f"✅ '{field_key}' updated to: {updated_val}")
        return True

    def prompt_instance_edit(self, field_name: str, current_value: str) -> str:
        """
        Present a list of existing reference values based on the noun’s
        current primary_id_field.
        """
        # 1) Locate project root and load noun_types.json
        project_root    = Path("projects") / self.project_name
        noun_types_file = project_root / "noun_types.json"
        noun_defs       = json.loads(noun_types_file.read_text())

        # 2) Figure out which noun we're referencing
        ref_noun = self.data.get("reference_noun") or self.noun_type
        primary  = noun_defs.get(ref_noun, {}).get("primary_id_field")
        if not primary:
            raise RuntimeError(f"No primary_id_field for referenced noun '{ref_noun}'")

        # 3) Load that noun’s items.jsonl and collect values under the primary field
        items_file = project_root / "nouns" / ref_noun / "items.jsonl"
        options: list[str] = []
        if items_file.exists():
            for line in items_file.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                val = rec.get(primary)
                if val is not None:
                    options.append(str(val))

        # 4) Let the user pick one
        idx = indexed_choice(options, f"Choose value for '{field_name}' (or 'q' to cancel)")
        return options[idx]

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        """Validate uniqueness of reference values across entries and globally."""
        # Make sure self.data is still the adjective config
        if not isinstance(self.data, dict):
            raise TypeError(f"Expected self.data to be a dict, got {type(self.data).__name__}")

        field = self.data.get("adjective")
        if not field:
            return []

        errors: list[str] = []

        # Gather all values for this field in the current entries
        values = [entry.get(field) for entry in entries if entry.get(field)]

        # Check for duplicates within the current run
        if self.data.get("unique_per_run"):
            seen = set()
            for v in values:
                if v in seen:
                    errors.append(f"Reference '{v}' is not unique within the current run.")
                seen.add(v)

        # Check for duplicates globally in the items.jsonl file
        if self.data.get("unique_globally") and context:
            noun_type = context.get("noun_type")
            project_path: Path = context.get("project_path")

            if noun_type and project_path:
                items_path = project_path / "nouns" / noun_type / "items.jsonl"
                existing_refs = set()

                if items_path.exists():
                    with open(items_path, 'r') as f:
                        for line in f:
                            try:
                                item = json.loads(line.strip())
                                ref_value = item.get(field)
                                if ref_value is not None:
                                    existing_refs.add(ref_value)
                            except json.JSONDecodeError:
                                continue

                for v in values:
                    if v in existing_refs:
                        errors.append(f"Reference '{v}' is already used globally.")
                    elif values.count(v) > 1:
                        errors.append(f"Reference '{v}' is repeated in the current entry set.")

        return errors

class ReferenceListAdjective(BaseAdjective):
    def __init__(
        self,
        data: dict,
        noun_type: str = None,
        verb_types: dict | None = None,
        project_name: str | None = None
    ):
        super().__init__(
            data,
            noun_type=noun_type,
            verb_types=verb_types,
            project_name=project_name
        )

    def interactive_configure(self):
        noun_file = Path("projects") / self.project_name / "noun_types.json"
        noun_data = sem.load_json(noun_file)
        noun_keys = list(noun_data.keys())

        idx = indexed_choice(noun_keys, "Select a noun type for multi-reference")
        if idx is None:
            return
        self.data["reference_noun"] = noun_keys[idx]

        filters = self.prompt_filters(self.data["reference_noun"])
        self.data["filters"] = filters

    def set_field(self, key: str, raw_input: str):
        # raw_input is expected to be a list-like string (e.g., "id1,id2")
        values = [v.strip() for v in raw_input.split(",") if v.strip()]
        self.data[key] = [sem.infer_type(v) for v in values]

    def prompt_instance_edit(self, field_name: str, current_value: list) -> list:
        ref_noun = self.data.get("reference_noun")
        items_file = Path("projects") / self.project_name / "nouns" / ref_noun / "items.jsonl"
        if not items_file.exists():
            print(f"⚠️ No items found for referenced noun '{ref_noun}'.")
            return current_value

        with open(items_file) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        if not entries:
            print(f"⚠️ No entries in items for '{ref_noun}'.")
            return current_value

        # Apply filters
        filters = self.data.get("filters", {})
        entries = self.apply_filters_to_items(entries, filters)

        id_key = next((k for k in entries[0].keys() if k.endswith("_id")), None)
        if not id_key:
            id_key = next(iter(entries[0].keys()))

        names = [str(e.get(id_key, "")) for e in entries]
        selected = []
        print(f"\n📚 Select one or more values for '{field_name}' (enter index repeatedly, 'q' to finish):")
        while True:
            idx = indexed_choice(names, "Pick next (or 'q' to finish)")
            if idx is None:
                break
            choice = names[idx]
            if choice not in selected:
                selected.append(choice)

        return selected if selected else current_value

def get_adjective_class_handler(adjective_class: str | None = None):
    handlers = {
        "ActionRequirement": ActionRequirementAdjective,
        "StateControl":       StateControlAdjective,
        "Tag":                TagAdjective,
        "Reference":          ReferenceAdjective,
        "ReferenceList":      ReferenceListAdjective,
        "Picture":            PictureAdjective
    }
    if adjective_class is None:
        # no arg → return the full map
        return handlers
    return handlers.get(adjective_class, BaseAdjective)

def load_adjective_handler(
    project_path: Path,
    noun_type: str,
    field_name: str,
    verb_types: Optional[Dict[str, dict]] = None
):
    # Shape-tolerant read: works whether adjective_types.json is still a list or has been
    # migrated to a name-keyed dict (the migration shim re-expands it to the legacy list).
    from core.words.reader import load_descriptor_list
    adjectives = load_descriptor_list(project_path, "adjective")

    entry = next(
        (
            a for a in adjectives
            if a.get("adjective") == field_name
            and noun_type in a.get("applies_to", [])
        ),
        None
    )
    if entry is None:
        raise KeyError(f"No adjective '{field_name}' for noun '{noun_type}'")

    handler_cls = get_adjective_class_handler(entry["adjective_class"])
    return handler_cls(
        entry,                            # ← must be the full dict
        noun_type=noun_type,             # ← which noun this applies to
        verb_types=verb_types or {},     # ← your verb types map
        project_name=project_path.name   # ← so handler can find its project
    )

def infer_type(value: str):
    try:
        if value.lower() in ['true', 'false']:
            return value.lower() == 'true'
        elif '.' in value:
            return float(value)
        else:
            return int(value)
    except ValueError:
        return value.strip()

class PictureAdjective(BaseAdjective):
    def __init__(
        self,
        data: dict,
        noun_type: str | None = None,
        verb_types: dict | None = None,
        project_name: str | None = None
    ):
        super().__init__(
            data,
            noun_type=noun_type,
            verb_types=verb_types,
            project_name=project_name
        )

    def interactive_configure(self):
        # No complex config needed for images by default.
        pass

    def set_field(self, key: str, raw_input: str):
        # In normal adjective classes, raw_input comes from CLI input.
        # Here, call your upload/capture subroutine instead of accepting a manual path.
        img_path = capture_image_for_entity(  # noqa: F821  # legacy CLI: capture_image_for_entity was never ported to this layer
            project_name=self.project_name,
            noun_type=self.noun_type,
            noun_id=self.data.get("entity_id"),
            adjective_name=key
        )
        self.data[key] = img_path

    def prompt_instance_edit(self, field_name: str, current_value: str) -> str:
        # Option to replace existing image
        choice = menu_prompt({
            "u": "Upload new image",
            "k": "Keep current"
        })
        if choice == "u":
            img_path = capture_image_for_entity(  # noqa: F821  # legacy CLI: capture_image_for_entity was never ported to this layer
                project_name=self.project_name,
                noun_type=self.noun_type,
                noun_id=self.data.get("entity_id"),
                adjective_name=field_name
            )
            return img_path
        return current_value