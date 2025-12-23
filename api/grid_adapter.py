# api/grid_adapter.py
from __future__ import annotations

from fastapi import APIRouter, UploadFile, File, HTTPException, Body
from typing import Any, Dict, List
from pathlib import Path
import io, csv, json, sqlite3, os, re, tempfile

# Use your project resolver + IO helpers
from api.manifest.resolver import resolve_path
from api.i_o import load_data

# ---------------- Optional XLSX support ----------------
try:
    import pandas as pd  # pip install pandas openpyxl
    HAVE_PANDAS = True
except Exception:
    HAVE_PANDAS = False

router = APIRouter(prefix="/grid", tags=["grid"])

# ------------------------- Normalization Helpers -------------------------

def _flatten_once(obj: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            for sk, sv in v.items():
                out[f"{key}.{sk}"] = sv
        else:
            out[key] = v
    return out

def _object_of_arrays_to_rows(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    keys = list(obj.keys())
    max_len = max((len(v) for v in obj.values() if isinstance(v, list)), default=0)
    rows: List[Dict[str, Any]] = []
    for i in range(max_len):
        r: Dict[str, Any] = {}
        for k in keys:
            v = obj[k]
            r[k] = (v[i] if isinstance(v, list) and i < len(v) else None)
        rows.append(r)
    return rows

def _rows_headers_union(rows: List[Dict[str, Any]]) -> List[str]:
    keys = set()
    for r in rows:
        for k in r.keys():
            keys.add(k)
    order = sorted(keys)
    for special in ("test_type", "verb", "_runID"):
        if special in order:
            order.remove(special)
            order.insert(0, special)
    return order

def normalize_to_grid(data: Any) -> Dict[str, Any]:
    """
    Return {headers, rows}. Accepts:
      - list[dict]         (already table-ish)
      - list[list]         (header row + body)
      - dict[str, list]    (object-of-arrays)
      - dict               (single row)
      - str                (JSON or NDJSON)
      - scalar             (wrapped as {"value": scalar})
    Shallow-flattens nested dicts; stringifies non-primitive leaves.
    """
    rows: List[Dict[str, Any]] = []

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            acc: List[Any] = []
            for line in data.splitlines():
                t = line.strip()
                if not t:
                    continue
                try:
                    acc.append(json.loads(t))
                except Exception:
                    acc.append({"value": t})
            data = acc

    if isinstance(data, list):
        if all(isinstance(x, dict) for x in data):
            rows = [_flatten_once(x) for x in data]  # type: ignore
        elif data and isinstance(data[0], list):
            header = [str(h) if h is not None else "" for h in data[0]]  # type: ignore
            for arr in data[1:]:
                rows.append({
                    header[i] if i < len(header) else f"col_{i+1}":
                    (arr[i] if i < len(arr) else None)
                    for i in range(max(len(header), len(arr)))
                })
        else:
            rows = [{"value": v} for v in data]
    elif isinstance(data, dict):
        if all(isinstance(v, list) for v in data.values()):
            rows = _object_of_arrays_to_rows(data)
        else:
            rows = [_flatten_once(data)]
    else:
        rows = [{"value": data}]

    headers = _rows_headers_union(rows)
    out_rows: List[Dict[str, Any]] = []
    for r in rows:
        o: Dict[str, Any] = {}
        for h in headers:
            v = r.get(h, "")
            if v is None or isinstance(v, (str, int, float, bool)):
                o[h] = v if v is not None else ""
            else:
                try:
                    o[h] = json.dumps(v, ensure_ascii=False)
                except Exception:
                    o[h] = str(v)
        out_rows.append(o)
    return {"headers": headers, "rows": out_rows}

# ------------------------- Parsers -------------------------

def parse_csv_bytes(b: bytes) -> List[Dict[str, Any]]:
    text = b.decode("utf-8", errors="replace")
    rdr = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in rdr]

def parse_xlsx_bytes(b: bytes, max_sheets: int = 1, max_rows: int = 50000) -> List[Dict[str, Any]]:
    if not HAVE_PANDAS:
        raise HTTPException(500, "pandas/openpyxl not installed on server")
    if len(b) > 10 * 1024 * 1024:
        raise HTTPException(413, "Excel file too large (>10MB)")
    xls = pd.ExcelFile(io.BytesIO(b), engine="openpyxl")
    out_rows: List[Dict[str, Any]] = []
    for name in xls.sheet_names[:max_sheets]:
        df = xls.parse(name, dtype=str).fillna("")
        out_rows.extend(df.to_dict(orient="records")[:max_rows])
        break
    return out_rows

def run_sqlite_query(db_path: str, sql: str, max_rows: int = 50000) -> List[Dict[str, Any]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql)
        rows = cur.fetchmany(max_rows)
        return [dict(r) for r in rows]
    finally:
        con.close()

# ------------------------- Atomic write -------------------------

def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), prefix=".tmp_", encoding="utf-8") as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        try:
            if bak.exists():
                bak.unlink()
            path.replace(bak)
        except Exception:
            pass
    tmp_path.replace(path)

# ------------------------- Security helpers -------------------------

_SLUG = re.compile(r"^[\w\-\s\(\)\.]+$")  # no slashes or traversal

def _assert_slug(s: str, field: str):
    if not _SLUG.match(s):
        raise HTTPException(400, f"Invalid {field}")

def _get_project_path(project: str) -> Path:
    """
    Return the filesystem path to a project folder.
    We still resolve project root as '<repo>/projects/{project}',
    but *all file paths* beneath that use resolve_path(...).
    """
    base = Path("projects")
    p = base / project
    if not p.exists():
        raise HTTPException(404, f"Project not found: {project}")
    return p

# ------------------------- Routes -------------------------

@router.post("/normalize")
async def normalize_payload(
    source_type: str = Body(..., embed=True, description="json|csv|xlsx|ndjson"),
    content: Any = Body(..., embed=True, description="Raw content or already-parsed JSON"),
) -> Dict[str, Any]:
    try:
        t = source_type.lower()
        if t == "json":
            return normalize_to_grid(content)
        if t == "ndjson":
            if not isinstance(content, str):
                raise HTTPException(400, "NDJSON must be a string")
            return normalize_to_grid(content)
        if t == "csv":
            if not isinstance(content, str):
                raise HTTPException(400, "CSV must be a UTF-8 string")
            rows = parse_csv_bytes(content.encode("utf-8", errors="replace"))
            return normalize_to_grid(rows)
        if t == "xlsx":
            if not isinstance(content, (str, bytes)):
                raise HTTPException(400, "XLSX must be base64 string or bytes")
            b = content if isinstance(content, bytes) else io.BytesIO(bytes(content, "latin1")).getvalue()
            rows = parse_xlsx_bytes(b)
            return normalize_to_grid(rows)
        raise HTTPException(400, f"Unsupported source_type: {source_type}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Normalization error: {e}")

@router.post("/upload")
async def upload_file(type: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    """
    Multipart endpoint for files. Example:
      POST /grid/upload?type=csv|xlsx|ndjson|json
    Returns {headers, rows}.
    """
    b = await file.read()
    try:
        t = type.lower()
        if t == "csv":
            rows = parse_csv_bytes(b)
            return normalize_to_grid(rows)
        if t == "xlsx":
            rows = parse_xlsx_bytes(b)
            return normalize_to_grid(rows)
        if t == "ndjson":
            text = b.decode("utf-8", errors="replace")
            return normalize_to_grid(text)
        if t == "json":
            text = b.decode("utf-8", errors="replace")
            parsed = json.loads(text)
            return normalize_to_grid(parsed)
        raise HTTPException(400, f"Unsupported type: {type}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Upload parse error: {e}")

@router.get("/load/{project}/{group}/{run_id}")
async def load_grid(project: str, group: str, run_id: str) -> Dict[str, Any]:
    """
    Load verbs/{group}/data_dumps/{run_id}/DataEntry.json under projects/{project},
    but via resolver: resolve_path(project_path, "data_entry", verb_group=group, run_id=run_id).
    Returns normalized {headers, rows}. Missing file -> empty grid.
    """
    _assert_slug(project, "project")
    _assert_slug(group, "group")
    _assert_slug(run_id, "run_id")

    proj_path = _get_project_path(project)
    data_entry_path = resolve_path(proj_path, "data_entry", verb_group=group, run_id=run_id)

    if not data_entry_path.exists():
        return {"headers": [], "rows": []}

    try:
        data = load_data(data_entry_path)
        if data is None:
            return {"headers": [], "rows": []}
    except Exception as e:
        raise HTTPException(400, f"Load error: {e}")

    return normalize_to_grid(data)

@router.post("/save")
async def save_grid(
    path: str = Body(...),
    payload: Dict[str, Any] = Body(...)
) -> Dict[str, Any]:
    """
    Write payload.rows to an explicit path (absolute/relative).
    Prefer the scoped /save/{project}/{group}/{run_id} endpoint.
    """
    try:
        p = Path(path)
        if p.is_dir():
            raise HTTPException(400, "Path points to a directory")
        rows = payload.get("rows", [])
        if not isinstance(rows, list):
            raise HTTPException(400, "payload.rows must be a list")
        _atomic_write_json(p, rows)
        return {"ok": True, "saved": len(rows), "path": str(p)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Save error: {e}")

@router.post("/save/{project}/{group}/{run_id}")
async def save_grid_scoped(
    project: str,
    group: str,
    run_id: str,
    payload: Dict[str, Any] = Body(...)
) -> Dict[str, Any]:
    """
    Safer scoped save: resolve DataEntry.json under the project via resolver,
    then atomically replace it with a .bak.
    """
    _assert_slug(project, "project")
    _assert_slug(group, "group")
    _assert_slug(run_id, "run_id")

    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        raise HTTPException(400, "payload.rows must be a list")

    proj_path = _get_project_path(project)
    data_entry_path = resolve_path(proj_path, "data_entry", verb_group=group, run_id=run_id)

    try:
        _atomic_write_json(data_entry_path, rows)
        return {"ok": True, "saved": len(rows), "path": str(data_entry_path)}
    except Exception as e:
        raise HTTPException(400, f"Save error: {e}")

@router.post("/sql/sqlite")
async def sql_sqlite(
    db_path: str = Body(...),
    sql: str = Body(...),
    max_rows: int = Body(50000)
) -> Dict[str, Any]:
    """
    Minimal, safe-ish SQL path (SQLite only here).
    For other engines, wrap through SQLAlchemy with whitelisted DSNs.
    """
    try:
        if ";" in sql.replace(";;", ""):
            # naive guard against stacked statements
            raise HTTPException(400, "Only a single SQL statement is allowed")
        rows = run_sqlite_query(db_path, sql, max_rows=max_rows)
        return normalize_to_grid(rows)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"SQL error: {e}")

@router.post("/generate_id")
async def generate_id(
    project_root: str = Body(...),
    noun_type: str = Body(...),
    existing_ids: List[str] = Body(default=[]),
):
    """
    Delegate to utils.id_generator.generate_autogenerated_id() using your noun schema.
    (Unchanged; left as-is to match your current utils contract.)
    """
    try:
        from utils.id_generator import generate_autogenerated_id
        proj = Path(project_root)
        noun_defs = json.loads((proj / "noun_types.json").read_text(encoding="utf-8"))
        noun_schema = noun_defs[noun_type]
        new_id = generate_autogenerated_id(
            noun_type_name=noun_type,
            noun_schema=noun_schema,
            noun_types_path=proj / "noun_types.json",
            existing_ids=set(existing_ids),
        )
        return {"id": new_id}
    except Exception as e:
        raise HTTPException(400, f"ID generation error: {e}")
