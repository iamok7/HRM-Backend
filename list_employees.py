from hrms_api import create_app
from hrms_api.models.employee import Employee

app = create_app()

with app.app_context():
    employees = Employee.query.all()
    print(f"Found {len(employees)} employees:")
    for emp in employees:
        print(f"ID: {emp.id}, Name: {emp.first_name} {emp.last_name}, Email: {emp.email}, Code: {emp.code}")
