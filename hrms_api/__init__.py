import os
import click
from flask import Flask
from flask_cors import CORS

from hrms_api.extensions import db, migrate, init_db
from hrms_api.common.errors import register_error_handlers
from hrms_api.models import load_all

from datetime import timedelta, datetime
from flask_jwt_extended import JWTManager

from flask.cli import with_appcontext

jwt = JWTManager()


def create_app(config_object: str | None = None):
    app = Flask(__name__)

    # Basic inline config (defaults)
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret")
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=2)
    app.config["JWT_REFRESH_TOKEN_EXPIRES"] = timedelta(days=7)
    app.config["JWT_DECODE_LEEWAY"] = 120  # 2 minutes grace for clock skew
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:4445@127.0.0.1:5432/hrms_dev",
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    
    # Absolute path for reports storage to avoid CWD issues
    # app.root_path is usually .../apps/backend/hrms_api
    # We want .../apps/backend/reports_storage
    backend_root = os.path.dirname(app.root_path)
    app.config["REPORTS_STORAGE_ROOT"] = os.path.join(backend_root, "reports_storage")

    # 🔑 Try loading external config, but don't crash if missing
    if config_object:
        try:
            app.config.from_object(config_object)
        except Exception as e:
            # Just log and continue with defaults
            app.logger.warning("Could not import config object %r: %s", config_object, e)

    # CORS (dev)
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Extensions
    db.init_app(app)
    register_error_handlers(app)
    jwt.init_app(app)
    migrate.init_app(app, db)

    # Ensure models are loaded so metadata is complete
    with app.app_context():
        load_all()

    # Try to expose flask db commands
    try:
        from flask_migrate.cli import db as migrate_db_group
        app.cli.add_command(migrate_db_group)
    except Exception:
        try:
            from flask_migrate import cli as migrate_cli
            app.cli.add_command(migrate_cli.db)
        except Exception:
            pass

    # Blueprints
    from hrms_api.blueprints.health import bp as health_bp
    from hrms_api.blueprints.auth import bp as auth_bp
    from hrms_api.blueprints.users import bp as users_bp
    from hrms_api.blueprints.master_companies import bp as companies_bp
    from hrms_api.blueprints.master_locations import bp as locations_bp
    from hrms_api.blueprints.master_departments import bp as departments_bp
    from hrms_api.blueprints.master_designations import bp as designations_bp
    from hrms_api.blueprints.master_grades import bp as grades_bp
    from hrms_api.blueprints.master_cost_centers import bp as cost_centers_bp
    from hrms_api.blueprints.auth_v1 import bp as auth_v1_bp
    from hrms_api.blueprints.employees import bp as employees_bp
    from hrms_api.blueprints.employee_extras import bp as employee_extras_bp
    from hrms_api.blueprints.attendance_masters import bp as attendance_bp
    from hrms_api.blueprints.attendance_assignments import bp as attendance_assignments_bp
    from hrms_api.blueprints.attendance_calendar import bp as attendance_calendar_bp
    from hrms_api.blueprints.attendance_punches import bp as attendance_punches_bp
    from hrms_api.blueprints.attendance_monthly import bp as attendance_monthly_bp
    from hrms_api.blueprints.attendance_self_punch import bp as attendance_self_punch_bp
    from hrms_api.blueprints.attendance_punch_import import bp as attendance_punch_import_bp
    from hrms_api.blueprints.leave import bp as leave_bp
    from hrms_api.blueprints.leave_policies import bp as leave_policies_bp
    from hrms_api.blueprints.attendance_missed_punch import bp as attendance_missed_bp
    from hrms_api.blueprints.security import bp as security_bp
    from .rbac import bp as rbac_bp
    from hrms_api.common.errors import bp_errors
    from hrms_api.blueprints import security_admin

    # Payroll & Compliance
    from hrms_api.blueprints.trades import bp as trades_bp
    from hrms_api.blueprints.pay_cycles import bp as pay_cycles_bp
    from hrms_api.blueprints.pay_policies import bp as pay_policies_bp
    from hrms_api.blueprints.pay_profiles import bp as pay_profiles_bp
    from hrms_api.blueprints.pay_runs import bp as pay_runs_bp
    from hrms_api.blueprints.pay_compliance import bp as pay_compliance_bp
    from hrms_api.blueprints.attendance_rollup import bp as attendance_rollup_bp
    from hrms_api.blueprints.pay_adjustments import bp as pay_adjustments_bp
    from hrms_api.blueprints.attendance_face import bp as attendance_face_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(companies_bp)
    app.register_blueprint(locations_bp)
    app.register_blueprint(departments_bp)
    app.register_blueprint(designations_bp)
    app.register_blueprint(grades_bp)
    app.register_blueprint(cost_centers_bp)
    app.register_blueprint(auth_v1_bp)
    app.register_blueprint(employees_bp)
    app.register_blueprint(employee_extras_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(attendance_assignments_bp)
    app.register_blueprint(attendance_calendar_bp)
    app.register_blueprint(attendance_punches_bp)
    app.register_blueprint(attendance_monthly_bp)
    app.register_blueprint(attendance_self_punch_bp)
    app.register_blueprint(attendance_punch_import_bp)
    app.register_blueprint(attendance_face_bp)
    app.register_blueprint(leave_bp)
    app.register_blueprint(leave_policies_bp)
    app.register_blueprint(attendance_missed_bp)
    app.register_blueprint(security_bp)
    app.register_blueprint(rbac_bp)
    app.register_blueprint(bp_errors)
    app.register_blueprint(trades_bp)
    app.register_blueprint(security_admin.bp)
    app.register_blueprint(pay_cycles_bp)
    app.register_blueprint(pay_policies_bp)
    app.register_blueprint(pay_profiles_bp)
    app.register_blueprint(pay_runs_bp)
    app.register_blueprint(pay_compliance_bp)
    app.register_blueprint(attendance_rollup_bp)
    from hrms_api.blueprints.rgs import bp as rgs_bp
    from hrms_api.blueprints.rgs import bp as rgs_bp
    app.register_blueprint(rgs_bp)
    
    from hrms_api.blueprints.hr_dashboard import bp as hr_dashboard_bp
    app.register_blueprint(hr_dashboard_bp)
    
    # Payslips
    from hrms_api.blueprints.payroll_payslips import payroll_payslips_bp
    from hrms_api.blueprints.self_service import self_service_bp
    app.register_blueprint(payroll_payslips_bp)
    app.register_blueprint(self_service_bp)

    # Warm auth / RBAC settings
    with app.app_context():
        try:
            from hrms_api.rbac import _ensure_auth_settings, _apply_settings_to_app
            s = _ensure_auth_settings()
            _apply_settings_to_app(s)
        except Exception as e:
            app.logger.warning("Could not warm auth settings: %s", e)

    # ----------------- CLI COMMANDS -----------------

    @app.cli.command("seed-core")
    def seed_core():
        """Seed demo company and core users (admin, HR, employee)."""
        from hrms_api.models.user import User
        from hrms_api.models.master import Company
        from hrms_api.models.security import Role, UserRole
        from hrms_api.models.employee import Employee

        def ensure_role(code: str) -> "Role":
            role = Role.query.filter_by(code=code).first()
            if not role:
                role = Role(code=code)
                db.session.add(role)
                db.session.commit()
            return role

        def ensure_user(email: str, full_name: str, role_code: str, password: str = "4445"):
            user = User.query.filter_by(email=email).first()
            created = False
            if not user:
                user = User(email=email, full_name=full_name, status="active")
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                created = True

            role = ensure_role(role_code)
            if not any(ur.role_id == role.id for ur in user.user_roles):
                db.session.add(UserRole(user_id=user.id, role_id=role.id))
                db.session.commit()

            return user, created

        def ensure_employee_profile(email: str, first_name: str, last_name: str = "Demo"):
            emp = Employee.query.filter_by(email=email).first()
            if emp:
                return False

            c, loc, dept, des, grd, cc = _ensure_demo_masters()
            emp = Employee(
                company_id=c.id,
                location_id=loc.id if loc else None,
                department_id=dept.id if dept else None,
                designation_id=des.id if des else None,
                grade_id=grd.id if grd else None,
                cost_center_id=cc.id if cc else None,
                code="EMP-DEMO",
                email=email,
                first_name=first_name,
                last_name=last_name,
                employment_type="fulltime",
                status="active",
                doj=datetime.utcnow().date(),
            )
            db.session.add(emp)
            db.session.commit()
            return True

        c = Company.query.filter_by(code="DEMO").first()
        if not c:
            c = Company(code="DEMO", name="Demo Co")
            db.session.add(c)
            db.session.commit()

        admin_user, admin_created = ensure_user("admin@demo.local", "Demo Admin", "admin")
        hr_user, hr_created = ensure_user("hr@swstk.in", "HR Manager", "hr")
        employee_user, employee_created = ensure_user("emp@swstk.in", "Demo Employee", "employee")
        employee_profile_created = ensure_employee_profile(employee_user.email, "Demo", "Employee")

        click.echo(
            "Seeded/ensured: company DEMO; "
            f"admin@demo.local ({'created' if admin_created else 'existing'}) / 4445; "
            f"hr@swstk.in ({'created' if hr_created else 'existing'}) / 4445; "
            f"emp@swstk.in ({'created' if employee_created else 'existing'}) / 4445"
            + ("; employee profile created" if employee_profile_created else "")
        )

    @app.cli.command("seed-masters")
    def seed_masters():
        """Seed sample locations under DEMO company."""
        from hrms_api.models.master import Company, Location
        c = Company.query.filter_by(code="DEMO").first()
        if not c:
            click.echo("Run flask seed-core first (creates DEMO company).")
            return
        for nm in ("Pune", "Mumbai"):
            if not c.locations.filter_by(name=nm).first():
                db.session.add(Location(company_id=c.id, name=nm))
        db.session.commit()
        click.echo("Seeded locations: Pune, Mumbai")

    @app.cli.command("seed-more-masters")
    def seed_more_masters():
        from hrms_api.models.master import Company, Department, Designation, Grade, CostCenter
        c = Company.query.filter_by(code="DEMO").first()
        if not c:
            click.echo("Run flask seed-core first.")
            return
        # Departments
        d_eng = c.departments.filter_by(name="Engineering").first()
        if not d_eng:
            d_eng = Department(company_id=c.id, name="Engineering")
            db.session.add(d_eng)
        d_hr = c.departments.filter_by(name="HR").first()
        if not d_hr:
            d_hr = Department(company_id=c.id, name="HR")
            db.session.add(d_hr)
        db.session.commit()
        # Designations
        if not d_eng.designations.filter_by(name="Software Engineer").first():
            db.session.add(Designation(department_id=d_eng.id, name="Software Engineer"))
        if not d_hr.designations.filter_by(name="HR Executive").first():
            db.session.add(Designation(department_id=d_hr.id, name="HR Executive"))
        # Grades
        for g in ("G1", "G2", "G3"):
            if not Grade.query.filter_by(name=g).first():
                db.session.add(Grade(name=g))
        # Cost centers
        for code, name in (
            ("CC-001", "Corporate"),
            ("CC-ENG", "Engineering"),
            ("CC-HR", "Human Resources"),
        ):
            if not CostCenter.query.filter_by(code=code).first():
                db.session.add(CostCenter(code=code, name=name))
        db.session.commit()
        click.echo("Seeded: departments, designations, grades, cost centers.")

    @app.cli.command("seed-auth")
    def seed_auth():
        """Create 'admin' role and assign to admin@demo.local user."""
        from hrms_api.models.security import Role, UserRole
        from hrms_api.models.user import User
        admin = Role.query.filter_by(code="admin").first()
        if not admin:
            admin = Role(code="admin")
            db.session.add(admin)
            db.session.commit()
        u = User.query.filter_by(email="admin@demo.local").first()
        if not u:
            click.echo("User admin@demo.local not found. Run flask seed-core first.")
            return
        link = UserRole.query.filter_by(user_id=u.id, role_id=admin.id).first()
        if not link:
            db.session.add(UserRole(user_id=u.id, role_id=admin.id))
            db.session.commit()
        click.echo("Seeded role 'admin' and linked to admin@demo.local")

    # ---------------- Compliance CLI ----------------
    @app.cli.group("compliance")
    def compliance_group():
        """Compliance related utilities."""
        pass

    @compliance_group.command("seed-defaults")
    @click.option("--state", "state_opt", required=True, help="State code e.g. MH")
    @click.option("--company-id", "company_id_opt", type=int, required=True, help="Company ID")
    def compliance_seed_defaults(state_opt: str, company_id_opt: int):
        """Insert default PF/ESI/PT StatConfig records with effective_from = first of current FY."""
        from datetime import date
        from hrms_api.models.master import Company
        from hrms_api.models.payroll.stat_config import StatConfig

        # Resolve FY start (India): 1 Apr current FY
        today = date.today()
        fy_year = today.year if today.month >= 4 else today.year - 1
        eff_from = date(fy_year, 4, 1)

        # Quick company existence check (non-fatal)
        comp = Company.query.get(company_id_opt)
        if not comp:
            click.echo(
                f"Warning: company_id={company_id_opt} not found. Proceeding to insert configs anyway."
            )

        # PF default
        pf_json = {
            "emp_rate": 0.12,
            "er_eps_rate": 0.0833,
            "er_epf_rate": 0.0367,
            "wage_cap": 15000,
            "base_tag": "BASIC_DA",
            "voluntary_max": 0.12,
        }
        db.session.add(
            StatConfig(
                type="PF",
                scope_company_id=company_id_opt,
                scope_state=state_opt.upper(),
                priority=100,
                effective_from=eff_from,
                value_json=pf_json,
                key="STATCFG_V2_PF",
            )
        )

        # ESI default
        esi_json = {
            "emp_rate": 0.0075,
            "er_rate": 0.0325,
            "threshold": 21000,
            "entry_rule": "period_locking",
        }
        db.session.add(
            StatConfig(
                type="ESI",
                scope_company_id=company_id_opt,
                scope_state=state_opt.upper(),
                priority=100,
                effective_from=eff_from,
                value_json=esi_json,
                key="STATCFG_V2_ESI",
            )
        )

        # PT default
        pt_json = {
            "state": state_opt.upper(),
            "slabs": [
                {"min": 0, "max": 7500, "amount": 0},
                {"min": 7501, "max": 10000, "amount": 175},
                {"min": 10001, "max": 9999999, "amount": 200},
            ],
            "double_month": None,
        }
        db.session.add(
            StatConfig(
                type="PT",
                scope_company_id=company_id_opt,
                scope_state=state_opt.upper(),
                priority=100,
                effective_from=eff_from,
                value_json=pt_json,
                key="STATCFG_V2_PT",
            )
        )

        db.session.commit()
        click.echo(
            f"Seeded: PF, ESI, PT for company_id={company_id_opt}, state={state_opt.upper()} from {eff_from.isoformat()}"
        )

    # ------------- Helper functions for demo seeders -------------

    def _ensure_demo_masters():
        from hrms_api.models.master import Company, Location, Department, Designation, Grade, CostCenter
        # Company
        c = Company.query.filter_by(code="DEMO").first()
        if not c:
            c = Company(code="DEMO", name="Demo Co")
            db.session.add(c)
            db.session.commit()
        # Location
        loc = Location.query.filter_by(company_id=c.id, name="Pune").first()
        if not loc:
            loc = Location(company_id=c.id, name="Pune")
            db.session.add(loc)
            db.session.commit()
        # Department
        dept = Department.query.filter_by(company_id=c.id, name="Engineering").first()
        if not dept:
            dept = Department(company_id=c.id, name="Engineering")
            db.session.add(dept)
            db.session.commit()
        # Designation
        des = Designation.query.filter_by(department_id=dept.id, name="Software Engineer").first()
        if not des:
            des = Designation(department_id=dept.id, name="Software Engineer")
            db.session.add(des)
            db.session.commit()
        # Grade
        grd = Grade.query.filter_by(name="G1").first()
        if not grd:
            grd = Grade(name="G1")
            db.session.add(grd)
            db.session.commit()
        # Cost center
        cc = CostCenter.query.filter_by(code="CC-ENG").first()
        if not cc:
            cc = CostCenter(code="CC-ENG", name="Engineering")
            db.session.add(cc)
            db.session.commit()
        return c, loc, dept, des, grd, cc

    def _seed_employees_range_impl(start: int, end: int):
        from sqlalchemy import text
        from hrms_api.models.employee import Employee

        if start > end:
            raise ValueError("start must be <= end")

        c, loc, dept, des, grd, cc = _ensure_demo_masters()

        created = updated = 0
        for i in range(start, end + 1):
            email = f"emp{i}@demo.local"
            code = f"E-{i:04d}"
            emp = Employee.query.get(i)  # pin exact IDs
            if not emp:
                emp = Employee(
                    id=i,
                    company_id=c.id,
                    location_id=loc.id,
                    department_id=dept.id,
                    designation_id=des.id,
                    grade_id=grd.id,
                    cost_center_id=cc.id,
                    code=code,
                    email=email,
                    first_name=f"Emp{i}",
                    last_name="Demo",
                    doj=datetime.utcnow().date(),
                    employment_type="fulltime",
                    status="active",
                )
                db.session.add(emp)
                created += 1
            else:
                changed = False
                if not getattr(emp, "email", None):
                    emp.email = email
                    changed = True
                if not getattr(emp, "code", None):
                    emp.code = code
                    changed = True
                if getattr(emp, "status", None) != "active":
                    emp.status = "active"
                    changed = True
                if changed:
                    updated += 1
        db.session.commit()

        # reset PG sequence if IDs were set manually
        try:
            db.session.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('employees','id'), (SELECT MAX(id) FROM employees))"
                )
            )
            db.session.commit()
        except Exception:
            pass

        return created, updated

    def _seed_users_range_impl(start: int, end: int, password: str = "4445"):
        from hrms_api.models.user import User

        if start > end:
            raise ValueError("start must be <= end")

        created = updated = 0
        for i in range(start, end + 1):
            email = f"emp{i}@demo.local"
            full_name = f"Employee {i}"
            u = User.query.filter_by(email=email).first()
            if not u:
                u = User(email=email, full_name=full_name, status="active")
                u.set_password(password)
                db.session.add(u)
                created += 1
            else:
                changed = False
                if getattr(u, "full_name", "") != full_name:
                    u.full_name = full_name
                    changed = True
                if getattr(u, "status", "active") != "active":
                    u.status = "active"
                    changed = True
                if changed:
                    updated += 1
        db.session.commit()
        return created, updated

    @app.cli.command("seed-employees-range")
    @click.option("--start", default=1, show_default=True, type=int)
    @click.option("--end", default=10, show_default=True, type=int)
    def seed_employees_range(start: int, end: int):
        """Ensure employees with IDs [start..end] exist under DEMO."""
        try:
            created, updated = _seed_employees_range_impl(start, end)
            click.echo(f"Employees ensured in [{start}..{end}] → created={created}, updated={updated}")
        except ValueError as e:
            click.echo(str(e))

    @app.cli.command("seed-users-range")
    @click.option("--start", default=1, show_default=True, type=int)
    @click.option("--end", default=10, show_default=True, type=int)
    @click.option("--password", default="4445", show_default=True, type=str)
    def seed_users_range(start: int, end: int, password: str):
        """Ensure users emp{start}..emp{end} exist with given password."""
        try:
            created, updated = _seed_users_range_impl(start, end, password)
            click.echo(
                f"Users ensured emp{start}..emp{end} (password={password}) → created={created}, updated={updated}"
            )
        except ValueError as e:
            click.echo(str(e))

    @app.cli.command("seed-demo-range")
    @click.option("--start", default=1, show_default=True, type=int)
    @click.option("--end", default=10, show_default=True, type=int)
    @click.option("--password", default="4445", show_default=True, type=str)
    def seed_demo_range(start: int, end: int, password: str):
        """Wrapper: employees + users in one go."""
        try:
            e_created, e_updated = _seed_employees_range_impl(start, end)
            u_created, u_updated = _seed_users_range_impl(start, end, password)
            click.echo(
                f"Done: employees [{start}..end] (created={e_created}, updated={e_updated}) "
                f"and users emp{start}..emp{end} (created={u_created}, updated={u_updated}, password={password})"
            )
        except ValueError as e:
            click.echo(str(e))

    # Convenience aliases
    @app.cli.command("seed-employees-10")
    def seed_employees_10():
        created, updated = _seed_employees_range_impl(1, 10)
        click.echo(f"Employees ensured in [1..10] → created={created}, updated={updated}")

    @app.cli.command("seed-users-10")
    def seed_users_10():
        created, updated = _seed_users_range_impl(1, 10, "4445")
        click.echo("Users ensured emp1..emp10 (password=4445) → "
                   f"created={created}, updated={updated}")

    @app.cli.command("seed-demo-all")
    def seed_demo_all():
        e_created, e_updated = _seed_employees_range_impl(1, 10)
        u_created, u_updated = _seed_users_range_impl(1, 10, "4445")
        click.echo(
            f"All demo seeded: employees [1..10] (created={e_created}, updated={e_updated}); "
            f"users emp1..emp10 (created={u_created}, updated={u_updated}, password=4445)"
        )

    @app.cli.command("seed-leave-types")
    def seed_leave_types():
        from hrms_api.models.master import Company
        from hrms_api.models.leave import LeaveType
        c = Company.query.filter_by(code="DEMO").first()
        if not c:
            click.echo("Run seed-core first")
            return
        defs = [
            ("CL", "Casual Leave", 1.0, True, False),
            ("SL", "Sick Leave", 0.5, True, False),
            ("PL", "Privilege", 1.5, True, True),
        ]
        created = 0
        for code, name, accrual, paid, carry in defs:
            t = LeaveType.query.filter_by(company_id=c.id, code=code).first()
            if not t:
                t = LeaveType(
                    company_id=c.id,
                    code=code,
                    name=name,
                    accrual_per_month=accrual,
                    paid=paid,
                    carry_forward_limit=30 if carry else None,
                )
                db.session.add(t)
                created += 1
        db.session.commit()
        click.echo(f"Leave types ensured: created={created}")

    @app.cli.command("seed-leave-balances-10")
    def seed_leave_balances_10():
        from hrms_api.models.leave import LeaveType, EmployeeLeaveBalance
        from hrms_api.models.employee import Employee

        emp_ids = [e.id for e in Employee.query.filter(Employee.id.between(1, 10)).all()]
        types = LeaveType.query.all()
        if not emp_ids or not types:
            click.echo("Need employees 1..10 and leave types. Run seed-demo-all and seed-leave-types.")
            return
        created = 0
        year = 2025
        for eid in emp_ids:
            for t in types:
                b = EmployeeLeaveBalance.query.filter_by(employee_id=eid, leave_type_id=t.id, year=year).first()
                if not b:
                    # starter packs
                    start_bal = 12.0 if t.code == "CL" else 7.0 if t.code == "SL" else 15.0
                    b = EmployeeLeaveBalance(
                        employee_id=eid,
                        leave_type_id=t.id,
                        year=year,
                        opening_balance=start_bal,
                        accrued=0,
                        used=0,
                        adjusted=0
                    )
                    db.session.add(b)
                    created += 1
        db.session.commit()
        click.echo(
            f"Leave balances ensured for emp1..10 across {len(types)} types for year {year}: created={created}"
        )

    @app.cli.command("seed-rbac")
    @with_appcontext
    def seed_rbac():
        from hrms_api.seed_rbac import run
        click.echo(run())

    @app.cli.command("grant-admin")
    @click.argument("email")
    def grant_admin(email):
        """Attach 'admin' role to a user by email."""
        from hrms_api.models.user import User
        from hrms_api.models.security import Role, UserRole

        u = User.query.filter_by(email=email).first()
        if not u:
            click.echo(f"User {email} not found")
            return
        admin = Role.query.filter_by(code="admin").first()
        if not admin:
            admin = Role(code="admin")
            db.session.add(admin)
            db.session.commit()
        has = any(ur.role_id == admin.id for ur in u.user_roles)
        if not has:
            db.session.add(UserRole(user_id=u.id, role_id=admin.id))
            db.session.commit()
        click.echo(f"Granted 'admin' to {email}")

    @app.cli.command("rbac-grant-all")
    @click.argument("role_code")
    def rbac_grant_all(role_code):
        """Grant ALL existing permissions to a role (e.g., 'hr' or 'admin')."""
        from hrms_api.models.security import Role, Permission, RolePermission

        role = Role.query.filter_by(code=role_code).first()
        if not role:
            click.echo(f"Role {role_code} not found")
            return
        perm_ids = [p.id for p in Permission.query.all()]
        existing = {(rp.role_id, rp.permission_id) for rp in role.permissions}
        new_links = 0
        for pid in perm_ids:
            key = (role.id, pid)
            if key not in existing:
                db.session.add(RolePermission(role_id=role.id, permission_id=pid))
                new_links += 1
        db.session.commit()
        click.echo(f"Granted {new_links} permissions to role '{role_code}'")

    @app.cli.command("seed-rgs")
    def seed_rgs():
        """Seed initial RGS reports."""
        from hrms_api.models.rgs import RgsReport, RgsReportParameter
        from hrms_api.models.user import User
        
        # Ensure admin user exists for 'created_by'
        admin = User.query.filter_by(email="admin@demo.local").first()
        if not admin:
            # Fallback to any user or create one?
            # For now, just try to find ANY user
            admin = User.query.first()
            if not admin:
                click.echo("No users found. Run seed-core first.")
                return

        # 1. Attendance Monthly
        r1 = RgsReport.query.filter_by(code="ATTENDANCE_MONTHLY").first()
        if not r1:
            r1 = RgsReport(
                code="ATTENDANCE_MONTHLY",
                name="Attendance – Monthly Summary",
                description="Per-employee monthly attendance rollup",
                category="attendance",
                output_format="xlsx",
                created_by_user_id=admin.id,
                query_template="""
SELECT
    e.id AS employee_id,
    e.code AS emp_code,
    e.first_name || ' ' || e.last_name AS name,
    d.name AS department,
    r.year,
    r.month,
    r.present_days,
    r.absent_days,
    r.leave_days,
    r.weekly_off_days,
    r.holiday_days
FROM attendance_rollups r
JOIN employees e ON e.id = r.employee_id
LEFT JOIN departments d ON d.id = e.department_id
WHERE
    r.company_id = :company_id
    AND r.year = :year
    AND r.month = :month
ORDER BY e.code;
"""
            )
            db.session.add(r1)
            db.session.flush()
            
            # Params
            p1 = RgsReportParameter(report_id=r1.id, name="company_id", label="Company", type="int", is_required=True, order_index=1)
            p2 = RgsReportParameter(report_id=r1.id, name="month", label="Month", type="int", is_required=True, order_index=2)
            p3 = RgsReportParameter(report_id=r1.id, name="year", label="Year", type="int", is_required=True, order_index=3)
            db.session.add_all([p1, p2, p3])
            click.echo("Seeded ATTENDANCE_MONTHLY")

        # 2. Payroll Register (Placeholder query)
        r2 = RgsReport.query.filter_by(code="PAYROLL_REGISTER").first()
        if not r2:
            r2 = RgsReport(
                code="PAYROLL_REGISTER",
                name="Payroll Register",
                description="Salary register for a pay run",
                category="payroll",
                output_format="xlsx",
                created_by_user_id=admin.id,
                query_template="""
SELECT
    pr.id AS run_id,
    e.code AS emp_code,
    e.first_name || ' ' || e.last_name AS name,
    pri.net_pay,
    pri.gross_pay,
    pri.total_deductions
FROM pay_run_items pri
JOIN pay_runs pr ON pr.id = pri.pay_run_id
JOIN employees e ON e.id = pri.employee_id
WHERE
    pr.company_id = :company_id
    AND pr.id = :pay_run_id
ORDER BY e.code;
"""
            )
            db.session.add(r2)
            db.session.flush()

            p1 = RgsReportParameter(report_id=r2.id, name="company_id", label="Company", type="int", is_required=True, order_index=1)
            p2 = RgsReportParameter(report_id=r2.id, name="pay_run_id", label="Pay Run ID", type="int", is_required=True, order_index=2)
            db.session.add_all([p1, p2])
            click.echo("Seeded PAYROLL_REGISTER")

    @app.cli.command("seed-rgs-compliance")
    def seed_rgs_compliance():
        """Seed Statutory Compliance RGS report (PAYROLL_COMPLIANCE_MONTHLY)."""
        from hrms_api.models.rgs import RgsReport, RgsReportParameter
        from hrms_api.models.user import User
        
        admin = User.query.filter_by(email="admin@demo.local").first()
        if not admin:
            admin = User.query.first()
            if not admin:
                click.echo("No users found. Run seed-core first.")
                return

        code = "PAYROLL_COMPLIANCE_MONTHLY"
        r = RgsReport.query.filter_by(code=code).first()
        if r:
            click.echo(f"Report {code} already exists. Skipping.")
            return

        query = """
WITH run AS (
    SELECT pr.*
    FROM pay_runs pr
    WHERE pr.company_id = :company_id
      AND EXTRACT(YEAR FROM pr.period_start) = :year
      AND EXTRACT(MONTH FROM pr.period_start) = :month
    ORDER BY
      CASE COALESCE(pr.status, 'draft')
        WHEN 'locked' THEN 3
        WHEN 'approved' THEN 2
        WHEN 'calculated' THEN 1
        ELSE 0
      END DESC,
      pr.id DESC
    LIMIT 1
),
base AS (
    SELECT
        r.id                                  AS pay_run_id,
        r.company_id                          AS company_id,
        r.period_start,
        r.period_end,

        e.id                                  AS employee_id,
        e.code                                AS emp_code,
        (e.first_name || ' ' || COALESCE(e.last_name, '')) AS employee_name,
        d.name                                AS department,
        desig.name                            AS designation,
        loc.name                              AS location,
        g.name                                AS grade,
        cc.code                               AS cost_center,
        e.employment_type,
        e.status,

        -- statutory ids (Mocked as NULL since columns missing in DB)
        NULL                                  AS uan,
        NULL                                  AS pf_number,
        NULL                                  AS esi_number,
        NULL                                  AS pan,

        -- gross/net from item
        COALESCE(i.gross, 0)                  AS gross_pay,
        COALESCE(i.net, COALESCE(i.gross, 0)) AS net_pay,

        l.amount                              AS line_amount,
        sc.code                               AS comp_code,
        sc.type                               AS comp_type
    FROM run r
    JOIN pay_run_items i       ON i.pay_run_id = r.id
    JOIN employees e           ON e.id = i.employee_id
    LEFT JOIN departments d    ON d.id = e.department_id
    LEFT JOIN designations desig ON desig.id = e.designation_id
    LEFT JOIN locations loc    ON loc.id = e.location_id
    LEFT JOIN grades g         ON g.id = e.grade_id
    LEFT JOIN cost_centers cc  ON cc.id = e.cost_center_id
    LEFT JOIN pay_run_item_lines l
           ON l.item_id = i.id
    LEFT JOIN salary_components sc
           ON sc.id = l.component_id
)
SELECT
    b.company_id,
    c.name                          AS company_name,
    EXTRACT(YEAR FROM b.period_start)::int  AS year,
    EXTRACT(MONTH FROM b.period_start)::int AS month,
    b.pay_run_id,
    b.period_start,
    b.period_end,

    b.employee_id,
    b.emp_code,
    b.employee_name,
    b.department,
    b.designation,
    b.location,
    b.grade,
    b.cost_center,
    b.employment_type,
    b.status,

    b.uan,
    b.pf_number,
    b.esi_number,
    b.pan,

    -- PF
    SUM(
      CASE WHEN b.comp_code IN ('BASIC','HRA','SPL_ALLOW','SPECIAL')
           THEN b.line_amount ELSE 0 END
    )                                  AS pf_wages,
    SUM(CASE WHEN b.comp_code IN ('PF_EMP') THEN b.line_amount ELSE 0 END)
                                       AS pf_employee,
    SUM(CASE WHEN b.comp_code IN ('PF_ER','PF_ER_EPF','PF_ER_EPS')
             THEN b.line_amount ELSE 0 END)
                                       AS pf_employer,
    SUM(CASE WHEN b.comp_code IN ('PF_EMP','PF_ER','PF_ER_EPF','PF_ER_EPS')
             THEN b.line_amount ELSE 0 END)
                                       AS pf_total,

    -- ESI
    SUM(
      CASE WHEN b.comp_code IN ('BASIC','HRA','SPL_ALLOW','SPECIAL')
           THEN b.line_amount ELSE 0 END
    )                                  AS esi_wages,
    SUM(CASE WHEN b.comp_code = 'ESI_EMP' THEN b.line_amount ELSE 0 END)
                                       AS esi_employee,
    SUM(CASE WHEN b.comp_code = 'ESI_ER' THEN b.line_amount ELSE 0 END)
                                       AS esi_employer,
    SUM(CASE WHEN b.comp_code IN ('ESI_EMP','ESI_ER')
             THEN b.line_amount ELSE 0 END)
                                       AS esi_total,

    -- PT
    SUM(
      CASE WHEN b.comp_code IN ('BASIC','HRA','SPL_ALLOW','SPECIAL')
           THEN b.line_amount ELSE 0 END
    )                                  AS pt_wages,
    SUM(CASE WHEN b.comp_code IN ('PT','PT_MH')
             THEN b.line_amount ELSE 0 END)
                                       AS professional_tax,

    -- LWF
    SUM(
      CASE WHEN b.comp_code IN ('BASIC','HRA','SPL_ALLOW','SPECIAL')
           THEN b.line_amount ELSE 0 END
    )                                  AS lwf_wages,
    SUM(CASE WHEN b.comp_code IN ('LWF','LWF_EMP')
             THEN b.line_amount ELSE 0 END)
                                       AS lwf_employee,
    SUM(CASE WHEN b.comp_code = 'LWF_ER'
             THEN b.line_amount ELSE 0 END)
                                       AS lwf_employer,
    SUM(CASE WHEN b.comp_code IN ('LWF','LWF_EMP','LWF_ER')
             THEN b.line_amount ELSE 0 END)
                                       AS lwf_total,

    -- Totals for cross-check
    SUM(CASE WHEN b.comp_type = 'earning'
             THEN b.line_amount ELSE 0 END)
                                       AS gross_earnings,
    SUM(CASE WHEN b.comp_type = 'deduction'
             THEN b.line_amount ELSE 0 END)
                                       AS total_deductions,
    b.net_pay

FROM base b
LEFT JOIN companies c ON c.id = b.company_id
GROUP BY
    b.company_id, c.name,
    b.pay_run_id, b.period_start, b.period_end,
    b.employee_id, b.emp_code, b.employee_name,
    b.department, b.designation, b.location, b.grade, b.cost_center,
    b.employment_type, b.status,
    b.uan, b.pf_number, b.esi_number, b.pan,
    b.net_pay
ORDER BY b.emp_code;
"""
        r = RgsReport(
            code=code,
            name="Payroll – Statutory Compliance (Monthly)",
            description="Monthly PF/ESI/PT/LWF register",
            category="payroll",
            output_format="xlsx",
            created_by_user_id=admin.id,
            query_template=query
        )
        db.session.add(r)
        db.session.flush()

        p1 = RgsReportParameter(report_id=r.id, name="company_id", label="Company", type="int", is_required=True, order_index=1)
        p2 = RgsReportParameter(report_id=r.id, name="month", label="Month", type="int", is_required=True, order_index=2)
        p3 = RgsReportParameter(report_id=r.id, name="year", label="Year", type="int", is_required=True, order_index=3)
        db.session.add_all([p1, p2, p3])
        
        db.session.commit()
        click.echo(f"Seeded {code}")

        db.session.commit()

    return app
