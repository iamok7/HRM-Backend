import os
from hrms_api import create_app
from hrms_api.extensions import db
from hrms_api.models.attendance_rollup import AttendanceRollup
from hrms_api.models.rgs import RgsReport
from hrms_api.services.rgs_service import RgsService

app = create_app()

with app.app_context():
    # 1. Check Rollup Count
    count = AttendanceRollup.query.filter_by(year=2025, month=11).count()
    print(f"Rollups for Nov 2025: {count}")
    
    if count < 50:
        print("WARNING: Low rollup count!")
    
    # 2. Check Data Quality (Sample)
    rollup = AttendanceRollup.query.filter_by(year=2025, month=11).first()
    if rollup:
        print("Sample Rollup Data:")
        print(f"  Employee ID: {rollup.employee_id}")
        print(f"  Present: {rollup.present_days}")
        print(f"  Absent: {rollup.absent_days}")
        print(f"  Holidays: {rollup.holiday_days}")
        print(f"  Weekly Offs: {rollup.weekly_off_days}")
        print(f"  Total Working: {rollup.total_working_days}")
        
        if rollup.present_days == 0 and rollup.absent_days == 0:
             print("WARNING: Sample rollup has 0 present/absent days!")

    # 3. Generate Report
    report = RgsReport.query.filter_by(code="ATTENDANCE_MONTHLY").first()
    if report:
        service = RgsService(storage_root="./test_reports")
        params = {"company_id": 1, "year": 2025, "month": 11}
        
        try:
            validated = service.validate_params(report, params)
            rows = service.execute_report(report, validated)
            print(f"Report Rows: {len(rows)}")
            
            if rows:
                print("First Row:", rows[0])
        except Exception as e:
            print(f"Report Generation Failed: {e}")
