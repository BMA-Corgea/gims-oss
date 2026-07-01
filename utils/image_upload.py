from flask import Blueprint, request, jsonify
import os
import json
import logging
from datetime import datetime
from PIL import Image
import io

log = logging.getLogger(__name__)

upload_bp = Blueprint('upload', __name__)

@upload_bp.route('/list_nouns/<project_name>', methods=['GET'])
def list_nouns_with_picture(project_name):
    """
    Returns nouns that have a field with adjective_class 'Picture'
    from projects/{project_name}/noun_types.json
    """
    noun_types_path = os.path.join("projects", project_name, "noun_types.json")
    if not os.path.exists(noun_types_path):
        return jsonify([])

    with open(noun_types_path) as f:
        noun_types = json.load(f)

    nouns_with_image = []
    for noun_name, noun_def in noun_types.items():
        fields = noun_def.get("fields", {})
        for field_name, field_def in fields.items():
            if field_def.get("adjective_class") == "Picture":
                nouns_with_image.append(noun_name)
                break

    return jsonify(nouns_with_image)

@upload_bp.route('/upload_image', methods=['POST'])
def upload_image():

    project_name = request.form.get('project_name')
    noun_name = request.form.get('noun_name')
    item_id = request.form.get('item_id')

    if not project_name:
        return "❌ project_name is required.", 400
    if not noun_name:
        return "❌ noun_name is required.", 400
    if not item_id:
        return "❌ item_id is required.", 400

    file = request.files.get('photo')
    if not file or file.filename == '':
        return "❌ No file part", 400

    # Validate file type
    allowed_types = {'image/jpeg', 'image/png', 'image/webp'}
    if file.mimetype not in allowed_types:
        return "❌ Invalid file type. Allowed: JPEG, PNG, WEBP.", 400

    # Load image with Pillow
    try:
        image = Image.open(file.stream)
        # Decompression bomb protection
        max_pixels = 100 * 1000000  # e.g. 100 megapixels
        if image.width * image.height > max_pixels:
            return "❌ Image too large, exceeds pixel limit.", 400
    except Exception as e:
        log.warning("image_upload: failed to open image", exc_info=True)
        return f"❌ Failed to open image: {e}", 400

    # Resize to width=1080px while keeping aspect ratio
    max_width = 1080
    if image.width > max_width:
        ratio = max_width / float(image.width)
        new_height = int(float(image.height) * ratio)
        image = image.resize((max_width, new_height), Image.LANCZOS)

    # Save resized image to a BytesIO buffer to check size
    buffer = io.BytesIO()
    # Determine format
    if file.mimetype == 'image/jpeg':
        image_format = 'JPEG'
        image.save(buffer, format=image_format, optimize=True, quality=85)
    elif file.mimetype == 'image/png':
        image_format = 'PNG'
        image.save(buffer, format=image_format, optimize=True)
    elif file.mimetype == 'image/webp':
        image_format = 'WEBP'
        image.save(buffer, format=image_format, quality=85)
    else:
        return "❌ Unsupported image format.", 400

    buffer.seek(0)

    # Check resized image size (max 3MB)
    if buffer.getbuffer().nbytes > 3 * 1024 * 1024:
        return "❌ Resized file still too large. Try a smaller original or adjust compression.", 400

    # Build save path within projects/(project_name)/nouns/(noun_name)/images
    images_dir = os.path.join("projects", project_name, "nouns", noun_name, "images")
    os.makedirs(images_dir, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    from werkzeug.utils import secure_filename

    filename = secure_filename(file.filename)
    filename = f"{timestamp}_{filename}"
    file_path = os.path.join(images_dir, filename)

    # Save final image
    with open(file_path, 'wb') as out_file:
        out_file.write(buffer.read())

    # Calculate relative path from project root
    rel_path = os.path.relpath(file_path, start=os.path.join("projects", project_name))

    # --- Update items.jsonl and DataEntry.json for this run ---
    items_path = os.path.join("projects", project_name, "nouns", noun_name, "items.jsonl")
    noun_types_path = os.path.join("projects", project_name, "noun_types.json")

    if not os.path.exists(noun_types_path):
        return "❌ noun_types.json not found.", 400

    with open(noun_types_path) as f:
        noun_types = json.load(f)
    noun_def = noun_types.get(noun_name)
    if not noun_def:
        return f"❌ Noun {noun_name} not defined.", 400

    # find the image adjective field and primary‐ID field
    image_field = None
    primary_id = noun_def.get("primary_id_field")
    for fld, fld_def in noun_def.get("fields", {}).items():
        if fld_def.get("adjective_class") == "Picture":
            image_field = fld
            break
    if not image_field or not primary_id:
        return "❌ Missing Picture adjective or primary_id_field in schema.", 400

    # -- 1) patch items.jsonl and retrieve run_id --
    updated = False
    run_id = None
    lines = []
    with open(items_path) as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get(primary_id) == item_id:
                entry[image_field] = rel_path
                run_id = entry.get("_runID")
                updated = True
            lines.append(entry)

    if not updated:
        return f"❌ Item {item_id} not found in items.jsonl.", 400

    with open(items_path, "w") as f:
        for entry in lines:
            f.write(json.dumps(entry) + "\n")

    # -- 2) patch DataEntry.json for this runID --
    dataentry_updated = False
    dataentry_files_checked = 0
    if run_id:
        import glob
        pattern = os.path.join(
            "projects", project_name, "verbs", "*",
            "data_dumps", run_id, "DataEntry.json"
        )
        for data_file in glob.glob(pattern):
            dataentry_files_checked += 1
            try:
                with open(data_file) as f:
                    data = json.load(f)
                changed = False
                for rec in data:
                    if rec.get(primary_id) == item_id:
                        rec[image_field] = rel_path
                        changed = True
                if changed:
                    with open(data_file, "w") as f:
                        json.dump(data, f, indent=2)
                    dataentry_updated = True
            except Exception:
                # Do not silently swallow: a failed DataEntry write means the image
                # reference was not recorded. Log with the offending file + item so the
                # failure is diagnosable instead of vanishing (R10).
                log.warning(
                    "image_upload: failed to update DataEntry file %s for item %s",
                    data_file, item_id, exc_info=True,
                )

    # Emit signal file to trigger refresh in Textual UI
    signal_path = os.path.join("projects", project_name, "refresh.signal")
    with open(signal_path, "w") as f:
        f.write(f"refresh:{datetime.now().isoformat()}\n")

    # -- Return full thought process --
    result_message = f"✅ Image saved at: {file_path}, recorded under item '{item_id}'.\n"
    result_message += f"📁 items.jsonl updated: {updated}\n"
    result_message += f"✏️ DataEntry.json updated: {dataentry_updated}"

    return result_message

@upload_bp.route('/list_items/<project_name>/<noun_name>', methods=['GET'])
def list_items(project_name, noun_name):
    """
    Returns a list of primary IDs for a given noun type, filtered by run ID if provided
    """
    run_id = request.args.get('run')

    noun_types_path = os.path.join("projects", project_name, "noun_types.json")
    items_path = os.path.join("projects", project_name, "nouns", noun_name, "items.jsonl")

    if not os.path.exists(noun_types_path):
        return jsonify({"error": "noun_types.json not found"}), 404
    if not os.path.exists(items_path):
        return jsonify([])

    with open(noun_types_path) as f:
        noun_types = json.load(f)

    noun_def = noun_types.get(noun_name)
    if not noun_def:
        return jsonify({"error": f"Noun {noun_name} not found"}), 404

    primary_id_field = noun_def.get("primary_id_field")
    if not primary_id_field:
        return jsonify({"error": "No primary_id_field defined"}), 400

    items = []
    with open(items_path) as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)

                # 🔑 Filter by run_id if provided
                if run_id and entry.get("_runID") != run_id:
                    continue

                items.append(entry.get(primary_id_field, "Unknown ID"))

    return jsonify(items)

@upload_bp.route('/create_item', methods=['POST'])
def create_item():
    data = request.json

    project_name = data.get('project_name')
    noun_name = data.get('noun_name')
    item_id = data.get('item_id')
    run_id = data.get('_runID')  # optional

    if not project_name or not noun_name or not item_id:
        return "❌ Missing required fields.", 400

    noun_types_path = os.path.join("projects", project_name, "noun_types.json")
    items_path = os.path.join("projects", project_name, "nouns", noun_name, "items.jsonl")

    if not os.path.exists(noun_types_path):
        return "❌ noun_types.json not found.", 400

    with open(noun_types_path) as f:
        noun_types = json.load(f)

    noun_def = noun_types.get(noun_name)
    if not noun_def:
        return f"❌ Noun {noun_name} not defined.", 400

    primary_id_field = noun_def.get("primary_id_field")
    if not primary_id_field:
        return "❌ No primary_id_field defined.", 400

    # Check for duplicate (ID + _runID combination)
    if os.path.exists(items_path):
        with open(items_path) as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    if entry.get(primary_id_field) == item_id:
                        if run_id:
                            if entry.get("_runID") == run_id:
                                return f"❌ Item with ID '{item_id}' and run '{run_id}' already exists.", 400
                        else:
                            # If no run_id provided, block duplicates without _runID too
                            if "_runID" not in entry:
                                return f"❌ Item with ID '{item_id}' already exists.", 400

    # Build new item dict
    new_item = {primary_id_field: item_id}
    if run_id:
        new_item["_runID"] = run_id

    with open(items_path, "a") as f:
        f.write(json.dumps(new_item) + "\n")

    return jsonify({"message": "✅ Item created.", "item_id": item_id})

@upload_bp.route('/list_run_ids/<project_name>/<noun_name>', methods=['GET'])
def list_run_ids(project_name, noun_name):
    """
    Returns a list of all unique _runID values for items in this noun's items.jsonl
    """
    items_path = os.path.join("projects", project_name, "nouns", noun_name, "items.jsonl")

    if not os.path.exists(items_path):
        return jsonify([])

    run_ids = set()
    with open(items_path) as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                rid = entry.get("_runID")
                if rid:
                    run_ids.add(rid)

    return jsonify(sorted(run_ids))