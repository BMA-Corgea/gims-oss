# tools/monitoring_test.py

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import json
from utils.monitoring import check_next_step, evaluate_condition

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python3 tools/monitoring_test.py <noun_type> <adjective> <instance_id>")
        sys.exit(1)

    noun_type, adjective_field, instance_id = sys.argv[1:]
    project_path = Path("projects") / "LIMS-System"

    # Load noun schema and find the adjective
    noun_defs = json.load(open(project_path / "noun_types.json"))
    schema = noun_defs.get(noun_type, {})
    fields = schema.get("fields", {})
    if adjective_field not in fields or fields[adjective_field].get("adjective_class") != "ActionRequirement":
        print(f"❌ '{adjective_field}' is not a valid ActionRequirement for {noun_type}.")
        sys.exit(1)

    # Load adjective config
    adj_list = json.load(open(project_path / "adjective_types.json"))
    ar_config = next((a for a in adj_list if a["adjective"] == adjective_field), None)
    if not ar_config:
        print(f"❌ No config for adjective '{adjective_field}'.")
        sys.exit(1)

    request_options = ar_config.get("request_options", {})
    # Load the instance record
    items_path = project_path / "nouns" / noun_type / "items.jsonl"
    instance = None
    with open(items_path) as f:
        for line in f:
            obj = json.loads(line)
            if obj.get(f"{noun_type.lower()}_id") == instance_id:
                instance = obj
                break
    if not instance:
        print(f"❌ No {noun_type} found with ID '{instance_id}'.")
        sys.exit(1)

    request_label = instance.get(adjective_field)
    verbs = request_options.get(request_label, [])
    if not verbs:
        print(f"❌ No verbs mapped for request '{request_label}'.")
        sys.exit(1)

    print(f"{noun_type} {instance_id} requests: {request_label}\n")

    # Preload verb definitions
    verb_defs = json.load(open(project_path / "verb_types.json"))

    for verb in verbs:
        print(f"🔎 Verb: {verb}")
        try:
            next_steps = check_next_step(
                project_path=project_path,
                source_noun_type=noun_type,
                source_id=instance_id,
                required_verb=verb
            )
        except Exception as e:
            print(f"  ❌ Error finding next steps: {e}")
            continue

        # Determine raw_data_inputs for this verb
        verb_def = verb_defs.get(verb, {})
        raw_inputs = (
            verb_def
            .get("data_entry_schema", {})
            .get("raw_data_inputs", [])
        )

        for step in next_steps:
            linked = step["linked_id"]
            run_id = step["run_id"]
            print(f"  • Item {linked}: run {run_id or '(none)'}")
            if run_id:
                verb_group = verb_def.get("verb_group")
                evaluate_condition(
                    project_path=project_path,
                    run_id=run_id,
                    verb_group=verb_group,
                    noun_schema=schema,
                    raw_inputs=raw_inputs
                )
