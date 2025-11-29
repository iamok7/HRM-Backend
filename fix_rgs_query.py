from hrms_api import create_app
from hrms_api.extensions import db
from hrms_api.models.rgs import RgsReport

app = create_app()

with app.app_context():
    r = RgsReport.query.filter_by(code="ATTENDANCE_MONTHLY").first()
    if r:
        print(f"Updating query for {r.code}...")
        r.query_template = """
SELECT
    e.id AS employee_id,
    e.code AS emp_code,
    e.first_name || ' ' || e.last_name AS name,
    d.name AS department
FROM employees e
LEFT JOIN departments d ON d.id = e.department_id
WHERE
    e.company_id = :company_id
ORDER BY e.code;
"""
        db.session.commit()
        print("Updated.")
    else:
        print("Report not found.")
