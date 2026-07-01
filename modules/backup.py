# modules/backup_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.backup import backup_node

backup_module = make_standard_module(
    name='Backup',
    route='/backup',
    page_node=backup_node,
    description='UI node that serves gui/components/backup.html',
)
