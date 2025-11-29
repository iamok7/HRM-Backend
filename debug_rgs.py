import os
from hrms_api import create_app
from hrms_api.models.rgs import RgsOutput

app = create_app()

print(f"CWD: {os.getcwd()}")
print(f"App Config STORAGE: {app.config.get('REPORTS_STORAGE_ROOT')}")

with app.app_context():
    out = RgsOutput.query.get(1)
    if out:
        print(f"Output 1: {out.file_name}, URL: {out.storage_url}")
        
        # Check default location
        default_root = os.path.join(os.getcwd(), "reports_storage")
        path = os.path.join(default_root, out.storage_url)
        print(f"Checking path: {path}")
        print(f"Exists? {os.path.exists(path)}")
        
        # Check parent dir
        parent_root = os.path.join(os.path.dirname(os.getcwd()), "reports_storage")
        path2 = os.path.join(parent_root, out.storage_url)
        print(f"Checking parent path: {path2}")
        print(f"Exists? {os.path.exists(path2)}")
    else:
        print("Output 1 not found in DB")
