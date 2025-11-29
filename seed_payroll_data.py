from datetime import date
from hrms_api import create_app
from hrms_api.extensions import db
from hrms_api.models.payroll.pay_run import PayRun, PayRunItem, PayRunItemLine
from hrms_api.models.payroll.components import SalaryComponent
from hrms_api.models.employee import Employee
from hrms_api.models.master import Company

app = create_app()

def get_or_create_component(code, name, type_):
    comp = SalaryComponent.query.filter_by(code=code).first()
    if not comp:
        comp = SalaryComponent(code=code, name=name, type=type_)
        db.session.add(comp)
        db.session.flush()
    return comp

with app.app_context():
    company_id = 13 # Omkar
    year = 2025
    month = 11
    
    # Ensure components exist
    basic = get_or_create_component("BASIC", "Basic Salary", "earning")
    hra = get_or_create_component("HRA", "House Rent Allowance", "earning")
    pf_emp = get_or_create_component("PF_EMP", "PF Employee", "deduction")
    pt = get_or_create_component("PT", "Professional Tax", "deduction")
    
    # Create PayRun
    start_date = date(year, month, 1)
    end_date = date(year, month, 30) # Simplified
    
    run = PayRun.query.filter_by(company_id=company_id, period_start=start_date).first()
    if not run:
        print("Creating PayRun...")
        run = PayRun(
            company_id=company_id,
            period_start=start_date,
            period_end=end_date,
            status="locked", # Finalized
            created_by=1
        )
        db.session.add(run)
        db.session.flush()
    else:
        print("PayRun already exists.")
        
    # Create Items for Employees
    employees = Employee.query.filter_by(company_id=company_id).limit(10).all()
    print(f"Seeding payroll for {len(employees)} employees...")
    
    for emp in employees:
        item = PayRunItem.query.filter_by(pay_run_id=run.id, employee_id=emp.id).first()
        if not item:
            item = PayRunItem(
                pay_run_id=run.id,
                employee_id=emp.id,
                gross=50000,
                net=45000,
                earnings=50000,
                deductions=5000,
                calc_meta={"days_worked": 20, "lop_days": 0, "ot_hours": 2}
            )
            db.session.add(item)
            db.session.flush()
            
            # Lines
            lines = [
                (basic, 30000, False),
                (hra, 20000, False),
                (pf_emp, 1800, True),
                (pt, 200, True)
            ]
            
            for comp, amt, is_stat in lines:
                line = PayRunItemLine(
                    item_id=item.id,
                    component_id=comp.id,
                    amount=amt,
                    is_statutory=is_stat
                )
                db.session.add(line)
                
    db.session.commit()
    print("Payroll data seeded.")
