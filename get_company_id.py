from hrms_api import create_app
from hrms_api.models.master import Company

app = create_app()

with app.app_context():
    comp = Company.query.filter_by(code="Omkar").first()
    if comp:
        print(f"Company ID for Omkar: {comp.id}")
    else:
        print("Company Omkar not found!")
