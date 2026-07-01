# modules/camera_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.camera import camera_node
from nodes.compliance.camera import compliance_camera_node

camera_module = make_standard_module(
    name='Camera',
    route='/camera',
    page_node=camera_node,
    compliance_node=compliance_camera_node,
    description='UI node that serves gui/components/camera.html',
)
