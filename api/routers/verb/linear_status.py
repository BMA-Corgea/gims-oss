# api/routers/verb/linear_status.py
#
# Validation / normalization / proposal helpers for the linear_status block.
# Moved VERBATIM from api/routers/verb.py (no logic changes).

from typing import Any, List

from core.errors import AppError

from ._log import log


# ─────────────────────────────────────────────────────────────
# Validation / Normalization for linear_status
# ─────────────────────────────────────────────────────────────
_ALLOWED_STEP_TYPES = {
    "data_entry",
    "raw_upload",
    "interpretation",
    "adverb",
    "gate",
    "report",
}

def _normalize_bool(x: Any, default: bool) -> bool:
    return bool(x) if isinstance(x, (bool, int)) else default

def _validate_unique_ids(steps: List[dict]) -> List[str]:
    seen = set()
    dups = []
    for s in steps or []:
        sid = str(s.get("id", "")).strip()
        if not sid:
            dups.append("(blank id)")
            continue
        if sid in seen:
            dups.append(sid)
        seen.add(sid)
    return dups

def _index_schema_bits(verb_def: dict) -> dict:
    des = verb_def.get("data_entry_schema", {}) or {}
    adverbs = verb_def.get("adverb_schema", {}) or {}

    raw_inputs = list(des.get("raw_data_inputs", []) or [])
    interp = des.get("interpretation", {}) or {}
    interp_tabs = list(interp.get("tabs", []) or [])
    parsers = list(interp.get("parsers", []) or [])
    adverb_keys = sorted(list(adverbs.keys()))

    return {
        "raw_inputs": raw_inputs,
        "interp_tabs": interp_tabs,
        "parsers": parsers,
        "adverb_keys": adverb_keys,
    }

def _validate_linear_status_block(verb_def: dict, block: dict) -> dict:
    log.debug("[validate_linear_status] begin", {"verb_name": verb_def.get("verb_name")})

    if not isinstance(block, dict):
        log.debug("[validate_linear_status] invalid type", {"type": type(block).__name__})
        raise AppError("LINEAR_STATUS_INVALID", "linear_status must be an object", status=400)

    enabled = _normalize_bool(block.get("enabled", True), True)
    allow_manual_completion = _normalize_bool(block.get("allow_manual_completion", False), False)

    steps = block.get("steps", [])
    if not isinstance(steps, list):
        log.debug("[validate_linear_status] steps not a list")
        raise AppError("LINEAR_STATUS_STEPS_INVALID", "linear_status.steps must be a list", status=400)

    dups = _validate_unique_ids(steps)
    if dups:
        log.debug("[validate_linear_status] duplicate step ids", {"dups": dups})
        raise AppError(
            "LINEAR_STATUS_DUPLICATE_STEP_ID",
            f"Duplicate or blank step id(s): {', '.join(dups)}",
            status=400,
            details={"duplicate_ids": dups},
        )

    anchors = _index_schema_bits(verb_def)
    errors: List[str] = []

    normalized_steps: List[dict] = []
    for idx, s in enumerate(steps):
        ctx = {"index": idx, "id": s.get("id")}
        log.debug("[validate_linear_status] step", {**ctx, "raw": s})

        if not isinstance(s, dict):
            errors.append(f"Step[{idx}] must be an object")
            continue

        sid = str(s.get("id", "")).strip()
        if not sid:
            errors.append(f"Step[{idx}] missing 'id'")
        stype = str(s.get("type", "")).strip()
        if stype not in _ALLOWED_STEP_TYPES:
            errors.append(f"Step[{sid or idx}] invalid type '{stype}'")

        label = s.get("label")
        if label is not None and not isinstance(label, str):
            errors.append(f"Step[{sid}] label must be a string if provided")

        required = _normalize_bool(s.get("required", True), True)

        source = s.get("source")
        parser = s.get("parser")
        roles = s.get("roles")

        if stype == "raw_upload":
            if not isinstance(source, str) or not source.strip():
                errors.append(f"Step[{sid}] raw_upload requires 'source' (one of raw_data_inputs)")
            elif source not in anchors["raw_inputs"]:
                errors.append(f"Step[{sid}] source '{source}' not found in raw_data_inputs {anchors['raw_inputs']}")
        elif stype == "interpretation":
            if not isinstance(source, str) or not source.strip():
                errors.append(f"Step[{sid}] interpretation requires 'source' (one of interpretation.tabs)")
            elif source not in anchors["interp_tabs"]:
                errors.append(f"Step[{sid}] source '{source}' not found in interpretation.tabs {anchors['interp_tabs']}")
            if parser is not None:
                if not isinstance(parser, str):
                    errors.append(f"Step[{sid}] parser must be a string")
                elif anchors["parsers"] and parser not in anchors["parsers"]:
                    errors.append(f"Step[{sid}] parser '{parser}' not in defined parsers {anchors['parsers']}")
        elif stype == "adverb":
            if not isinstance(source, str) or not source.strip():
                errors.append(f"Step[{sid}] adverb requires 'source' (an adverb key)")
            elif source not in anchors["adverb_keys"]:
                errors.append(f"Step[{sid}] adverb source '{source}' not in adverb_schema keys {anchors['adverb_keys']}")
        elif stype == "gate":
            if roles is not None and not (isinstance(roles, list) and all(isinstance(r, str) for r in roles)):
                errors.append(f"Step[{sid}] gate.roles must be a list of strings if provided")

        norm = {
            "id": sid,
            "type": stype,
            "label": label if isinstance(label, str) else None,
            "required": required,
        }
        if stype in ("raw_upload", "interpretation", "adverb") and isinstance(source, str) and source.strip():
            norm["source"] = source
        if stype == "interpretation" and isinstance(parser, str):
            norm["parser"] = parser
        if stype == "gate" and isinstance(roles, list):
            norm["roles"] = roles

        normalized_steps.append(norm)
        log.debug("[validate_linear_status] step normalized", {**ctx, "norm": norm})

    if errors:
        log.debug("[validate_linear_status] errors", {"errors": errors})
        raise AppError(
            "LINEAR_STATUS_VALIDATION_FAILED",
            "Validation failed",
            status=400,
            details={"errors": errors},
        )

    normalized = {
        "enabled": enabled,
        "allow_manual_completion": allow_manual_completion,
        "steps": normalized_steps,
    }
    log.debug("[validate_linear_status] ok", {"enabled": enabled, "allow_manual_completion": allow_manual_completion, "count": len(normalized_steps)})
    return normalized

def _propose_linear_status(verb_def: dict) -> dict:
    log.debug("[propose_linear_status] begin", {"verb_name": verb_def.get("verb_name")})
    anchors = _index_schema_bits(verb_def)
    steps: List[dict] = []

    # 1) data entry
    steps.append({
        "id": "data_entry",
        "type": "data_entry",
        "label": "Data Entry",
        "required": True,
    })

    # 2) raw uploads in declared order
    for raw in anchors["raw_inputs"]:
        steps.append({
            "id": f"raw_{raw}",
            "type": "raw_upload",
            "label": raw,
            "source": raw,
            "required": True,
        })

    # 3) interpretation tabs
    for tab in anchors["interp_tabs"]:
        step = {
            "id": f"interp_{tab}",
            "type": "interpretation",
            "label": tab,
            "source": tab,
            "required": True,
        }
        matching = [p for p in anchors["parsers"] if p == tab]
        if matching:
            step["parser"] = matching[0]
        steps.append(step)

    # 4) adverbs
    for adv in anchors["adverb_keys"]:
        steps.append({
            "id": f"adv_{adv}",
            "type": "adverb",
            "label": adv,
            "source": adv,
            "required": False,
        })

    proposal = {
        "enabled": True,
        "allow_manual_completion": False,
        "steps": steps,
    }
    log.debug("[propose_linear_status] built", {"count": len(steps)})
    return proposal
