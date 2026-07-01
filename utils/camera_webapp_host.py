def host_camera_webapp(port: int = 5001, project_name: str | None = None, noun_name: str | None = None, run_id: str | None = None) -> str:
    from flask import Flask
    from utils.image_upload import upload_bp
    from pathlib import Path
    import threading
    import urllib.parse

    project_root = Path(__file__).resolve().parent.parent
    static_dir = project_root / 'static'

    app = Flask(__name__, static_folder=str(static_dir), static_url_path="")
    app.register_blueprint(upload_bp)

    def _run():
        app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    # Build URL with parameters
    params = {}
    if project_name:
        params['project'] = project_name
    if noun_name:
        params['noun'] = noun_name
    if run_id:
        params['run'] = run_id

    query_string = urllib.parse.urlencode(params)
    url = f"http://localhost:{port}/camera_webapp.html"
    if query_string:
        url += f"?{query_string}"

    return url