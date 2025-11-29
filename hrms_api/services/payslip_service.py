from datetime import datetime
from flask import render_template_string
from hrms_api.models.payroll.pay_run import PayRun, PayRunItem, PayRunItemLine
from hrms_api.models.employee import Employee
from hrms_api.models.master import Company, Department, Designation, Location, Grade, CostCenter
from hrms_api.models.employee_bank import EmployeeBankAccount
from hrms_api.models.payroll.components import SalaryComponent

class PayslipService:
    def build_payslip_dto(self, item: PayRunItem) -> dict:
        """
        Constructs the Payslip DTO for a given PayRunItem.
        """
        run = item.pay_run
        emp = item.employee
        company = run.company
        
        # Fetch related objects (optimization: could be eager loaded)
        dept = Department.query.get(emp.department_id) if emp.department_id else None
        desig = Designation.query.get(emp.designation_id) if emp.designation_id else None
        loc = Location.query.get(emp.location_id) if emp.location_id else None
        grade = Grade.query.get(emp.grade_id) if emp.grade_id else None
        cc = CostCenter.query.get(emp.cost_center_id) if emp.cost_center_id else None
        bank = EmployeeBankAccount.query.filter_by(employee_id=emp.id, is_primary=True).first()
        
        # Parse calc_meta
        meta = item.calc_meta or {}
        
        # Process Lines
        earnings = []
        deductions = []
        
        earnings_sum = {
            "basic": 0, "hra": 0, "special_allowance": 0, 
            "other_earnings": 0, "gross_earnings": 0
        }
        deductions_sum = {
            "pf_employee": 0, "esi_employee": 0, "professional_tax": 0,
            "lwf_employee": 0, "other_deductions": 0, "total_deductions": 0
        }
        employer_contrib = {
            "pf_employer": 0, "esi_employer": 0, "lwf_employer": 0, "total_employer_contrib": 0
        }
        
        for line in item.lines:
            comp = line.component
            amount = float(line.amount)
            
            entry = {"code": comp.code, "name": comp.name, "amount": amount}
            
            if comp.type == "earning":
                earnings.append(entry)
                earnings_sum["gross_earnings"] += amount
                
                if comp.code == "BASIC": earnings_sum["basic"] += amount
                elif comp.code == "HRA": earnings_sum["hra"] += amount
                elif comp.code in ["SPL_ALLOW", "SPECIAL"]: earnings_sum["special_allowance"] += amount
                else: earnings_sum["other_earnings"] += amount
                
            elif comp.type == "deduction":
                # Check if it's employer contribution (usually handled separately or marked)
                # For now, assuming standard codes for ER contribs if they appear in lines
                # But usually ER contribs are separate lines or not in 'deductions' list for net pay
                # Let's assume standard deduction codes
                
                if comp.code in ["PF_ER", "PF_ER_EPF", "PF_ER_EPS", "ESI_ER", "LWF_ER"]:
                    # Employer contribution
                    employer_contrib["total_employer_contrib"] += amount
                    if "PF_ER" in comp.code: employer_contrib["pf_employer"] += amount
                    elif comp.code == "ESI_ER": employer_contrib["esi_employer"] += amount
                    elif comp.code == "LWF_ER": employer_contrib["lwf_employer"] += amount
                else:
                    # Employee deduction
                    deductions.append(entry)
                    deductions_sum["total_deductions"] += amount
                    
                    if comp.code == "PF_EMP": deductions_sum["pf_employee"] += amount
                    elif comp.code == "ESI_EMP": deductions_sum["esi_employee"] += amount
                    elif comp.code in ["PT", "PT_MH"]: deductions_sum["professional_tax"] += amount
                    elif comp.code in ["LWF", "LWF_EMP"]: deductions_sum["lwf_employee"] += amount
                    else: deductions_sum["other_deductions"] += amount

        # Totals
        gross_pay = float(item.gross or 0)
        net_pay = float(item.net or 0)
        ctc = earnings_sum["gross_earnings"] + employer_contrib["total_employer_contrib"]
        
        dto = {
            "company": {
                "id": company.id,
                "code": company.code,
                "name": company.name,
                # "address_line1": company.address_line1, # Assuming these exist
                # "pan": company.pan
            },
            "employee": {
                "id": emp.id,
                "code": emp.code,
                "name": f"{emp.first_name} {emp.last_name or ''}".strip(),
                "department": dept.name if dept else None,
                "designation": desig.name if desig else None,
                "location": loc.name if loc else None,
                "grade": grade.name if grade else None,
                "cost_center": cc.code if cc else None,
                "doj": str(emp.doj) if emp.doj else None,
                # Statutory IDs (mocked as null if missing in model)
                "uan": None,
                "pf_number": None,
                "esi_number": None,
                "pan": None,
                "bank_name": bank.bank_name if bank else None,
                "bank_ifsc": bank.ifsc if bank else None,
                "bank_account_number": bank.account_number if bank else None
            },
            "run": {
                "pay_run_id": run.id,
                "year": run.period_start.year,
                "month": run.period_start.month,
                "period_start": str(run.period_start),
                "period_end": str(run.period_end),
                "status": run.status
            },
            "attendance": {
                "days_in_period": (run.period_end - run.period_start).days + 1,
                "days_worked": float(meta.get("days_worked", 0)),
                "lop_days": float(meta.get("lop_days", 0)),
                "ot_hours": float(meta.get("ot_hours", 0)),
                # "weekly_off_days": ... (if in meta)
            },
            "earnings": earnings,
            "earnings_summary": earnings_sum,
            "deductions": deductions,
            "deductions_summary": deductions_sum,
            "employer_contributions": employer_contrib,
            "totals": {
                "gross_pay": gross_pay,
                "net_pay": net_pay,
                "ctc_monthly": ctc
            }
        }
        return dto

    def render_payslip_html(self, dto: dict) -> str:
        # Load template (in real app, use render_template)
        # For now, we'll read the file content manually or use render_template_string if we had the string
        # But better to use Flask's render_template if we are in request context
        from flask import render_template
        return render_template("payslip.html", **dto)
