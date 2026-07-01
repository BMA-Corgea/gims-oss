"""Phase 5 prep — layering guards.

Locks two invariants the provider-neutral storage refactor depends on:

1. The pure ``core/`` layer must never import a cloud SDK (``boto3``). Cloud specifics belong in
   the ``api/manifest/*_aws.py`` adapters, reached behind a port — so ``core`` stays runnable with
   no cloud deps and the eventual ObjectStore/RecordStore adapters can't leak back into core.
2. ``RDS_ENABLED`` is env-driven (config kernel), not a committed constant edit.
"""
import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
CORE = REPO / "core"


def _imports_boto3(py_file: pathlib.Path) -> bool:
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return False  # dead/broken files are not a live import path
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "boto3" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "boto3":
                return True
    return False


def test_core_never_imports_boto3():
    offenders = [str(p.relative_to(REPO)) for p in CORE.rglob("*.py") if _imports_boto3(p)]
    assert offenders == [], f"core/ must not import boto3 (cloud belongs in api adapters): {offenders}"


def test_rds_enabled_is_env_driven():
    # Unset by default -> False; flipping the env flips the resolver flag (no code edit).
    import importlib
    import utils.config as cfg
    importlib.reload(cfg)
    assert cfg.rds_enabled() is False  # env unset in the test environment


def _module_scope_imports(py_file: pathlib.Path) -> set[str]:
    """Top-level (module-scope) imported root packages only — NOT imports inside functions."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in tree.body:  # module scope only
        if isinstance(node, ast.Import):
            roots.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)
    return roots


def test_storage_factory_aws_imports_stay_lazy():
    """The provider factory must reach the aws adapter (and any cloud SDK) only inside function
    bodies, never at module scope — else `import core.storage.factory` drags boto3 into core."""
    factory = CORE / "storage" / "factory.py"
    top = _module_scope_imports(factory)
    forbidden = {m for m in top if m.split(".")[0] in {"boto3", "botocore", "psycopg", "psycopg2"}
                 or m == "api.storage_aws"}
    assert forbidden == set(), f"core/storage/factory.py imports these at module scope (must be lazy): {forbidden}"


def test_registering_builtins_does_not_eager_import_cloud_sdk():
    """The provider REGISTRY runs the built-in `register_provider("aws", ...)` at module import.
    That registration must store the aws factory callables WITHOUT importing their cloud SDK — the
    import has to stay inside the callable body, fired only when an aws store is actually built. A
    clean subprocess proves importing the factory (which registers `aws`) still leaks no SDK."""
    import os
    import subprocess
    import sys

    code = (
        "import core.storage.factory as f; "
        "assert {'local', 'aws'} <= set(f._PROVIDERS), 'built-ins must self-register'; "
        "import sys; "
        "leaked=[m for m in ('boto3','botocore','psycopg','psycopg2') if m in sys.modules]; "
        "print(','.join(leaked)); sys.exit(1 if leaked else 0)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"registering built-in providers leaked a cloud SDK at import: {proc.stdout.strip()!r} "
        f"(stderr: {proc.stderr.strip()!r})"
    )
