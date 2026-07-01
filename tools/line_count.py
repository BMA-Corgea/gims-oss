from pathlib import Path

EXCLUDES = {
    "__pycache__",
    "node_modules",
    ".git",
    ".venv",
    ".pytest_cache",
    "dist",
    "build",
    "gims-electron",
}
EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css"}
SPECIAL_DIRS = {"utils", "tools", "docker"}

def count_lines(root="."):
    totals = {
        "total": 0,
        "special": 0,
        "remainder": 0,
    }

    for path in Path(root).rglob("*"):
        if path.is_file() and path.suffix in EXTS:
            if any(part in EXCLUDES for part in path.parts):
                continue

            try:
                with open(path, "r", errors="ignore") as f:
                    count = sum(1 for _ in f)
            except Exception:
                continue

            totals["total"] += count
            if any(part in SPECIAL_DIRS for part in path.parts):
                totals["special"] += count
            else:
                totals["remainder"] += count

    return totals

if __name__ == "__main__":
    totals = count_lines(".")
    print("Total lines of code:", totals["total"])
    print("Lines in utils/tools/meta/docker:", totals["special"])
    print("Lines in remainder:", totals["remainder"])
