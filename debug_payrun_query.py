from hrms_api import create_app
from hrms_api.models.payroll.pay_run import PayRun
from hrms_api.extensions import db

app = create_app()

with app.app_context():
    print("Testing PayRun query...")
    try:
        cid = 13
        print(f"Querying for company_id={cid} (type={type(cid)})")
        runs = PayRun.query.filter(PayRun.company_id == cid).all()
        print(f"Found {len(runs)} runs.")
    except Exception as e:
        print(f"Query failed: {e}")
