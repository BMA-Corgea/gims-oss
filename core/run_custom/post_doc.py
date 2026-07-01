# core/run_custom/post_doc.py
from __future__ import annotations
from ._common import log


# ---- Light "concert pat-down" for post-doc -----------------------------------
def _run_post_doc_safe(entry: str, args: dict, env: dict, *, output_dir: str, safety: str = "light", timeout_s: int = 20, tool_module=None):
    """
    Executes module:function with common-sense guards.

    HARD REQUIREMENT:
      - All writes must be inside the project's prepositional_phrase_output_dir
        (resolved by the caller and passed as env["prepositional_phrase_output_dir"]).
      - Cleans up _prepared directory after successful execution

    Notes:
      - We do NOT resolve paths here (no I/O in core). The caller must supply:
          env["prepositional_phrase_output_dir"]  -> absolute path anchor for writes
          env["canonical_phrase_base"]            -> "custom/prepositional phrases" absolute base
          env["pphrase_name"]                     -> the phrase folder name
          env["project_path"]                     -> project root
      - We pass the expected kwargs to the post-doc function:
          output_root, phrase_root, project_path, context
      - Resource limits are applied INSIDE the worker thread to avoid "can't start new thread".
    """
    from importlib import import_module as _imp
    import builtins, os, threading
    from pathlib import Path
    import socket as _socket
    import subprocess as _subprocess
    import shutil as _shutil

    log.debug("[post_doc] begin | entry=", entry, "safety=", safety, "timeout_s=", timeout_s, "output_dir=", output_dir)

    # ---------- resolve anchors from env (no filesystem operations here) ----------
    phrase_out_dir = env.get("prepositional_phrase_output_dir")
    if not phrase_out_dir:
        raise RuntimeError("post_doc: missing prepositional_phrase_output_dir in env")
    allowed_root = Path(phrase_out_dir).resolve()

    canonical_base = env.get("canonical_phrase_base") or ""
    pphrase_name   = env.get("pphrase_name") or ""
    # phrase_root is the folder where the template for this phrase lives:
    phrase_root = Path(str(canonical_base)) / str(pphrase_name) if (canonical_base and pphrase_name) else Path(str(canonical_base))

    # Track _prepared location for cleanup
    prepared_dir = allowed_root / "_prepared"

    # ---------- direct/unsafe path ----------
    if safety == "off":
        log.debug("[post_doc] safety=off (no guards)")
        mod_name, fn_name = entry.split(":", 1)
        target_mod = tool_module if (
            tool_module
            and (
                mod_name == getattr(tool_module, "__name__", "")
                or mod_name == Path(getattr(tool_module, "__file__", "")).stem
            )
        ) else _imp(mod_name)
        fn = getattr(target_mod, fn_name)

        call_args = {
            "output_root": str(allowed_root),           # write under canonical phrase output dir
            "phrase_root": str(phrase_root),            # where template lives
            "project_path": str(env.get("project_path") or ""),
            "context": env,                              # pass full env as context
            **(args or {}),
        }
        ret = fn(env, **call_args)
        log.debug("[post_doc] done (off) ->", type(ret).__name__)
        
        # Clean up _prepared if it exists
        if prepared_dir.exists():
            try:
                # shutil is not monkey-patched in 'off' mode, so this is safe.
                _shutil.rmtree(prepared_dir)
                log.debug("[post_doc] cleaned up _prepared directory")
            except Exception as e:
                log.debug(f"[post_doc] warning: failed to clean up _prepared: {e}")
        
        return ret

    # ---------- safe path ----------
    def _is_under_allowed(path: str) -> bool:
        rp = Path(path).resolve()
        try:
            rp.relative_to(allowed_root)
            return True
        except Exception:
            return False

    # monkeypatch targets
    _orig_open   = builtins.open
    _orig_popen  = _subprocess.Popen
    _orig_system = os.system
    _orig_socket = _socket.socket
    _orig_rmtree = _shutil.rmtree
    _orig_remove = os.remove
    _orig_unlink = os.unlink
    _orig_rmdir  = os.rmdir

    def _blocked(*a, **kw):
        log.debug("[post_doc][guard] blocked call")
        raise RuntimeError("post_doc: operation blocked by safety policy")

    def _guard_open(file, mode="r", *a, **kw):
        if any(m in str(mode) for m in ("w", "a", "+")):
            if not _is_under_allowed(str(file)):
                log.debug("[post_doc][guard] write blocked (outside allowed root):", file)
                raise RuntimeError(f"post_doc: write must be under prepositional_phrase_output_dir: {file}")
        return _orig_open(file, mode, *a, **kw)

    def _guard_rmtree(path, *a, **kw):
        if not _is_under_allowed(str(path)):
            log.debug("[post_doc][guard] rmtree blocked (outside allowed root):", path)
            raise RuntimeError(f"post_doc: rmtree must be under prepositional_phrase_output_dir: {path}")
        return _orig_rmtree(path, *a, **kw)

    def _guard_remove(path, *a, **kw):
        if not _is_under_allowed(str(path)):
            log.debug("[post_doc][guard] remove blocked (outside allowed root):", path)
            raise RuntimeError(f"post_doc: remove must be under prepositional_phrase_output_dir: {path}")
        return _orig_remove(path, *a, **kw)

    def _guard_unlink(path, *a, **kw):
        if not _is_under_allowed(str(path)):
            log.debug("[post_doc][guard] unlink blocked (outside allowed root):", path)
            raise RuntimeError(f"post_doc: unlink must be under prepositional_phrase_output_dir: {path}")
        return _orig_unlink(path, *a, **kw)

    def _guard_rmdir(path, *a, **kw):
        if not _is_under_allowed(str(path)):
            log.debug("[post_doc][guard] rmdir blocked (outside allowed root):", path)
            raise RuntimeError(f"post_doc: rmdir must be under prepositional_phrase_output_dir: {path}")
        return _orig_rmdir(path, *a, **kw)

    # resolve callable
    mod_name, fn_name = entry.split(":", 1)
    target_mod = tool_module if (
        tool_module
        and (
            mod_name == getattr(tool_module, "__name__", "")
            or mod_name == Path(getattr(tool_module, "__file__", "")).stem
        )
    ) else _imp(mod_name)
    fn = getattr(target_mod, fn_name)

    _saved_env = dict(os.environ)
    execution_success = False
    return_value = None
    try:
        # neuter env for network/subprocess surprises
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("NO_PROXY", None)
        os.environ["PATH"] = "/usr/bin:/bin"

        # guards
        builtins.open     = _guard_open
        _subprocess.Popen = _blocked
        os.system         = _blocked
        _socket.socket    = _blocked
        _shutil.rmtree    = _guard_rmtree
        os.remove         = _guard_remove
        os.unlink         = _guard_unlink
        os.rmdir          = _guard_rmdir

        err = {"exc": None}
        ret_holder = {"ret": None}

        def _runner():
            try:
                call_args = {
                    "output_root": str(allowed_root),
                    "phrase_root": str(phrase_root),
                    "project_path": str(env.get("project_path") or ""),
                    "context": env,
                    **(args or {}),
                }
                ret_holder["ret"] = fn(env, **call_args)
            except Exception as ex:
                err["exc"] = ex

        th = threading.Thread(target=_runner, daemon=True)
        th.start()
        th.join(timeout=timeout_s)
        if th.is_alive():
            log.debug("[post_doc][error] timeout")
            raise TimeoutError(f"post_doc: timed out after {timeout_s}s")
        if err["exc"]:
            log.debug("[post_doc][error] raised:", repr(err["exc"]))
            raise err["exc"]
        
        # If we reach here, the guarded execution was successful.
        execution_success = True
        return_value = ret_holder["ret"]
        log.debug("[post_doc] ok ->", type(return_value).__name__)

    finally:
        # ALWAYS restore the original functions, regardless of success or failure.
        os.environ.clear()
        os.environ.update(_saved_env)
        builtins.open     = _orig_open
        _subprocess.Popen = _orig_popen
        os.system         = _orig_system
        _socket.socket    = _orig_socket
        _shutil.rmtree    = _orig_rmtree
        os.remove         = _orig_remove
        os.unlink         = _orig_unlink
        os.rmdir          = _orig_rmdir
        log.debug("[post_doc] guards restored")

    # --- Cleanup Phase ---
    # This code runs AFTER the 'finally' block has restored all functions.
    if execution_success and prepared_dir.exists():
        try:
            # Now we are calling the original, unguarded shutil.rmtree.
            _shutil.rmtree(prepared_dir)
            log.debug("[post_doc] cleaned up _prepared directory successfully")
        except Exception as e:
            log.debug(f"[post_doc] warning: failed to clean up _prepared directory: {e}")

    return return_value
