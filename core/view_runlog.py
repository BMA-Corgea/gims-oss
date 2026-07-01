# core/core_view_runlog.py

from typing import List, Dict, Any

# Classic (buckets-mode) completion states — keep aligned with render_status_bar in status.py
_COMPLETE_STATES = {"Uploaded", "Complete", "Parsed", "Manually Completed"}


def summarize_status_as_fraction(breakdown: dict) -> str:
    """
    Summarize a status breakdown as 'X / Y'.

    Works for BOTH classic and linear modes.

    Classic mode (legacy):
      - X = count of values that are strings and in _COMPLETE_STATES
      - Y = total number of keys in the breakdown (matches render_status_bar logic)

    Linear mode (new):
      Expect a structure like:
        {
          "mode": "linear",
          "linear_steps_completed": <int>,
          "linear_steps_total": <int>,
          ...
        }
      - X = linear_steps_completed
      - Y = linear_steps_total
    """
    if not isinstance(breakdown, dict):
        return "0 / 0"

    # Linear mode
    if breakdown.get("mode") == "linear":
        completed = int(breakdown.get("linear_steps_completed", 0))
        total = int(breakdown.get("linear_steps_total", 0))
        return f"{completed} / {total}"

    # Classic mode
    completed = sum(
        1 for v in breakdown.values()
        if isinstance(v, str) and v in _COMPLETE_STATES
    )
    total = len(breakdown)
    return f"{completed} / {total}"


def collect_headers(entries: List[Dict[str, Any]]) -> List[str]:
    """
    Collect all headers across entries, ensuring 'run_ID' is first after '#',
    and 'test_type' or 'verb' follows if present. Always include '__status'.
    Hides debug/internal fields.

    NOTE: Some projects use a different primary id (e.g., "general ID").
    This helper keeps the historical behavior (prefers 'run_ID' if present).
    If you want to surface a different PID first, pass pre-ordered headers
    from the caller instead of using this helper.
    """
    if not entries:
        return []

    hidden_fields = {
        "_adverb_data",
        "_adverb_schema",
        "_data_entry",
        "_noun_schema",
        "_present_files",
        "_raw_inputs",
        "_status_breakdown",
        "_status_data",
        "_verb_def",
        "details",           # linear-mode nested details
        "first_incomplete",  # linear-mode nested object
    }

    headers = set().union(*(e.keys() for e in entries)) | {"__status"}
    headers -= hidden_fields

    # Prefer 'run_ID' if present; otherwise just sort
    remainder = headers - {"run_ID"}

    if "test_type" in remainder:
        ordered = ["test_type"] + sorted(remainder - {"test_type"})
    elif "verb" in remainder:
        ordered = ["verb"] + sorted(remainder - {"verb"})
    else:
        ordered = sorted(remainder)

    # Add '#' and put 'run_ID' first after that (if present)
    return ["#"] + (["run_ID"] if "run_ID" in headers else []) + ordered


def flatten_entries(entries: List[Dict[str, Any]], headers: List[str]) -> List[List[Any]]:
    """
    Convert dict entries into row-wise lists aligned with given headers.
    Includes index column as first element.
    """
    rows = []
    for idx, entry in enumerate(entries):
        row = [idx]  # leading index for '#'
        for h in headers[1:]:  # skip '#'
            row.append(entry.get(h, ""))
        rows.append(row)
    return rows


def prepare_runlog(
    entries: List[Dict[str, Any]],
    required_fields: List[str],
    noun_schema: dict,
    raw_inputs: list[str],
    adverb_schema: dict | list | None,
    verb_types: dict,
) -> Dict[str, Any]:
    """
    Core formatting only. Assumes status was already calculated in backend
    (each entry already has '__status' and maybe '_status_breakdown').

    Returns headers + rows ready for the front-end table.
    """
    headers = collect_headers(entries)
    rows = flatten_entries(entries, headers)
    return {
        "headers": headers,
        "rows": rows,
        "entries": entries,  # already enriched with __status and _status_breakdown
    }
