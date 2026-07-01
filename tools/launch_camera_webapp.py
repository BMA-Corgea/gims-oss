# tools/launch_camera_webapp.py

import subprocess
import sys
from pathlib import Path

# Ensure project root is on PYTHONPATH
sys.path.append(str(Path(__file__).resolve().parent.parent))

from utils.camera_webapp_host import host_camera_webapp

def main():
    project_name = "LIMS-System"

    print("🚀 Launching Camera Web App...")
    url = host_camera_webapp(project_name=project_name)
    print(f"📸 Camera Web App hosted at: {url}")

    # Auto-open browser, suppress GTK warnings
    subprocess.run(
        ["xdg-open", url],
        stderr=subprocess.DEVNULL
    )

    input("🔧 Press Enter to exit and shut down the webapp...\n")

if __name__ == "__main__":
    main()