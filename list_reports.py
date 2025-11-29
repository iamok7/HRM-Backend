from hrms_api import create_app
from hrms_api.models.rgs import RgsReport

app = create_app()

with app.app_context():
    reports = RgsReport.query.all()
    for r in reports:
        print(f"ID: {r.id}, Code: {r.code}, Name: {r.name}")
        print(f"Query Snippet: {r.query_template[:100]}...")
        print("-" * 20)
