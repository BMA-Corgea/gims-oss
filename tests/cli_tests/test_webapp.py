import pytest
from io import BytesIO
from PIL import Image
import json
import os

from utils.image_upload import upload_bp
from flask import Flask

@pytest.fixture
def client(tmp_path):
    """
    Sets up a test Flask app with the upload blueprint.
    """
    app = Flask(__name__)
    app.register_blueprint(upload_bp)

    # Change working directory to tmp_path for isolated filesystem
    os.chdir(tmp_path)

    with app.test_client() as client:
        yield client

@pytest.fixture
def dummy_image():
    """
    Returns a BytesIO object containing a small JPEG image.
    """
    image = Image.new("RGB", (640, 480), color="blue")
    img_bytes = BytesIO()
    image.save(img_bytes, format="JPEG")
    img_bytes.seek(0)
    return img_bytes

class TestHappyPaths:

    def setup_noun(self, tmp_path, project_name, noun_name, primary_id_field, item_id):
        os.makedirs(tmp_path / "projects" / project_name / "nouns" / noun_name, exist_ok=True)

        # Write noun_types.json
        noun_types = {
            noun_name: {
                "primary_id_field": primary_id_field,
                "fields": {
                    "picture_field": {"adjective_class": "Picture"}
                }
            }
        }
        with open(tmp_path / "projects" / project_name / "noun_types.json", "w") as f:
            json.dump(noun_types, f)

        # Write items.jsonl with item_id
        with open(tmp_path / "projects" / project_name / "nouns" / noun_name / "items.jsonl", "w") as f:
            json.dump({primary_id_field: item_id}, f)
            f.write("\n")

    def test_upload_valid_jpeg(self, client, tmp_path, dummy_image):
        """
        Uploads a valid JPEG and expects success.
        """
        project_name = "proj"
        noun_name = "TestNoun"
        item_id = "item001"
        self.setup_noun(tmp_path, project_name, noun_name, "sample_id", item_id)

        data = {
            "project_name": project_name,
            "noun_name": noun_name,
            "item_id": item_id,
            "photo": (dummy_image, "test.jpg")
        }

        response = client.post("/upload_image", data=data, content_type="multipart/form-data")
        assert response.status_code == 200
        assert "✅" in response.data.decode()

    # Similar tests for PNG and WEBP
    def test_upload_valid_png(self, client, tmp_path):
        project_name = "proj"
        noun_name = "TestNoun"
        item_id = "item001"
        self.setup_noun(tmp_path, project_name, noun_name, "sample_id", item_id)

        # Create PNG
        image = Image.new("RGB", (640, 480), color="green")
        img_bytes = BytesIO()
        image.save(img_bytes, format="PNG")
        img_bytes.seek(0)

        data = {
            "project_name": project_name,
            "noun_name": noun_name,
            "item_id": item_id,
            "photo": (img_bytes, "test.png")
        }

        response = client.post("/upload_image", data=data, content_type="multipart/form-data")
        assert response.status_code == 200
        assert "✅" in response.data.decode()

class TestNegativeCases:

    def test_missing_project_name(self, client):
        data = {}
        response = client.post("/upload_image", data=data)
        assert response.status_code == 400
        assert "project_name is required" in response.data.decode()

    def test_missing_noun_name(self, client):
        data = {"project_name": "proj"}
        response = client.post("/upload_image", data=data)
        assert response.status_code == 400
        assert "noun_name is required" in response.data.decode()

    def test_missing_item_id(self, client):
        data = {"project_name": "proj", "noun_name": "TestNoun"}
        response = client.post("/upload_image", data=data)
        assert response.status_code == 400
        assert "item_id is required" in response.data.decode()

    def test_no_file_uploaded(self, client):
        data = {"project_name": "proj", "noun_name": "TestNoun", "item_id": "item"}
        response = client.post("/upload_image", data=data)
        assert response.status_code == 400
        assert "No file part" in response.data.decode()

    def test_unsupported_file_type(self, client):
        data = {
            "project_name": "proj",
            "noun_name": "TestNoun",
            "item_id": "item",
            "photo": (BytesIO(b"fake"), "test.txt")
        }
        response = client.post("/upload_image", data=data, content_type="multipart/form-data")
        assert response.status_code == 400
        assert "Invalid file type" in response.data.decode()

    def test_corrupt_image_file(self, client):
        data = {
            "project_name": "proj",
            "noun_name": "TestNoun",
            "item_id": "item",
            "photo": (BytesIO(b"notanimage"), "test.jpg")
        }
        response = client.post("/upload_image", data=data, content_type="multipart/form-data")
        assert response.status_code == 400
        assert "Failed to open image" in response.data.decode()

class TestOptional:

    def test_path_traversal_filename(self, client, tmp_path, dummy_image):
        """
        Ensures filename path traversal attempts do not escape intended directory.
        """
        project_name = "proj"
        noun_name = "TestNoun"
        item_id = "item001"

        os.makedirs(tmp_path / "projects" / project_name / "nouns" / noun_name, exist_ok=True)
        noun_types = {
            noun_name: {
                "primary_id_field": "sample_id",
                "fields": {
                    "picture_field": {"adjective_class": "Picture"}
                }
            }
        }
        with open(tmp_path / "projects" / project_name / "noun_types.json", "w") as f:
            json.dump(noun_types, f)
        with open(tmp_path / "projects" / project_name / "nouns" / noun_name / "items.jsonl", "w") as f:
            json.dump({"sample_id": item_id}, f)
            f.write("\n")

        data = {
            "project_name": project_name,
            "noun_name": noun_name,
            "item_id": item_id,
            "photo": (dummy_image, "../../evil.jpg")
        }

        response = client.post("/upload_image", data=data, content_type="multipart/form-data")
        assert response.status_code == 200
        assert "✅" in response.data.decode()
