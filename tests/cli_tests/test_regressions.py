def test_reference_adjective_can_load_from_edit_flow():
    entry = {
        "adjective": "Elbow_Submission",
        "adjective_class": "Reference",
        "applies_to": ["Elbows"],
        "reference_noun": "Submission"
    }
    from utils.handlers.adjective import ReferenceAdjective
    handler = ReferenceAdjective(
        entry,
        noun_type="Elbows",
        project_name="LIMS-System",
    )
    assert handler is not None