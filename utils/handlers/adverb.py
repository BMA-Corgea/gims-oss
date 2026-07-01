from __future__ import annotations
import json
from pathlib import Path

from utils.interface import indexed_choice, menu_prompt
from utils import semantics as sem
from utils.logger import get_logger

log = get_logger(__name__)

class BaseAdverb:
    """
    Base class for all adverb handlers.
    __init__ supports:
      - BaseAdverb()                        # no context
      - BaseAdverb(project_name, field_name, config_dict)
      - BaseAdverb(project_name, field_name)   # config_dict defaults to {}
    """
    def __init__(self, *args):
        # (project_name, field_name, [config])
        if len(args) == 3:
            self.project_name, self.field_name, cfg = args
        elif len(args) == 2:
            self.project_name, self.field_name = args
            cfg = {}
        elif len(args) == 0:
            self.project_name = None
            self.field_name   = None
            cfg = {}
        else:
            raise TypeError(f"{self.__class__.__name__}.__init__() takes 0, 2 or 3 args ({len(args)} given)")

        # the dict of settings for this adverb
        self.config = cfg or {}
        # alias for tests that expect .data
        self.data = self.config

    def prompt_required_flag(self):
        """Universal prompt to mark an adverb as required or not."""
        choice = input("Should this adverb be required? (Y/N): ").strip().lower()
        self.config["required"] = True if choice == "y" else False

    def prompt_filters(self, noun_type: str) -> dict:
        """
        Prompt the user for filter conditions on a given noun type.
        Returns a dict[field_name → required_value].
        """
        noun_path = Path("projects") / self.project_name / "noun_types.json"
        if not noun_path.exists():
            print(f"❌ Could not find {noun_path}")
            return {}

        try:
            noun_defs = json.loads(noun_path.read_text())
        except Exception:
            print(f"❌ Error reading {noun_path}")
            return {}

        if noun_type not in noun_defs:
            print(f"❌ Noun '{noun_type}' not defined.")
            return {}

        fields = list(noun_defs[noun_type].get("fields", {}).keys())
        if not fields:
            print(f"ℹ️ No fields to filter on for '{noun_type}'.")
            return {}

        print(f"\n📌 Define filters to limit which '{noun_type}' items appear:")
        filters: dict[str,str] = {}
        while True:
            idx = indexed_choice(fields, "Field to filter on (or 'q' to finish)")
            if idx is None:
                break
            key = fields[idx]
            val = input(f"Required value for '{key}': ").strip()
            filters[key] = val
        return filters

    def list_available_verbs(project_path: Path) -> list[str]:
        vt_file = project_path / "verb_types.json"
        if not vt_file.exists():
            return []
        return list(json.loads(vt_file.read_text()).keys())

    @staticmethod
    def save_to_adverb_types(project_path: Path, entry: dict):
        """
        Append or update an entry in projects/.../adverb_types.json.
        Ensures that 'adverb_class' is always present.
        """
        path = project_path / "adverb_types.json"
        path.parent.mkdir(parents=True, exist_ok=True)

        # Guarantee the adverb_class key
        if "adverb_class" not in entry:
            # fallback: use class name if given
            entry["adverb_class"] = entry.get("_class_name") or entry.get("adverb_class", "Attribute")

        # Load through the back-compat reader so existing entries survive whether the file is
        # still a legacy list OR the migrated name-keyed dict (Phase 3). The old
        # `if not isinstance(arr, list): arr = []` guard silently DROPPED every entry on a
        # migrated dict file — a data-loss bug this read avoids.
        from core.words.reader import load_descriptor_list
        arr = load_descriptor_list(project_path, "adverb")

        # remove any old for same verb+adverb
        arr = [e for e in arr
               if not (e.get("verb") == entry["verb"]
                       and e.get("adverb") == entry["adverb"])]
        arr.append(entry)

        path.write_text(json.dumps(arr, indent=2))

    def inject_into_verb_schema(project_path: Path,
                                verb_key: str,
                                adverb: str,
                                config: dict):
        """
        Add or update verb_types.json → [verb_key].adverb_schema.[adverb] = config
        """
        vt_path = project_path / "verb_types.json"
        vt = {}
        if vt_path.exists():
            vt = json.loads(vt_path.read_text())

        verb_cfg = vt.setdefault(verb_key, {})
        ad_schema = verb_cfg.setdefault("adverb_schema", {})
        ad_schema[adverb] = config

        vt_path.write_text(json.dumps(vt, indent=2))

    def interactive_register_from_context(self,
                                          verb_types: dict[str,dict],
                                          existing_adverbs: list[dict]) -> BaseAdverb | None:
        project_path = Path("projects") / self.project_name

        # 1) Name
        name = input("Adverb name: ").strip()
        if not name:
            return None
        self.field_name = name

        # 2) Pick a verb
        verbs = list(verb_types.keys())
        vidx  = indexed_choice(verbs, "Attach to which verb?")
        if vidx is None:
            return None
        verb_key = verbs[vidx]

        # 3) Pick a class & instantiate handler
        cls_name = list(CLASS_MAP.keys())[indexed_choice(list(CLASS_MAP.keys()), "Select adverb class")]
        handler  = CLASS_MAP[cls_name](self.project_name, name, {})

        # 4) Delegate all class-specific prompts
        handler.interactive_configure()

        # 5) Persist global adverb_types.json, including the class name
        entry = {
            "adverb": name,
            "verb": verb_key,
            "adverb_class": cls_name,    # ← explicitly include class
            **handler.config
        }

        at_file = project_path / "adverb_types.json"
        at_file.parent.mkdir(parents=True, exist_ok=True)
        # Read via the back-compat reader (tolerates legacy list OR migrated keyed dict, Phase 3).
        from core.words.reader import load_descriptor_list
        arr = load_descriptor_list(project_path, "adverb")
        arr = [e for e in arr if not (
            e["verb"] == verb_key and e["adverb"] == name
        )]
        arr.append(entry)
        at_file.write_text(json.dumps(arr, indent=2))

        # 6) Inject minimal config into verb_types.json
        vt_file = project_path / "verb_types.json"
        try:
            vt = json.loads(vt_file.read_text())
        except FileNotFoundError:
            vt = {}
        except (json.JSONDecodeError, OSError) as e:
            # Don't silently mask a corrupt/unreadable existing file as empty.
            log.warning("adverb: could not read", vt_file, "-> starting empty:", repr(e))
            vt = {}
        adsch = vt.setdefault(verb_key, {}).setdefault("adverb_schema", {})
        minimal = {k:v for k,v in handler.config.items() if k!="adverb_class"}
        adsch[name] = minimal
        vt_file.write_text(json.dumps(vt, indent=2))

        print(f"✅ Registered adverb '{name}' on verb '{verb_key}'.")
        return handler

class TagAdverb(BaseAdverb):
    """Adverb holding a fixed set of tag values, with descriptions and display flags."""

    def interactive_configure(self) -> bool:
        changed = False

        # 1) Description (with current shown in brackets)
        cur_desc = self.config.get("description", "")
        prompt = "📖 Short description (for hover/tooltips)"
        if cur_desc:
            prompt += f" [{cur_desc}]"
        desc = input(prompt + ": ").strip()
        if desc and desc != cur_desc:
            self.config["description"] = desc
            changed = True

        # 2) Edit valid options
        if self._edit_valid_options():
            changed = True

        # ✅ Required flag prompt
        self.prompt_required_flag()

        return changed

    def _edit_valid_options(self) -> bool:
        """Interactive menu to add/edit/delete tag options. Returns True if mutated."""
        current = self.config.get("valid_options", []) or []
        mutated = False

        while True:
            print("\nCurrent valid options:")
            if not current:
                print("  (none declared)")
            else:
                for i, opt in enumerate(current):
                    label = opt.get("value", "")
                    extra = []
                    if opt.get("explanation"):
                        extra.append(opt["explanation"])
                    if opt.get("display_in_label"):
                        extra.append("show in ID")
                    suf = f" - {'; '.join(extra)}" if extra else ""
                    print(f"[{i}] {label}{suf}")

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
                if not val or any(o['value']==val for o in current):
                    print("❌ Invalid or duplicate.")
                    continue
                exp  = input("📝 Explanation (optional): ").strip()
                show = input("🏷️ Show in ID? (y/n) ").strip().lower().startswith('y')
                current.append({
                    'value': val,
                    'explanation': exp,
                    'display_in_label': show
                })
                mutated = True

            elif action == 'e':
                if not current:
                    continue
                idx = indexed_choice([o['value'] for o in current], "Which to edit?")
                if idx is None:
                    continue
                opt = current[idx]
                new_val = input(f"Value [{opt['value']}]: ").strip() or opt['value']
                new_exp = input(f"Explanation [{opt.get('explanation','')}]: ").strip()
                if new_exp == "" and opt.get('explanation'):
                    new_exp = opt['explanation']
                disp    = input(f"Show in ID? (y/n) [{'y' if opt.get('display_in_label') else 'n'}]: ").strip().lower()
                if disp in ('y','n'):
                    disp_flag = disp.startswith('y')
                else:
                    disp_flag = opt.get('display_in_label', False)
                opt.update({'value': new_val, 'explanation': new_exp, 'display_in_label': disp_flag})
                mutated = True

            elif action == 'd':
                if not current:
                    continue
                idx = indexed_choice([o['value'] for o in current], "Which to delete?")
                if idx is not None:
                    current.pop(idx)
                    mutated = True

        if mutated:
            self.config['valid_options'] = current
        return mutated

    def prompt_for_value(self, project_path: Path):
        opts = [opt["value"] for opt in self.config.get("valid_options", [])]
        idx  = indexed_choice(opts, f"Select {self.field_name}")
        return None if idx is None else opts[idx]

    def validate(self, value, project_path: Path) -> bool:
        return value in {opt["value"] for opt in self.config.get("valid_options", [])}

class ReferenceAdverb(BaseAdverb):
    """Adverb that references another noun."""

    def __init__(self, *args):
        # same signature as before…
        if len(args) == 2 and isinstance(args[1], dict):
            field_name, config = args
            project_name = None
        elif len(args) == 2:
            project_name, field_name = args
            config = {}
        elif len(args) == 3:
            project_name, field_name, config = args
        else:
            raise TypeError("ReferenceAdverb() needs 2 or 3 args")
        super().__init__(project_name, field_name, config or {})

    def interactive_configure(self) -> bool:
        # 1) Load noun types
        project_path = Path("projects") / self.project_name
        noun_file    = project_path / "noun_types.json"
        try:
            noun_defs = json.loads(noun_file.read_text())
        except Exception:
            print(f"❌ Could not load {noun_file}")
            return False

        # 2) Let user pick which noun to reference
        noun_choices = list(noun_defs.keys())
        idx = indexed_choice(noun_choices, "Select a noun type to reference")
        if idx is None:
            return False
        ref_noun = noun_choices[idx]
        self.config["reference_noun"] = ref_noun

        # 3) Define filters using the shared method
        filters = self.prompt_filters(ref_noun)
        self.config["filters"] = filters

        # 4) Ask whether this adverb is required
        self.prompt_required_flag()

        return True

    def prompt_for_value(self, project_path: Path):
        valid = self._valid_ids(project_path)
        opts = sorted(valid)
        idx = indexed_choice(opts, f"Choose {self.field_name}")
        return None if idx is None else opts[idx]

    def _valid_ids(self, project_path: Path) -> set[str]:
        noun = self.config.get("reference_noun")
        if not noun:
            return set()
        path = project_path / "nouns" / noun / "items.jsonl"
        out = set()
        if path.exists():
            for ln in path.read_text().splitlines():
                try:
                    obj = json.loads(ln)
                    key = next(iter(obj.keys()))
                    out.add(str(obj.get(key)))
                except Exception:
                    pass
        return out

    def validate(self, value, project_path: Path) -> bool:
        return str(value) in self._valid_ids(project_path)

class ReferenceListAdverb(BaseAdverb):
    """
    Adverb that references multiple items across one or more noun types.
    Schema keys:
      - reference_nouns: list[str]
      - filters: dict[noun_type, dict[field, required_value]]
    """

    def interactive_configure(self) -> bool:
        project_path = Path("projects") / self.project_name
        noun_file    = project_path / "noun_types.json"
        try:
            noun_defs = json.loads(noun_file.read_text())
        except Exception:
            print(f"❌ Could not load {noun_file}")
            return False

        all_nouns = list(noun_defs.keys())
        if not all_nouns:
            print("❌ No noun types defined; register a noun first.")
            return False

        # start from any existing config
        selected = list(self.config.get("reference_nouns", []))
        filters  = dict(self.config.get("filters", {}))

        # remaining options
        remaining = [n for n in all_nouns if n not in selected]

        ops = {"a": "add noun type", "d": "remove noun type", "q": "finish"}
        while True:
            print(f"\nCurrently selected noun types: {selected or ['(none)']}")
            action = menu_prompt(ops)
            if action == "q":
                break

            if action == "a":
                if not remaining:
                    print("✅ All noun types already selected.")
                    continue
                idx = indexed_choice(remaining, "Select noun type to add")
                if idx is not None:
                    nt = remaining.pop(idx)
                    selected.append(nt)
                    print(f"\n🔍 Define filters for '{nt}':")
                    filters[nt] = self.prompt_filters(nt)

            elif action == "d":
                if not selected:
                    print("❌ Nothing to remove.")
                    continue
                idx = indexed_choice(selected, "Select noun type to remove")
                if idx is not None:
                    nt = selected.pop(idx)
                    remaining.append(nt)
                    filters.pop(nt, None)

        if not selected:
            print("❌ Must select at least one noun type.")
            return False

        # universal required-flag prompt
        self.prompt_required_flag()

        # write it back
        self.config["reference_nouns"] = selected
        self.config["filters"]         = filters
        return True

    def _valid_ids(self, project_path: Path) -> set[str]:
        out = set()
        for noun in self.config.get("reference_nouns", []):
            p = project_path / "nouns" / noun / "items.jsonl"
            if not p.exists():
                continue
            for ln in p.read_text().splitlines():
                try:
                    obj = json.loads(ln)
                    key = next(iter(obj.keys()))
                    out.add(str(obj.get(key)))
                except Exception:
                    continue
        return out

    def edit_value_for_run(self, current_value: list, project_path: Path) -> list:
        """
        Interactive editor for a list of references.
        """
        current_value = current_value or []
        while True:
            opts = [f"{i+1}. {v}" for i, v in enumerate(current_value)] + [
                "➕ Add new reference",
                "❌ Remove existing reference",
                "⬅️ Done"
            ]
            idx = indexed_choice(opts, "Edit Reference List")
            if idx is None or idx == len(opts) - 1:
                break
            elif opts[idx].startswith("➕"):
                val = self.prompt_for_value(project_path)
                if val and val not in current_value and self.validate(val, project_path):
                    current_value.append(val)
                    print("✅ Added.")
            elif opts[idx].startswith("❌"):
                if not current_value:
                    print("⚠️ Nothing to remove.")
                    continue
                del_idx = indexed_choice(
                    [f"{i+1}. {v}" for i, v in enumerate(current_value)],
                    "Select entry to delete"
                )
                if del_idx is not None:
                    print(f"❌ Removed {current_value[del_idx]}")
                    current_value.pop(del_idx)
        return current_value

    def prompt_for_value(self, project_path: Path):
        """Let user pick multiple IDs from all reference_nouns."""
        ids = sorted(self._valid_ids(project_path))
        chosen: list[str] = []
        while True:
            idx = indexed_choice(ids, f"Select {self.field_name} (or 'q' to finish)")
            if idx is None:
                break
            chosen.append(ids[idx])
        return chosen

    def validate(self, value, project_path: Path) -> bool:
        valid = self._valid_ids(project_path)
        return all(str(v) in valid for v in (value or []))

class AttributeAdverb(BaseAdverb):
    """
    Adverb that holds a single freeform value: string, number, or date.
    Config keys:
      - field_type: "string" | "number" | "date"
      - format: (only if date) one of ["mmddyy","mmddyyyy","yyyy-mm-dd"]
    """

    def interactive_configure(self) -> bool:
        # 1) Pick the data type
        types = ["string", "number", "date"]
        idx = indexed_choice(types, f"Select data type for '{self.field_name}'")
        if idx is None:
            return False
        dtype = types[idx]
        self.config["field_type"] = dtype

        # 2) If it's a date, also pick a format
        if dtype == "date":
            fmts = ["mmddyy", "mmddyyyy", "yyyy-mm-dd"]
            fidx = indexed_choice(fmts, "Select date format")
            if fidx is None:
                return False
            self.config["format"] = fmts[fidx]

        # 3) Universal “required?” flag
        self.prompt_required_flag()

        return True

    def prompt_for_value(self, project_path: Path):
        ftype = self.config.get("field_type", "string")
        fmt = self.config.get("format") if ftype == "date" else None
        required = self.config.get("required", False)

        label = f"{self.field_name}"
        if ftype == "date" and fmt:
            label += f" (format: {fmt})"
        if not required:
            label += " (optional)"
        
        return input(f"{label}: ").strip()

    def validate(self, value, project_path: Path) -> bool:
        val = str(value).strip()
        ftype = self.config.get("field_type", "string")

        if ftype == "date":
            fmt = self.config.get("format", "yyyy-mm-dd")
            return sem.is_valid_date(val, fmt)

        if ftype == "number":
            try:
                float(val)
                return True
            except ValueError:
                return False

        # default = string
        return True

CLASS_MAP = {
    "Tag": TagAdverb,
    "Reference": ReferenceAdverb,
    "ReferenceList": ReferenceListAdverb,
    "Attribute": AttributeAdverb,
    "StateContext": AttributeAdverb,
}

def load_adverb_handler(project_path: Path,
                        verb_name: str,
                        adverb_name: str) -> BaseAdverb:
    """Load the configured handler for a verb's adverb field using adverb_types.json."""
    # Read via the back-compat reader so a migrated name-keyed dict file is iterated as a list of
    # entries (Phase 3); a raw json.load would iterate dict keys (strings) and crash on `a.get(...)`.
    from core.words.reader import load_descriptor_list
    all_adverbs = load_descriptor_list(project_path, "adverb")

    # Find the matching adverb entry for this verb
    match = next(
        (a for a in all_adverbs
         if a.get("adverb") == adverb_name and
            (a.get("verb") == verb_name or verb_name in a.get("verbs", []))),
        None
    )

    if not match:
        print(f"⚠️ Could not find adverb '{adverb_name}' for verb '{verb_name}'. Falling back to AttributeAdverb.")
        return AttributeAdverb(project_path.name, adverb_name, {})

    cls_name = match.get("adverb_class", "Attribute")
    cls = CLASS_MAP.get(cls_name, AttributeAdverb)
    return cls(project_path.name, adverb_name, match)

def get_adverb_handler(name: str,
                       config: dict,
                       project_name: str) -> BaseAdverb:
    """
    Instantiate the right handler based on config['adverb_class'].
    Falls back to AttributeAdverb if unknown.
    """
    cls_name = config.get("adverb_class", "Attribute")
    Handler = CLASS_MAP.get(cls_name, AttributeAdverb)
    return Handler(project_name, name, config)