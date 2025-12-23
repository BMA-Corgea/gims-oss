# core/handlers/conjunction.py

from datetime import datetime
from typing import Dict, List, Any, Tuple


def validate_and_normalize_override(
    schema: Dict[str, Any],
    provided: Dict[str, Any],
    context: Dict[str, Any],
    valid_refs: Dict[str, List[str]] | None = None,
) -> Tuple[bool, Dict[str, Any], List[str]]:
    """
    Validate and normalize a conjunction override against its schema.

    schema: one entry from verb['status_values'] (name, status, fields)
    provided: dict of user-provided values (note, reference IDs, etc.)
    context: compliance info (e.g. {"initials": "CJ", "date": "2025-07-31"})
    valid_refs: optional dict of {noun_type: [valid_ids]} provided by backend.
                Special case: can include {"Run": [valid_run_ids]}.

    Returns:
        (is_valid, normalized_override, list_of_errors)
    """

    errors: List[str] = []
    result: Dict[str, Any] = {
        "type": schema["name"],
        "status": schema["status"],
    }

    required = schema.get("fields", [])

    for field in required:
        # ---------------------
        # Simple string fields
        # ---------------------
        if isinstance(field, str):
            val = provided.get(field) or context.get(field)
            if not val:
                errors.append(f"Missing required field: {field}")
                continue

            if field.lower() == "date":
                try:
                    datetime.strptime(val, "%Y-%m-%d")
                except Exception:
                    errors.append("Invalid date format, expected YYYY-MM-DD")

            result[field] = val
        
        # ---------------------
        # Field objects with name (non-reference)
        # ---------------------
        elif isinstance(field, dict) and "name" in field and field.get("type") != "reference":
            field_name = field["name"]
            val = provided.get(field_name) or context.get(field_name)
            if not val and field.get("required", False):
                errors.append(f"Missing required field: {field_name}")
                continue
                
            if val:
                result[field_name] = val

        # ---------------------
        # Reference fields
        # ---------------------
        elif isinstance(field, dict) and field.get("type") == "reference":
            label = field.get("label")
            ref_noun = field.get("reference_noun")
            mode = field.get("mode", "Reference")

            # Provided values must exist
            values = provided.get(label)
            if not values:
                errors.append(f"Missing reference field: {label}")
                continue

            if mode == "Reference":
                values = [values]  # normalize to list

            # Validate against allowed references
            if valid_refs and ref_noun in valid_refs:
                allowed = set(valid_refs[ref_noun])
                for v in values:
                    if v not in allowed:
                        errors.append(f"Invalid reference {v} for {ref_noun}")

            # Save normalized (single vs list)
            result[label] = values if mode == "ReferenceList" else values[0]

        else:
            errors.append(f"Invalid field definition: {field}")

    return (len(errors) == 0, result, errors)


def apply_conjunction(status_data: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Attach a validated override to a run's status_data.
    """
    out = dict(status_data)
    out.setdefault("conjunctions", []).append(override)
    return out


def resolve_conjunction(status_data: Dict[str, Any], idx: int, note: str) -> Dict[str, Any]:
    """
    Mark a conjunction as resolved.
    """
    out = dict(status_data)
    try:
        out["conjunctions"][idx]["resolution"] = [{"note": note}]
    except (KeyError, IndexError):
        pass
    return out


def to_global_entry(run_id: str, verb_key: str, override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a global override entry (but don't write it).
    """
    return {
        "run": run_id,
        "verb": verb_key,
        **override
    }


# --------------------------
# Conjunction Schema Helpers
# --------------------------

def create_conjunction(name: str, status: str, fields: List[Any]) -> Dict[str, Any]:
    """
    Create a new conjunction schema definition.
    fields: list of strings or reference dicts.
    """
    return {
        "name": name,
        "status": status,
        "fields": fields
    }


def update_conjunction(existing: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update fields of a conjunction schema.
    updates: {"status": ..., "fields": [...]} or {"name": ...}
    """
    out = existing.copy()
    out.update(updates)
    return out


def delete_conjunction(conjunctions: List[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
    """
    Remove a conjunction schema by name.
    """
    return [c for c in conjunctions if c.get("name") != name]


def validate_conjunction_schema(conjunction: Dict[str, Any]) -> List[str]:
    """
    Validate the structure of a conjunction schema.
    """
    errors: List[str] = []
    if not conjunction.get("name"):
        errors.append("Missing name")
    if not conjunction.get("status"):
        errors.append("Missing status")
    if "fields" not in conjunction:
        errors.append("Missing fields")
    else:
        for f in conjunction["fields"]:
            # Simple string fields are always valid
            if isinstance(f, str):
                continue
                
            # Validate reference field objects
            if isinstance(f, dict) and f.get("type") == "reference":
                if not f.get("label") or not f.get("reference_noun"):
                    errors.append("Reference field missing label or reference_noun")
            # Allow field objects with name and description for non-reference fields
            elif isinstance(f, dict) and "name" in f:
                # Non-reference fields with names are valid
                continue
            else:
                errors.append(f"Invalid field definition: {f}")
    return errors
