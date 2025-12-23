# core/orchestration/registry.py
from __future__ import annotations

from typing import Iterable

from fastapi import FastAPI

from .module import Module
from .node import NodeKind


class ModuleRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, Module] = {}

    # --- CRUD ---
    def register(self, module: Module) -> None:
        self._modules[module.name] = module

    def unregister(self, name: str) -> None:
        self._modules.pop(name, None)

    def get(self, name: str) -> Module:
        return self._modules[name]

    # --- Query ---
    def list(self) -> list[str]:
        return list(self._modules.keys())

    def list_accessible(self, user_roles: Iterable[str] | None = None) -> list[str]:
        return [
            m.name
            for m in self._modules.values()
            if m.is_accessible_by(user_roles)
        ]

    def all(self) -> list[Module]:
        return list(self._modules.values())

    # --- Orchestration ---
    def mount_all(
        self,
        app: FastAPI,
        *,
        prefix_map: dict[str, str] | None = None,
    ) -> None:
        for mod in self._modules.values():
            prefix = ""
            if prefix_map and mod.name in prefix_map:
                prefix = prefix_map[mod.name]
            mod.mount(app, prefix=prefix)

    # Gather injections for a target path across modules,
    # but ONLY from modules that explicitly inject for that target.
    #
    # Ordering:
    #   0) /login/inject.js (if the participating module ships LOGIN/RULES)
    #   1) infrastructure injects (provided by INFRASTRUCTURE/LOGIN/RULES nodes of participating modules)
    #   2) everything else (UI/app assets, including STATE/UI node provides_inject from participating modules)
    def gather_injections(self, target: str) -> dict[str, list[str]]:
        scripts: list[str] = []
        styles: list[str] = []

        # Modules that actually contribute to this target
        participating: list[Module] = []

        # First pass: collect explicit module-level injections and identify participants
        for mod in self._modules.values():
            inj = mod.injections_for(target)
            mod_scripts = inj.get("scripts", []) or []
            mod_styles = inj.get("stylesheets", []) or []

            if mod_scripts or mod_styles:
                participating.append(mod)
                scripts.extend(mod_scripts)
                styles.extend(mod_styles)

        # Nothing participates? Return what we have (usually nothing).
        if not participating:
            return {"scripts": list(dict.fromkeys(scripts)), "stylesheets": list(dict.fromkeys(styles))}

        # Second pass: from participating modules only, collect node-provided injects
        infra_injects: set[str] = set()   # from INFRASTRUCTURE/LOGIN/RULES nodes
        extra_ui_injects: list[str] = []  # from any other node kinds (STATE/UI/etc.)
        ships_login_or_rules = False

        for mod in participating:
            for n in mod.nodes.values():
                if n.kind in (NodeKind.LOGIN, NodeKind.RULES):
                    ships_login_or_rules = True

                meta = n.meta or {}
                prov = meta.get("provides_inject")
                if not prov:
                    continue

                # normalize to iterable of strings
                if isinstance(prov, str):
                    prov = [prov]
                for p in prov:
                    if not isinstance(p, str) or not p.strip():
                        continue
                    p = p.strip()
                    if n.kind in (NodeKind.INFRASTRUCTURE, NodeKind.LOGIN, NodeKind.RULES):
                        infra_injects.add(p)
                    else:
                        # STATE/UI/etc. contributes UI-level assets
                        extra_ui_injects.append(p)

        # If any participating module ships LOGIN/RULES, ensure login helper is present
        if ships_login_or_rules and "/login/inject.js" not in scripts:
            scripts.append("/login/inject.js")

        # Merge infra injects next (dedup naturally later)
        for p in infra_injects:
            if p not in scripts:
                scripts.append(p)

        # Merge extra UI injects last
        for p in extra_ui_injects:
            if p not in scripts:
                scripts.append(p)

        # De-dup while preserving order
        def dedupe(seq: list[str]) -> list[str]:
            seen = set()
            out: list[str] = []
            for x in seq:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
            return out

        scripts = dedupe(scripts)
        stylesheets = dedupe(styles)

        # Final ordering: login first, then infra, then everything else
        index_map = {s: i for i, s in enumerate(scripts)}

        def is_login(path: str) -> bool:
            return path.endswith("/login/inject.js") or path == "/login/inject.js"

        def is_infra(path: str) -> bool:
            return path in infra_injects

        def priority(path: str) -> tuple[int, int]:
            if is_login(path):
                return (0, index_map[path])
            if is_infra(path):
                return (1, index_map[path])
            return (2, index_map[path])

        scripts.sort(key=priority)

        return {
            "scripts": scripts,
            "stylesheets": stylesheets,
        }


# Global singleton used by the app
registry = ModuleRegistry()
