# core/orchestration/module.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Dict, List

from fastapi import FastAPI

from .node import Node, kebab


@dataclass(slots=True)
class Module:
    """
    A Module is a named bundle of Nodes (e.g., 'Verb Workbench' = UI + API + RULES).
    Use `roles` to gate access (empty set = public).

    `inject` is a module-level asset injection manifest:
      {
        "/launcher": {
          "scripts": ["/login/inject.js"],
          "stylesheets": ["/some/optional.css"]
        },
        "/deep-search": {
          "scripts": [...],
          "stylesheets": [...]
        }
      }
    """
    name: str
    nodes: Dict[str, Node] | List[Node]
    version: str = "0.1.0"
    description: str = ""
    roles: set[str] = field(default_factory=set)
    meta: dict[str, Any] = field(default_factory=dict)
    inject: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.nodes, list):
            self.nodes = {n.name: n for n in self.nodes}
        if not self.nodes:
            raise ValueError("Module must contain at least one Node.")
        # normalize inject structure
        for target, cfg in list(self.inject.items()):
            self.inject[target] = {
                "scripts": list(cfg.get("scripts", [])),
                "stylesheets": list(cfg.get("stylesheets", [])),
            }

    @property
    def slug(self) -> str:
        return kebab(self.name)

    def get(self, node_name: str) -> Node:
        return self.nodes[node_name]

    def list_nodes(self) -> list[str]:
        return list(self.nodes.keys())

    def mount(self, app: FastAPI, prefix: str = "") -> None:
        for node in self.nodes.values():
            node.mount(app, prefix=prefix)

    def is_accessible_by(self, user_roles: Iterable[str] | None) -> bool:
        if not self.roles:
            return True
        if not user_roles:
            return False
        return bool(set(user_roles) & self.roles)

    # NEW: module-provided injections for a given target path
    def injections_for(self, target: str) -> dict[str, list[str]]:
        return self.inject.get(target, {"scripts": [], "stylesheets": []})
        