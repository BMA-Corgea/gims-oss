# core/run_custom/pphrase_expand.py
# ============================================================
# PREPHRASE EXPANSION (PURE, NO I/O)
# ============================================================
from __future__ import annotations
from typing import Dict, List, Any, Optional, Callable, Tuple
from copy import deepcopy
from ._common import log


_SUP_OPS = {"in", "=", "!=", "contains", "between", "has_pair", "exists", "missing"}

def expand_prephrase_settings_dynamic(
    settings: List[Dict[str, Any]],
    user_values: Optional[Dict[str, Any]] = None,
    *,
    fetch_noun_schema: Callable[[str], Optional[Dict[str, Any]]],
    fetch_noun_items: Callable[[str], List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    PURE expander: interprets dynamic 'options' dicts without performing I/O itself.

    DI (dependency injection):
      - fetch_noun_schema(noun_name) -> schema dict or None
      - fetch_noun_items(noun_name)  -> list[dict] rows

    Steps per field (kind in {single,multi} and options is dict):
      - source = "noun: <Type>" only (others -> [])
      - read primary_id_field from noun schema (fallback to "id")
      - optionally gate to 'complete' rows (all required fields non-empty)
      - apply filters (NO-OP on empty inputs)
      - dedupe (unique_by or primary_id_field)
      - sort (pre-map by row fields; post-map by label/value)
      - map label/value (format strings)
      - limit
    """
    log.debug("[expand] begin | fields=", len(settings))
    out = deepcopy(settings)
    uv = user_values or {}

    for i, field in enumerate(out, 1):
        fid = field.get("id")
        fkind = (field.get("kind") or "").lower()
        log.debug(f"[expand] field[{i}] id={fid!r} kind={fkind!r}")

        if fkind not in {"single", "multi"}:
            log.debug(f"[expand] field[{i}] skip (kind not single/multi)")
            continue

        options = field.get("options")
        if not isinstance(options, dict):
            log.debug(f"[expand] field[{i}] options not dynamic dict -> leave as-is")
            continue

        source = options.get("source")
        log.debug(f"[expand] field[{i}] dynamic source={source!r}")
        if not source or not isinstance(source, str):
            log.debug(f"[expand][warn] field[{i}] missing/invalid source -> options=[]")
            field["options"] = []
            continue

        if not source.lower().startswith("noun:"):
            log.debug(f"[expand] field[{i}] unsupported source {source!r} -> options=[]")
            field["options"] = []
            continue

        noun_type = source.split(":", 1)[1].strip()
        log.debug(f"[expand] field[{i}] noun_type={noun_type!r}")

        # -- Load noun schema/items via DI --
        noun_schema = fetch_noun_schema(noun_type)
        if not noun_schema:
            log.debug(f"[expand][error] field[{i}] noun schema not found -> options=[]")
            field["options"] = []
            continue

        primary_id = noun_schema.get("primary_id_field") or "id"
        required_fields = _extract_required_fields(noun_schema)
        log.debug(f"[expand] field[{i}] primary_id={primary_id!r} required_fields={required_fields}")

        try:
            rows = fetch_noun_items(noun_type)  # list[dict]
            log.debug(f"[expand] field[{i}] loaded {len(rows)} noun item(s)")
        except Exception as e:
            log.debug(f"[expand][error] field[{i}] fetch_noun_items failed: {e}")
            field["options"] = []
            continue

        # -- Complete gate --
        complete_flag = bool(options.get("complete", False))
        if complete_flag:
            before = len(rows)
            rows = [r for r in rows if _row_is_complete(r, required_fields)]
            log.debug(f"[expand] field[{i}] complete=True | {before} -> {len(rows)}")
        else:
            log.debug(f"[expand] field[{i}] complete=False (skip gate)")

        # -- Filters --
        filters = options.get("filters", [])
        log.debug(f"[expand] field[{i}] filters_present={isinstance(filters, list)} count={len(filters) if isinstance(filters, list) else 0}")
        if isinstance(filters, list):
            for k, flt in enumerate(filters, 1):
                log.debug(f"[expand] field[{i}] filter[{k}] raw={flt!r}")
                rows = _apply_filter(rows, flt, uv, i, k)

        # -- Dedupe --
        allow_dup = bool(options.get("allow_duplicates", False))
        unique_by = options.get("unique_by")
        if allow_dup:
            log.debug(f"[expand] field[{i}] allow_duplicates=True (skip dedupe)")
        else:
            if isinstance(unique_by, list) and all(isinstance(x, str) for x in unique_by):
                before = len(rows)
                rows = _dedupe_rows_by_keys(rows, unique_by)
                log.debug(f"[expand] field[{i}] dedupe by {unique_by} | {before} -> {len(rows)}")
            elif isinstance(primary_id, str) and primary_id:
                before = len(rows)
                rows = _dedupe_rows_by_keys(rows, [primary_id])
                log.debug(f"[expand] field[{i}] dedupe by primary_id={primary_id!r} | {before} -> {len(rows)}")
            else:
                log.debug(f"[expand] field[{i}] no unique_by and no primary_id; skipping dedupe")

        # -- Sort (rows pre-map) --
        sort_specs = options.get("sort", [])
        if isinstance(sort_specs, list) and sort_specs:
            row_sorts, opt_sorts = _partition_sort_specs(sort_specs)
            if row_sorts:
                log.debug(f"[expand] field[{i}] row sort specs -> {row_sorts}")
                rows = _sort_rows(rows, row_sorts)
                log.debug(f"[expand] field[{i}] rows sorted (pre-map)")
        else:
            opt_sorts = []

        # -- Map label/value (with _runID passthrough) --
        map_spec = options.get("map") or {}
        label_tpl = map_spec.get("label", "{"+(primary_id or "id")+"}")
        value_tpl = map_spec.get("value", "{"+(primary_id or "id")+"}")
        log.debug(f"[expand] field[{i}] map label={label_tpl!r} value={value_tpl!r}")

        options_list: List[Dict[str, Any]] = []
        for ridx, row in enumerate(rows, 1):
            label = _format_template(label_tpl, row)
            value = _format_template(value_tpl, row)
            opt: Dict[str, Any] = {"label": label, "value": value}
            if "_runID" in row:
                opt["_runID"] = row["_runID"]
                log.debug(f"[expand] field[{i}] row[{ridx}] _runID={row['_runID']!r} -> option")
            options_list.append(opt)
        log.debug(f"[expand] field[{i}] mapped {len(options_list)} option(s)")

        # -- Sort (options post-map) --
        if opt_sorts:
            log.debug(f"[expand] field[{i}] option sort specs -> {opt_sorts}")
            options_list = _sort_options(options_list, opt_sorts)
            log.debug(f"[expand] field[{i}] options sorted (post-map)")

        # -- Limit --
        limit = options.get("limit")
        if isinstance(limit, int) and limit >= 0:
            before = len(options_list)
            options_list = options_list[:limit]
            log.debug(f"[expand] field[{i}] limit={limit} | {before} -> {len(options_list)}")
        else:
            log.debug(f"[expand] field[{i}] no/invalid limit -> keep {len(options_list)}")

        field["options"] = options_list
        log.debug(f"[expand] field[{i}] done | options_len={len(options_list)}")

    log.debug("[expand] done")
    return out


# --------------------------
# Helpers (pure)
# --------------------------

def _extract_required_fields(noun_schema: Dict[str, Any]) -> List[str]:
    fields = noun_schema.get("fields", {}) or {}
    req = [name for name, meta in fields.items() if isinstance(meta, dict) and meta.get("required") is True]
    log.debug("[expand.helpers] required_fields ->", req)
    return req

def _is_nonempty(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) > 0
    return bool(v)

def _row_is_complete(row: Dict[str, Any], required_fields: List[str]) -> bool:
    for f in required_fields:
        if not _is_nonempty(row.get(f)):
            return False
    return True

def _get_ref_values(flt: Dict[str, Any], user_values: Dict[str, Any]) -> Any:
    ref = flt.get("ref")
    if ref is None:
        return None
    if isinstance(ref, str):
        val = user_values.get(ref)
        log.debug(f"[expand.helpers] ref={ref!r} -> {val!r}")
        return val
    if isinstance(ref, list):
        vals = [user_values.get(r) for r in ref]
        log.debug(f"[expand.helpers] ref(list)={ref!r} -> {vals!r}")
        return vals
    log.debug(f"[expand.helpers] ref invalid type -> {type(ref).__name__}")
    return None

def _should_apply(op: str, value: Any) -> bool:
    if op == "in":
        return isinstance(value, (list, tuple, set)) and len(value) > 0
    if op == "between":
        if isinstance(value, (list, tuple)) and len(value) == 2:
            lo, hi = value
            return _is_nonempty(lo) or _is_nonempty(hi)
        return False
    if op in {"contains", "=", "!="}:
        return _is_nonempty(value)
    if op in {"exists", "missing", "has_pair"}:
        return True
    return False

def _apply_filter(rows: List[Dict[str, Any]], flt: Dict[str, Any], uv: Dict[str, Any], fi: int, ki: int) -> List[Dict[str, Any]]:
    op = flt.get("op")
    field = flt.get("field")
    val  = flt.get("value", None)
    refv = _get_ref_values(flt, uv)
    value = refv if refv is not None else val

    if op not in _SUP_OPS:
        log.debug(f"[expand][warn] field[{fi}] filter[{ki}] unsupported op={op!r} -> NO-OP")
        return rows

    if op in {"in", "=", "!=", "contains", "between"} and not field:
        log.debug(f"[expand][warn] field[{fi}] filter[{ki}] op={op!r} missing 'field' -> NO-OP")
        return rows

    if not _should_apply(op, value):
        log.debug(f"[expand] field[{fi}] filter[{ki}] NO-OP (empty value) | op={op!r} value={value!r}")
        return rows

    before = len(rows)
    log.debug(f"[expand] field[{fi}] filter[{ki}] apply | op={op!r} field={field!r} value={value!r} rows={before}")

    if op == "exists":
        target = True if flt.get("value", None) is None else bool(flt["value"])
        rows = [r for r in rows if (_is_nonempty(r.get(field)) if target else not _is_nonempty(r.get(field)))]
    elif op == "missing":
        rows = [r for r in rows if not _is_nonempty(r.get(field))]
    elif op == "in":
        s = set(value)  # type: ignore[arg-type]
        rows = [r for r in rows if r.get(field) in s]
    elif op == "=":
        rows = [r for r in rows if r.get(field) == value]
    elif op == "!=":
        rows = [r for r in rows if r.get(field) != value]
    elif op == "contains":
        needle = str(value).lower()
        rows = [r for r in rows if needle in str(r.get(field, "")).lower()]
    elif op == "between":
        lo, hi = value if isinstance(value, (list, tuple)) and len(value) == 2 else (None, None)
        def in_range(x):
            if not _is_nonempty(x): return False
            sx = str(x)
            ok_lo = (not _is_nonempty(lo)) or (sx >= str(lo))
            ok_hi = (not _is_nonempty(hi)) or (sx <= str(hi))
            return ok_lo and ok_hi
        rows = [r for r in rows if in_range(r.get(field))]
    elif op == "has_pair":
        keys = value if isinstance(value, (list, tuple)) else []
        rows = [r for r in rows if all(_is_nonempty(r.get(k)) for k in keys)]

    after = len(rows)
    log.debug(f"[expand] field[{fi}] filter[{ki}] done | {before} -> {after}")
    return rows

def _dedupe_rows_by_keys(rows: List[Dict[str, Any]], keys: List[str]) -> List[Dict[str, Any]]:
    seen: set[Tuple[Any, ...]] = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
        sig = tuple(r.get(k) for k in keys)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(r)
    return out

def _partition_sort_specs(sort_specs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    row_sorts, opt_sorts = [], []
    for s in sort_specs:
        fld = s.get("field")
        if fld in {"label", "value"}:
            opt_sorts.append(s)
        else:
            row_sorts.append(s)
    return row_sorts, opt_sorts

def _sort_key(x: Any) -> Tuple[int, str]:
    if x is None:
        return (0, "")
    return (1, str(x))

def _sort_rows(rows: List[Dict[str, Any]], sort_specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for s in reversed(sort_specs):
        fld = s.get("field")
        reverse = s.get("dir", "asc") == "desc"
        rows.sort(key=lambda r: _sort_key(r.get(fld)), reverse=reverse)
    return rows

def _sort_options(options: List[Dict[str, Any]], sort_specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for s in reversed(sort_specs):
        fld = s.get("field")
        reverse = s.get("dir", "asc") == "desc"
        options.sort(key=lambda o: _sort_key(o.get(fld)), reverse=reverse)
    return options

class _SafeDict(dict):
    def __missing__(self, key):  # allows "{missing}" -> ""
        return ""

def _format_template(tpl: str, row: Dict[str, Any]) -> str:
    try:
        return tpl.format_map(_SafeDict({k: "" if v is None else v for k, v in row.items()}))
    except Exception:
        return str(row)
