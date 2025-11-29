import os
import csv
import io
from hrms_api import create_app
from hrms_api.extensions import db
from hrms_api.models.rgs import RgsReport
from hrms_api.services.rgs_service import RgsService

app = create_app()

with app.app_context():
    report = RgsReport.query.filter_by(code="ATTENDANCE_MONTHLY").first()
    if not report:
        print("Report ATTENDANCE_MONTHLY not found!")
        exit(1)

    # Force CSV for easy checking
    report.output_format = "csv"
    
    service = RgsService(storage_root="./test_reports")
    
    # Params
    params = {
        "company_id": 1,
        "year": 2025,
        "month": 11
    }
    
    print("Executing report...")
    try:
        # We can use execute_report directly to check rows
        validated = service.validate_params(report, params)
        rows = service.execute_report(report, validated)
        
        if not rows:
            print("No rows returned!")
        else:
            print(f"Returned {len(rows)} rows.")
            print("Columns found:", list(rows[0].keys()))
            print("First row data:", rows[0])
            
            # Check for specific columns
            required_cols = ["present_days", "absent_days", "leave_days", "weekly_off_days"]
            missing = [c for c in required_cols if c not in rows[0]]
            if missing:
                print(f"MISSING COLUMNS: {missing}")
            else:
                print("All required columns present.")

    except Exception as e:
        print(f"Error: {e}")
