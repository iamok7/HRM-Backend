from hrms_api import create_app
from hrms_api.models.rgs import RgsOutput

app = create_app()

with app.app_context():
    # Get latest output
    out = RgsOutput.query.order_by(RgsOutput.id.desc()).first()
    if out:
        print(f"ID: {out.id}")
        print(f"File Name: '{out.file_name}'")
        print(f"Storage URL: '{out.storage_url}'")
        print(f"Mime Type: '{out.mime_type}'")
    else:
        print("No outputs found.")
