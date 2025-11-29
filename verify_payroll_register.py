import os
from hrms_api import create_app
from hrms_api.extensions import db
from hrms_api.models.rgs import RgsReport
from hrms_api.services.rgs_service import RgsService

app = create_app()

with app.app_context():
    report = RgsReport.query.filter_by(code="PAYROLL_REGISTER").first()
    if not report:
        print("Report not found!")
        exit(1)
        
    print(f"Verifying report: {report.name}")
    
    # Use Company 13 (Omkar) and Nov 2025
    params = {"company_id": 13, "year": 2025, "month": 11}
    
    service = RgsService(storage_root="./test_reports")
    
    try:
        validated = service.validate_params(report, params)
        print("Params validated.")
        
        # Note: This might return empty rows if no PayRun exists for this period.
        # But we want to ensure the SQL is valid and doesn't crash.
        rows = service.execute_report(report, validated)
        print(f"Report executed successfully. Rows: {len(rows)}")
        
        if rows:
            print("First Row Keys:", rows[0].keys())
            print("First Row Data:", rows[0])
        else:
            print("No data returned (Expected if no PayRun exists).")
            
    except Exception as e:
        print(f"Report Execution Failed: {e}")
        if hasattr(e, 'payload'):
            print(f"Error Payload: {e.payload}")
        import traceback
        traceback.print_exc()
