# tools/launch_gui_webapp.py

import uvicorn
import multiprocessing
import webbrowser
import time
import sys
from pathlib import Path

# Ensure project root is in sys.path for import resolution
project_root = Path(__file__).resolve().parents[1]
sys.path.append(str(project_root))

def run_server():
    uvicorn.run(
        "api.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=["core", "api", "gui", "utils"],
    )

if __name__ == "__main__":
    server_process = multiprocessing.Process(target=run_server)
    server_process.start()

    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:8000/")

    server_process.join()
