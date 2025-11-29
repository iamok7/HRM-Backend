import argparse
import random
from datetime import date, timedelta, datetime, time
from hrms_api import create_app
from hrms_api.extensions import db
from hrms_api.models.master import Company, Location, Department, Designation, Grade
from hrms_api.models.employee import Employee
from hrms_api.models.attendance_punch import AttendancePunch
from hrms_api.models.attendance import Holiday, WeeklyOffRule, Shift
from hrms_api.blueprints.attendance_rollup import generate_rollups_for_period

# Indian names for realism
FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan", "Krishna", "Ishaan", "Diya", "Saanvi", "Ananya", "Aadhya", "Pari", "Kiara", "Riya", "Anvi", "Pihu", "Myra"]
LAST_NAMES = ["Sharma", "Verma", "Gupta", "Malhotra", "Bhatia", "Saxena", "Mehta", "Chopra", "Singh", "Kumar", "Patel", "Reddy", "Nair", "Iyer", "Rao"]

def get_or_create_company(code="DEMO"):
    comp = Company.query.filter_by(code=code).first()
    if not comp:
        print(f"Creating company {code}...")
        comp = Company(code=code, name=f"{code} Corp", is_active=True)
        db.session.add(comp)
        db.session.commit()
    return comp

def seed_masters(company):
    # Locations
    locs = ["Mumbai", "Bangalore", "Delhi"]
    locations = []
    for name in locs:
        l = Location.query.filter_by(company_id=company.id, name=name).first()
        if not l:
            l = Location(company_id=company.id, name=name)
            db.session.add(l)
        locations.append(l)
    
    # Departments
    depts = ["Engineering", "HR", "Sales", "Marketing"]
    departments = []
    for name in depts:
        d = Department.query.filter_by(company_id=company.id, name=name).first()
        if not d:
            d = Department(company_id=company.id, name=name)
            db.session.add(d)
        departments.append(d)
        
    # Designations
    desigs = ["Associate", "Senior", "Lead", "Manager"]
    designations = []
    # Just link all to first dept for simplicity or random
    for name in desigs:
        # Check if exists in any dept for this company (simplified check)
        # Actually Designation is per department.
        # Let's just create them for the first department if missing
        d = Designation.query.filter_by(department_id=departments[0].id, name=name).first()
        if not d:
            d = Designation(department_id=departments[0].id, name=name)
            db.session.add(d)
        designations.append(d)

    # Holidays (Oct-Nov 2025)
    holidays = [
        (date(2025, 10, 2), "Gandhi Jayanti"),
        (date(2025, 10, 20), "Diwali"),
        (date(2025, 11, 14), "Children's Day"), # Just an example
    ]
    for d_date, name in holidays:
        h = Holiday.query.filter_by(company_id=company.id, date=d_date).first()
        if not h:
            h = Holiday(company_id=company.id, date=d_date, name=name)
            db.session.add(h)
            
    # Weekly Off (Sat/Sun)
    # 5=Sat, 6=Sun
    for w in [5, 6]:
        wo = WeeklyOffRule.query.filter_by(company_id=company.id, weekday=w).first()
        if not wo:
            wo = WeeklyOffRule(company_id=company.id, weekday=w)
            db.session.add(wo)

    db.session.commit()
    return locations, departments, designations

def seed_employees(company, count, locations, departments, designations):
    existing = Employee.query.filter_by(company_id=company.id).count()
    to_create = max(0, count - existing)
    print(f"Existing employees: {existing}. Creating {to_create} more...")
    
    created = []
    for i in range(to_create):
        fn = random.choice(FIRST_NAMES)
        ln = random.choice(LAST_NAMES)
        code = f"DEMO-{existing + i + 1:04d}"
        
        emp = Employee(
            company_id=company.id,
            code=code,
            first_name=fn,
            last_name=ln,
            email=f"{fn.lower()}.{ln.lower()}.{code}@example.com",
            location_id=random.choice(locations).id,
            department_id=random.choice(departments).id,
            designation_id=random.choice(designations).id if designations else None,
            doj=date(2024, 1, 1),
            status="active"
        )
        db.session.add(emp)
        created.append(emp)
        if i % 10 == 0:
            db.session.commit()
    db.session.commit()
    return Employee.query.filter_by(company_id=company.id).all()

def generate_punches(employees, start_date, end_date):
    print(f"Generating punches from {start_date} to {end_date}...")
    
    delta = end_date - start_date
    days = [start_date + timedelta(days=i) for i in range(delta.days + 1)]
    
    # Pre-fetch holidays and weekly offs to skip punches
    # Simplified: assume global holidays/weekly offs for the company
    holidays = {h.date for h in Holiday.query.filter_by(company_id=employees[0].company_id).all()}
    weekly_offs = {wo.weekday for wo in WeeklyOffRule.query.filter_by(company_id=employees[0].company_id).all()}
    
    punch_count = 0
    for d in days:
        if d in holidays:
            continue
        if d.weekday() in weekly_offs:
            continue
            
        for emp in employees:
            # 85% Present, 5% Absent, 5% Leave (skip), 5% Random skip
            r = random.random()
            if r < 0.85:
                # Present
                # IN: 9:00 +/- 30 mins
                in_min = random.randint(-30, 30)
                in_time = (datetime.combine(d, time(9, 0)) + timedelta(minutes=in_min)).time()
                
                # OUT: 18:00 +/- 30 mins
                out_min = random.randint(-30, 30)
                out_time = (datetime.combine(d, time(18, 0)) + timedelta(minutes=out_min)).time()
                
                # Create punches
                # Check if exists first? For speed, maybe just try/except or assume clean slate if new db
                # But upsert_manual_punch is safer
                # We'll construct objects directly for speed if we know they don't exist, but let's be safe
                
                # Check if punch exists to avoid UniqueConstraint error
                # uq_punch_employee_ts_dir
                
                # IN
                ts_in = datetime.combine(d, in_time)
                exists_in = AttendancePunch.query.filter_by(
                    employee_id=emp.id,
                    ts=ts_in,
                    direction="in"
                ).first()
                
                if not exists_in:
                    p1 = AttendancePunch(
                        company_id=emp.company_id,
                        employee_id=emp.id,
                        ts=ts_in,
                        direction="in",
                        method="machine"
                    )
                    db.session.add(p1)
                    punch_count += 1

                # OUT
                ts_out = datetime.combine(d, out_time)
                exists_out = AttendancePunch.query.filter_by(
                    employee_id=emp.id,
                    ts=ts_out,
                    direction="out"
                ).first()
                
                if not exists_out:
                    p2 = AttendancePunch(
                        company_id=emp.company_id,
                        employee_id=emp.id,
                        ts=ts_out,
                        direction="out",
                        method="machine"
                    )
                    db.session.add(p2)
                    punch_count += 1
                
        if punch_count > 1000:
            db.session.commit()
            punch_count = 0
            
    db.session.commit()
    print("Punches generated.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company-code", default="DEMO")
    parser.add_argument("--start-date", default="2025-10-01")
    parser.add_argument("--end-date", default="2025-11-30")
    parser.add_argument("--employees", type=int, default=100)
    args = parser.parse_args()
    
    app = create_app()
    with app.app_context():
        comp = get_or_create_company(args.company_code)
        locs, depts, desigs = seed_masters(comp)
        emps = seed_employees(comp, args.employees, locs, depts, desigs)
        
        s_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        e_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()
        
        generate_punches(emps, s_date, e_date)
        
        # Run Engine
        # For each month in range
        curr = s_date
        months = set()
        while curr <= e_date:
            months.add((curr.year, curr.month))
            # next month
            if curr.month == 12:
                curr = date(curr.year + 1, 1, 1)
            else:
                curr = date(curr.year, curr.month + 1, 1)
        
        for y, m in months:
            print(f"Generating rollups for {y}-{m}...")
            count = generate_rollups_for_period(comp.id, y, m)
            print(f"Generated {count} rollups.")

if __name__ == "__main__":
    main()
