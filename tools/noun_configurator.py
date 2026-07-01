import sys
import json
import logging
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.handlers.noun_type import NounType, register_noun_type
from utils.interface import menu_prompt, indexed_choice
from utils.id_generator import prompt_date_format, generate_hex_segment
import datetime

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

VALID_FIELD_TYPES = ["number", "string", "date", "datetime"]

def ask_field_type(default="string"):
    idx = indexed_choice(VALID_FIELD_TYPES, f"Field type index [default: {default}]")
    return default if idx is None else VALID_FIELD_TYPES[idx]

def configure_id_segments() -> list:
    segments = []
    while True:
        action = menu_prompt({
            's': 'static string segment',
            'd': 'dynamic segment',
            'v': 'view current format',
            'q': 'done'
        })

        if action == 'q':
            break
        if action == 'v':
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
                    preview.append(generate_hex_segment(int(seg['start'], 16), 0, seg['length']))
            logger.info(f"🧪 Preview format: {''.join(preview)}")
            continue

        if action == 's':
            val = input("Enter static string (e.g., SMP): ").strip()
            if val:
                segments.append({"type": "static", "value": val})
        elif action == 'd':
            idx = indexed_choice([
                "Date component", "Number counter", "Letter counter", "Hexadecimal counter"
            ], "Choose dynamic segment type")
            if idx == 0:
                fmt = prompt_date_format()
                if fmt:
                    segments.append({"type": "date", "format": fmt})
            elif idx == 1:
                start = int(input("Start number: ") or "0")
                length = int(input("Digit width: ") or "4")
                segments.append({"type": "number", "start": start, "length": length})
            elif idx == 2:
                start = int(input("Letter index start (0=A): ") or "0")
                segments.append({"type": "letter", "start": start})
            elif idx == 3:
                start_hex = input("Start hex: ") or "0"
                length = int(input("Hex width: ") or "4")
                segments.append({"type": "hex", "start": start_hex, "length": length})
    return segments

def interactive_register(project_path: Path, noun_name: str):
    noun_schema = {
        "description": input(f"📄 Description for noun '{noun_name}': ").strip(),
        "fields": {}
    }
    nt = NounType(noun_name, noun_schema, project_path / "noun_types.json")

    while True:
        logger.info("Current fields:")
        for i, (fname, fdata) in enumerate(nt.schema.get("fields", {}).items()):
            logger.info(f"[{i}] {fname}: {fdata['type']}")
        action = menu_prompt({'a': 'add field', 'q': 'done'})
        if action == 'q':
            break
        if action == 'a':
            fname = input("Field name: ").strip()
            ftype = ask_field_type()
            req = input("Required? (y/n): ").strip().lower().startswith("y")
            fmt = prompt_date_format() if ftype == "date" else None
            nt.add_field(fname, ftype, req, fmt)

    fields = list(nt.schema["fields"].keys())
    if fields:
        idx = indexed_choice(fields, "Select primary ID field")
        pid = fields[idx] if idx is not None else fields[0]

        choice = menu_prompt({
            'y': 'yes, autogenerate new IDs',
            'n': 'no, user must enter manually',
            'k': 'keep existing settings'
        })
        expanded = {'y': 'yes', 'n': 'no', 'k': 'keep'}.get(choice, 'keep')

        segments = configure_id_segments() if expanded == 'yes' else []
        if expanded == 'yes' and not segments:
            logger.warning("⚠️ No segments provided. Autogeneration skipped.")
            expanded = 'no'

        nt.configure_primary_id(pid, expanded, segments)

    logger.info(f"✅ Finished configuring noun: '{noun_name}'")

def interactive_edit(project_path: Path, noun_name: str):
    noun_file = project_path / "noun_types.json"
    with open(noun_file) as f:
        all_nouns = json.load(f)

    schema = all_nouns[noun_name]
    nt = NounType(noun_name, schema, noun_file)

    while True:
        fields = nt.schema.get("fields", {})
        logger.info(f"\n🧬 Fields for noun '{noun_name}':")
        for i, (fname, fdata) in enumerate(fields.items()):
            req = " (required)" if fdata.get("required") else ""
            logger.info(f"[{i}] {fname} : {fdata['type']}{req}")

        action = menu_prompt({
            'a': 'add', 'e': 'edit', 'r': 'rename', 'd': 'delete',
            'p': 'configure primary ID', 'q': 'quit'
        })

        if action == 'q':
            logger.info(f"💾 Saved changes to '{noun_name}'")
            break
        elif action == 'a':
            fname = input("➕ New field name: ").strip()
            ftype = ask_field_type()
            req = input("Required? (y/n): ").strip().lower().startswith("y")
            fmt = prompt_date_format() if ftype == "date" else None
            nt.add_field(fname, ftype, req, fmt)
        elif action == 'e':
            idx = input("Index to edit: ").strip()
            if idx.isdigit():
                fname = list(fields.keys())[int(idx)]
                ftype = ask_field_type()
                fmt = prompt_date_format() if ftype == "date" else None
                req = input("Required? (y/n): ").strip().lower().startswith("y")
                nt.edit_field(fname, new_type=ftype, required=req, format_override=fmt)
        elif action == 'r':
            idx = input("Index to rename: ").strip()
            if idx.isdigit():
                old = list(fields.keys())[int(idx)]
                new = input(f"Rename '{old}' to: ").strip()
                adjective_file = project_path / "adjective_types.json"
                nt.rename_field(old, new, adjective_file=adjective_file)
        elif action == 'd':
            idx = input("Index to delete: ").strip()
            if idx.isdigit():
                fname = list(fields.keys())[int(idx)]
                confirm = input(f"Delete '{fname}'? (y/n): ").strip().lower()
                if confirm == "y":
                    nt.delete_field(fname)
        elif action == 'p':
            field_list = list(fields.keys())
            if not field_list:
                logger.warning("❌ No fields defined.")
                continue
            idx = indexed_choice(field_list, "Select primary ID field")
            if idx is None:
                continue
            pid = field_list[idx]
            choice = menu_prompt({
                'y': 'yes, autogenerate new IDs',
                'n': 'no, user must enter manually',
                'k': 'keep existing settings'
            })
            expanded = {'y': 'yes', 'n': 'no', 'k': 'keep'}.get(choice, 'keep')
            segments = configure_id_segments() if expanded == 'yes' else []
            if expanded == 'yes' and not segments:
                logger.warning("⚠️ No segments provided. Autogeneration skipped.")
                expanded = 'no'
            nt.configure_primary_id(pid, expanded, segments)

if __name__ == "__main__":
    logger.info("🧠 Launching Noun Configurator")
    project_root = Path("projects")
    projects = [p.name for p in project_root.iterdir() if p.is_dir()]
    if not projects:
        logger.error("❌ No projects found.")
        sys.exit(1)

    idx = indexed_choice(projects, "Choose a project")
    if idx is None:
        logger.warning("❌ Cancelled.")
        sys.exit(1)

    project_path = project_root / projects[idx]
    noun_file = project_path / "noun_types.json"
    existing_nouns = []
    if noun_file.exists():
        with open(noun_file) as f:
            existing_nouns = list(json.load(f).keys())

    action = menu_prompt({
        'r': 'register new noun type',
        'e': 'edit existing noun type',
        'q': 'quit'
    })

    if action == 'q':
        logger.info("👋 Bye.")
        sys.exit(0)

    if action == 'r':
        noun_name = input("Enter new noun type name: ").strip()
        interactive_register(project_path, noun_name)
    elif action == 'e':
        if not existing_nouns:
            logger.warning("❌ No existing noun types to edit.")
            sys.exit(1)
        idx = indexed_choice(existing_nouns, "Select noun to edit")
        if idx is not None:
            interactive_edit(project_path, existing_nouns[idx])
