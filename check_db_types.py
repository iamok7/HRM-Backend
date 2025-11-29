from hrms_api import create_app
from hrms_api.extensions import db
from sqlalchemy import text

app = create_app()

with app.app_context():
    print("Checking column types...")
    
    # Check users.id
    res = db.session.execute(text("SELECT data_type FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'id'")).scalar()
    print(f"users.id type: {res}")
    
    # Check pay_runs.company_id
    res = db.session.execute(text("SELECT data_type FROM information_schema.columns WHERE table_name = 'pay_runs' AND column_name = 'company_id'")).scalar()
    print(f"pay_runs.company_id type: {res}")
