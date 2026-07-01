# modules/custom_upload_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.custom_upload import custom_upload_node
from nodes.compliance.custom_upload import compliance_custom_upload_node

custom_upload_module = make_standard_module(
    name='Custom Upload',
    route='/custom_upload',
    page_node=custom_upload_node,
    compliance_node=compliance_custom_upload_node,
    description='UI node that serves gui/components/custom_upload.html',
)
