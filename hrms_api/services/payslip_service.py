from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import joinedload

from hrms_api.extensions import db
from hrms_api.models.payroll.pay_run import PayRun, PayRunItem, PayRunItemLine
from hrms_api.models.employee import Employee
from hrms_api.models.master import Company, Department, Designation, Location, Grade, CostCenter
from hrms_api.models.employee_bank import EmployeeBankAccount
from hrms_api.models.payroll.components import SalaryComponent
from .payroll_common import get_pay_run_for_period

@dataclass
class PayslipComponent:
    code: str
    name: str
    amount: Decimal

@dataclass
class PayslipDTO:
    company: Dict[str, Any]
    employee: Dict[str, Any]
    run: Dict[str, Any]
    attendance: Dict[str, Any]
    earnings: List[PayslipComponent]
    earnings_summary: Dict[str, Any]
    deductions: List[PayslipComponent]
    deductions_summary: Dict[str, Any]
    employer_contributions: Dict[str, Any]
    totals: Dict[str, Any]
    ytd: Optional[Dict[str, Any]] = None

EARNING_COMPONENT_CODES = {"BASIC", "HRA", "SPL_ALLOW", "SPECIAL"}
PF_EMP_CODES = {"PF_EMP"}
ESI_EMP_CODES = {"ESI_EMP"}
PT_CODES = {"PT", "PT_MH"}
LWF_EMP_CODES = {"LWF", "LWF_EMP"}
PF_ER_CODES = {"PF_ER", "PF_ER_EPF", "PF_ER_EPS"}
ESI_ER_CODES = {"ESI_ER"}
LWF_ER_CODES = {"LWF_ER"}

class PayslipService:
    def build_payslip_dto(self, item: PayRunItem) -> dict:
        """
        Constructs the Payslip DTO for a given PayRunItem.
        Returns a dictionary representation of the DTO.
        """
        # Ensure relationships are loaded if not already
        # In a real scenario, we might want to query this item with joinedloads if passed item is detached or partial
        # But assuming item comes from a query that included necessary joins or lazy loading works
        
        run = item.pay_run
        emp = item.employee
        company = run.company
        
        # Fetch related objects (optimization: could be eager loaded)
        # Using getattr with default None to be safe if relation is missing
        dept = emp.department
        desig = emp.designation
        loc = emp.location
        grade = emp.grade
        cc = emp.cost_center
        
        bank = EmployeeBankAccount.query.filter_by(employee_id=emp.id, is_primary=True).first()
        
        # Parse calc_meta
        meta = item.calc_meta
        if not isinstance(meta, dict):
            meta = {}
        
        # Process Lines
        earnings = []
        deductions = []
        
        gross_earnings = Decimal("0.00")
        pf_employee = Decimal("0.00")
        esi_employee = Decimal("0.00")
        professional_tax = Decimal("0.00")
        lwf_employee = Decimal("0.00")
        other_deductions = Decimal("0.00")
        
        pf_employer = Decimal("0.00")
        esi_employer = Decimal("0.00")
        lwf_employer = Decimal("0.00")
        
        for line in item.lines:
            comp = line.component
            code = (comp.code or "").upper()
            amt = Decimal(str(line.amount or 0))
            
            comp_entry = PayslipComponent(code=code, name=comp.name, amount=amt)
            
            if comp.type == "earning":
                earnings.append(comp_entry)
                gross_earnings += amt
            elif comp.type == "deduction":
                deductions.append(comp_entry)
                
                if code in PF_EMP_CODES:
                    pf_employee += amt
                elif code in ESI_EMP_CODES:
                    esi_employee += amt
                elif code in PT_CODES:
                    professional_tax += amt
                elif code in LWF_EMP_CODES:
                    lwf_employee += amt
                else:
                    other_deductions += amt
            
            # Employer contributions
            if code in PF_ER_CODES:
                pf_employer += amt
            elif code in ESI_ER_CODES:
                esi_employer += amt
            elif code in LWF_ER_CODES:
                lwf_employer += amt

        total_deductions = pf_employee + esi_employee + professional_tax + lwf_employee + other_deductions
        total_employer_contrib = pf_employer + esi_employer + lwf_employer
        
        gross_pay = Decimal(str(item.gross or 0))
        # If gross_pay from item differs slightly from sum of earnings, trust item.gross usually, 
        # but for breakdown consistency we might want to use calculated sum. 
        # The spec says "gross pay from engine", so we use item.gross.
        
        net_pay = Decimal(str(item.net or 0))
        ctc_monthly = gross_earnings + total_employer_contrib

        # Build DTO
        dto = PayslipDTO(
            company={
                "id": company.id,
                "code": company.code,
                "name": company.name,
                # "address_line1": company.address_line1, # Assuming these exist in model or mocked
                # "address_line2": company.address_line2,
                # "city": getattr(company, "city", None),
                # "state": getattr(company, "state", None),
                # "country": getattr(company, "country", None),
                # "pan": getattr(company, "pan", None),
                # "tan": getattr(company, "tan", None),
            },
            employee={
                "id": emp.id,
                "code": emp.code,
                "name": f"{emp.first_name} {emp.last_name or ''}".strip(),
                "department": dept.name if dept else None,
                "designation": desig.name if desig else None,
                "location": loc.name if loc else None,
                "grade": grade.name if grade else None,
                "cost_center": cc.code if cc else None,
                "doj": str(emp.doj) if emp.doj else None,
                "dol": str(emp.dol) if emp.dol else None,
                "employment_type": emp.employment_type,
                "status": emp.status,
                # Statutory IDs (mocked as null if missing in model)
                "uan": getattr(emp, "uan", None),
                "pf_number": getattr(emp, "pf_number", None),
                "esi_number": getattr(emp, "esi_number", None),
                "pan": getattr(emp, "pan", None),
                "bank_name": bank.bank_name if bank else None,
                "bank_ifsc": bank.ifsc if bank else None,
                "bank_account_number": bank.account_number if bank else None
            },
            run={
                "pay_run_id": run.id,
                "year": run.period_start.year,
                "month": run.period_start.month,
                "period_start": str(run.period_start),
                "period_end": str(run.period_end),
                "processed_on": str(run.created_at) if run.created_at else None, # using created_at as proxy for processed_at
                "status": run.status
            },
            attendance={
                "days_in_period": (run.period_end - run.period_start).days + 1,
                "days_worked": float(meta.get("days_worked", 0)),
                "lop_days": float(meta.get("lop_days", 0)),
                "ot_hours": float(meta.get("ot_hours", 0)),
            },
            earnings=earnings,
            earnings_summary={
                "basic": sum(c.amount for c in earnings if c.code == "BASIC"),
                "hra": sum(c.amount for c in earnings if c.code == "HRA"),
                "special_allowance": sum(c.amount for c in earnings if c.code in {"SPL_ALLOW","SPECIAL"}),
                "other_earnings": sum(
                    c.amount for c in earnings
                    if c.code not in {"BASIC", "HRA", "SPL_ALLOW", "SPECIAL"}
                ),
                "gross_earnings": gross_earnings,
            },
            deductions=deductions,
            deductions_summary={
                "pf_employee": pf_employee,
                "esi_employee": esi_employee,
                "professional_tax": professional_tax,
                "lwf_employee": lwf_employee,
                "other_deductions": other_deductions,
                "total_deductions": total_deductions,
            },
            employer_contributions={
                "pf_employer": pf_employer,
                "esi_employer": esi_employer,
                "lwf_employer": lwf_employer,
                "total_employer_contrib": total_employer_contrib,
            },
            totals={
                "gross_pay": gross_pay,
                "net_pay": net_pay,
                "ctc_monthly": ctc_monthly,
            },
            ytd=None
        )
        
        return asdict(dto)

    def render_payslip_html(self, dto: dict) -> str:
        from flask import render_template
        return render_template("payroll/payslip.html", payslip=dto)
