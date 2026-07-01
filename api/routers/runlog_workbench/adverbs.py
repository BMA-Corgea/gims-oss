# api/routers/runlog_workbench/adverbs.py
"""Adverbs endpoints."""

from fastapi import Body

from ._router import router
from ._shared import (
    AppError,
    get_project_path,
    _group_pid_field,
    resolve_path,
    fs_exists,
    load_data,
    save_json,
    load_verb_group_log,
    load_schema,
    get_noun_schema,
    get_noun_items,
    HANDLERS,
)

# -----------------------------------------------------------------------------
# Adverbs
# -----------------------------------------------------------------------------

@router.get("/runlog/{project}/{group}/{run_id}/adverb")
def get_adverbs(project: str, group: str, run_id: str):
    project_path = get_project_path(project)

    dbg = {"steps": [], "errors": []}
    def stamp(msg, **extra):
        entry = {"msg": msg, **extra}
        dbg["steps"].append(entry)
        return entry

    adverb_file = resolve_path(project_path, "adverb_file", verb_group=group, run_id=run_id)
    current = load_data(adverb_file) or {}
    stamp("loaded_adverb_file", path=str(adverb_file), has_file=fs_exists(adverb_file), current_keys=list(current.keys()))

    pid_field = _group_pid_field(project_path, group)
    entries = load_verb_group_log(project_path, group) or []
    run = next((e for e in entries if str(e.get(pid_field)) == str(run_id)), None)
    if not run:
        stamp("run_not_found_in_group_log", group=group, pid_field=pid_field, run_id=run_id)
        verb_name = None
        verb_def = {}
        schema_map = {}
    else:
        verb_name = run.get("test_type") or run.get("verb")
        stamp("resolved_verb_name_from_group_log", verb=verb_name, group=group)

        verb_types = load_schema(project_path, "verb") or {}
        verb_def = verb_types.get(verb_name, {}) if verb_name else {}
        schema_map = verb_def.get("adverb_schema", {}) or {}
        stamp("loaded_adverb_schema", schema_keys=list(schema_map.keys()))

    ui = {}
    for key, entry in schema_map.items():
        entry = dict(entry)
        entry.setdefault("adverb", key)
        adverb_class = entry.get("adverb_class")
        stamp("process_adverb", key=key, adverb_class=adverb_class)

        cls = HANDLERS.get(adverb_class)
        if not cls:
            ui[key] = {"kind": "scalar", "field_type": entry.get("field_type", "string")}
            stamp("no_handler_fallback_scalar", key=key)
            continue

        try:
            try:
                handler = cls(entry)
            except TypeError:
                handler = cls(data=entry)
            stamp("handler_instantiated", key=key, handler=str(cls.__name__))
        except Exception as e:
            dbg["errors"].append({"key": key, "where": "ctor", "err": repr(e)})
            ui[key] = {"kind": "scalar", "field_type": entry.get("field_type", "string")}
            continue

        try:
            if adverb_class == "ReferenceList":
                ref_nouns = handler.get_reference_noun() or entry.get("reference_nouns") or []
                if isinstance(ref_nouns, str):
                    ref_nouns = [ref_nouns]

                all_vals: list[str] = []
                for nt in ref_nouns:
                    n_schema = get_noun_schema(project_path, nt) or {}
                    pid = (n_schema.get("primary_id_field")
                           or n_schema.get("primary_id")
                           or None)
                    if not pid:
                        continue
                    pid_u = pid.replace(" ", "_")
                    pid_s = pid.replace("_", " ")
                    rows = get_noun_items(project_path, nt)
                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        val = r.get(pid) or r.get(pid_u) or r.get(pid_s)
                        if val:
                            all_vals.append(str(val).strip())

                uniq = sorted({v for v in all_vals if v})
                opts = [{"value": v, "label": v} for v in uniq]
                ui[key] = {"kind": "ref_list", "options": opts}

            elif adverb_class == "Reference":
                ref = handler.get_reference_noun() or entry.get("reference_noun") or ""
                if isinstance(ref, list):
                    ref = ref[0] if ref else ""
                if not ref:
                    ui[key] = {"kind": "ref", "options": []}
                    continue

                n_schema = get_noun_schema(project_path, ref) or {}
                pid = (n_schema.get("primary_id_field")
                       or n_schema.get("primary_id")
                       or None)
                if not pid:
                    ui[key] = {"kind": "ref", "options": []}
                    continue

                pid_u = pid.replace(" ", "_")
                pid_s = pid.replace("_", " ")

                filters = entry.get("filters", {}) or {}
                def _passes(rec: dict) -> bool:
                    if not filters:
                        return True
                    for fk, fv in filters.items():
                        if rec.get(fk) != fv and rec.get(fk.replace(" ", "_")) != fv:
                            return False
                    return True

                rows = get_noun_items(project_path, ref)

                seen = set()
                opts: list[dict] = []
                for r in rows:
                    if not isinstance(r, dict) or not _passes(r):
                        continue
                    val = r.get(pid) or r.get(pid_u) or r.get(pid_s)
                    if not val:
                        continue
                    sval = str(val).strip()
                    if sval and sval not in seen:
                        seen.add(sval)
                        opts.append({"value": sval, "label": sval})

                opts.sort(key=lambda x: x["label"])
                ui[key] = {"kind": "ref", "options": opts}

            elif adverb_class == "Tag":
                opts = []
                try:
                    for o in handler.get_valid_options() or []:
                        opts.append({
                            "value": o.get("value"),
                            "label": o.get("value"),
                            "title": o.get("explanation", ""),
                            "display_in_label": bool(o.get("display_in_label")),
                        })
                except Exception as e:
                    dbg["errors"].append({"key": key, "where": "get_valid_options", "err": repr(e)})
                ui[key] = {"kind": "tag", "options": opts}

            elif adverb_class == "Attribute":
                ui[key] = {"kind": "scalar", "field_type": entry.get("field_type", "string")}

            elif adverb_class == "Picture":
                ui[key] = {"kind": "picture"}

            else:
                ui[key] = {"kind": "scalar", "field_type": entry.get("field_type", "string")}

        except Exception as e:
            dbg["errors"].append({"key": key, "where": "handler_flow", "err": repr(e)})
            ui[key] = {"kind": "scalar", "field_type": entry.get("field_type", "string")}

    available = [{**dict(entry), "adverb": key} for key, entry in schema_map.items()]

    return {
        "verb": verb_name,
        "adverbs": current,
        "available_types": available,
        "ui": ui,
        "file": str(adverb_file),
        "_debug": dbg,
    }

@router.post("/runlog/{project}/{group}/{run_id}/adverb/update")
def update_adverbs(project: str, group: str, run_id: str, payload: dict = Body(...)):
    project_path = get_project_path(project)

    status_file = resolve_path(project_path, "status_file", verb_group=group, run_id=run_id)
    status_doc = load_data(status_file) or {}
    ls = (status_doc.get("linear_status") or {})
    steps = list(ls.get("steps") or [])
    if steps:
        idx = ls.get("current_index")
        if idx is None:
            idx = next((i for i, s in enumerate(steps) if not bool(s.get("completed"))), len(steps))
        _ = steps[idx] if 0 <= idx < len(steps) else None

    adverb_file = resolve_path(project_path, "adverb_file", verb_group=group, run_id=run_id)
    new_vals = payload.get("adverbs")
    if not isinstance(new_vals, dict):
        raise AppError("INVALID_REQUEST_BODY", "Body must include 'adverbs' object.", status=400)

    save_json(adverb_file, new_vals)
    return {"status": "success", "count": len(new_vals), "file": str(adverb_file)}
