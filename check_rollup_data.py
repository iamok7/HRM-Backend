from hrms_api import create_app
from hrms_api.models.attendance_rollup import AttendanceRollup
from hrms_api.extensions import db

app = create_app()

with app.app_context():
    count = AttendanceRollup.query.filter_by(company_id=1, year=2025, month=11).count()
    print(f"Rollups found: {count}")
