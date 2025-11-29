from hrms_api import create_app
from hrms_api.extensions import db
from hrms_api.models.rgs import RgsReport
from sqlalchemy import text

app = create_app()

NEW_QUERY = """
SELECT
    e.id           AS employee_id,
    e.code         AS emp_code,
    e.first_name || ' ' || COALESCE(e.last_name, '') AS employee_name,
    d.name         AS department,
    desig.name     AS designation,
    loc.name       AS location,

    r.year         AS year,
    r.month        AS month,
    r.total_working_days AS days_in_month,
    r.present_days,
    r.absent_days,
    r.leave_days,
    r.weekly_off_days,
    r.holiday_days,
    r.lop_days,
    r.ot_hours

FROM attendance_rollups r
JOIN employees e       ON e.id = r.employee_id
LEFT JOIN departments d ON d.id = e.department_id
LEFT JOIN designations desig ON desig.id = e.designation_id
LEFT JOIN locations loc ON loc.id = e.location_id

WHERE
    r.company_id = :company_id
    AND r.year   = :year
    AND r.month  = :month

ORDER BY
    e.code;
"""

with app.app_context():
    report = RgsReport.query.filter_by(code="ATTENDANCE_MONTHLY").first()
    if report:
        print(f"Updating query for {report.code}...")
        report.query_template = NEW_QUERY
        db.session.commit()
        print("Successfully updated query template.")
    else:
        print("Report ATTENDANCE_MONTHLY not found!")
