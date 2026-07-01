# core/core_verb.py

from typing import Any, Dict, List, Optional


def create_new_verb(verb_key: str) -> Dict[str, Any]:
    """Initialize a bare verb definition structure."""
    return {
        "verb_name": verb_key,
        "verb_group": None,
        "description": "",
        "status_values": [],
        "data_entry_schema": {},
        "adverb_schema": {}
    }


def filter_valid_noun_type_refs(noun_types_schema: dict) -> list[str]:
    """
    Given the full noun_types schema (dict loaded from noun_types.json),
    return only the noun type keys that contain at least one field
    with adjective_class = Reference or ReferenceList.
    """
    valid = []
    for name, schema in noun_types_schema.items():
        fields = schema.get("fields", {})
        for field_props in fields.values():
            if (
                field_props.get("type") == "adjective"
                and field_props.get("adjective_class") in ("Reference", "ReferenceList")
            ):
                valid.append(name)
                break  # one qualifying field is enough
    return sorted(valid)


# -------------------------------
# Data Entry Schema Configuration
# -------------------------------

def set_instructions(schema: Dict[str, Any], instructions: List[str]) -> Dict[str, Any]:
    """Replace or define the instructions list."""
    schema = schema.copy()
    schema["instructions"] = list(instructions)
    return schema


def set_raw_data_inputs(schema: Dict[str, Any], raw_inputs: List[str]) -> Dict[str, Any]:
    """Replace or define raw_data_inputs (list of tab names)."""
    schema = schema.copy()
    schema["raw_data_inputs"] = list(raw_inputs)
    return schema


def set_set_up_inputs(schema: Dict[str, Any], noun_type_ref: str) -> Dict[str, Any]:
    """Define set_up_inputs, tied to a noun_type_ref."""
    schema = schema.copy()
    schema["set_up_inputs"] = {"noun_type_ref": noun_type_ref}
    return schema


def set_interpretation(schema: Dict[str, Any], method: str,
                       tabs: List[str], parsers: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Define interpretation settings.
    - method: "parsed" or "uploaded"
    - tabs: output tab names
    - parsers: optional parser execution order (only relevant if method == "parsed")
    """
    schema = schema.copy()
    schema["interpretation"] = {
        "method": method,
        "tabs": list(tabs),
    }
    if method == "parsed":
        schema["interpretation"]["parsers"] = parsers or []
    return schema


# -----------------
# Adverb Management
# -----------------

def set_adverb_schema(adverb_schema: Dict[str, Any],
                      adverbs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Define the adverb schema for a verb.
    Input: dict of {adverb_name: {"reference_noun": ...} or {"type": ...}}
    """
    return dict(adverbs)


# -----------------
# Verb Group + Logs
# -----------------

def define_log_schema(fields: Dict[str, Dict[str, Any]], primary_id: str) -> Dict[str, Any]:
    """
    Build the log schema with fields and primary_id.
    fields = {field_name: {"type": ..., "required": bool}}
    primary_id = name of the unique primary ID field
    """
    return {
        "primary_id": primary_id,
        "fields": dict(fields)
    }


# -----------------
# Updates to a verb
# -----------------

def update_description(verb_def: Dict[str, Any], description: str) -> Dict[str, Any]:
    out = verb_def.copy()
    out["description"] = description
    return out


def update_status_values(verb_def: Dict[str, Any], statuses: List[str]) -> Dict[str, Any]:
    out = verb_def.copy()
    out["status_values"] = list(statuses)
    return out


def update_data_entry_schema(verb_def: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    out = verb_def.copy()
    out["data_entry_schema"] = dict(schema)
    return out


def update_adverb_schema(verb_def: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    out = verb_def.copy()
    out["adverb_schema"] = dict(schema)
    return out


def assign_verb_group(verb_def: Dict[str, Any], group_name: str) -> Dict[str, Any]:
    out = verb_def.copy()
    out["verb_group"] = group_name
    return out
