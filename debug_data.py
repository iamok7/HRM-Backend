from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.user import User
from hrms_api import create_app

app = create_app()

with app.app_context():
    emp = Employee.query.get(1)
    if emp:
        print(f"Employee 1: ID={emp.id}, Email={emp.email}, Name={emp.first_name} {emp.last_name}")
    else:
        print("Employee 1 not found")

    user = User.query.filter_by(email="admin@demo.local").first()
    if user:
        print(f"User admin: ID={user.id}, Email={user.email}, Name={user.full_name}")
    else:
        print("User admin not found")
