from hrms_api import create_app
from hrms_api.extensions import db
from hrms_api.models.rgs import RgsReport, RgsReportParameter

app = create_app()

QUERY_TEMPLATE = """
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
        r.id                            AS pay_run_id,
        r.company_id                    AS company_id,
        r.period_start,
        r.period_end,

        e.id                            AS employee_id,
        e.code                          AS emp_code,
        (e.first_name || ' ' || COALESCE(e.last_name, '')) AS employee_name,
        d.name                          AS department,
        desig.name                      AS designation,
        loc.name                        AS location,
        g.name                          AS grade,
        cc.code                         AS cost_center,
        e.doj,
        e.dol,
        e.employment_type,
        e.status,

        -- attendance used for payroll (from calc_meta)
        (r.period_end - r.period_start + 1)              AS days_in_period,
        COALESCE(CAST(i.calc_meta->>'days_worked' AS NUMERIC), 0)   AS days_worked,
        COALESCE(CAST(i.calc_meta->>'lop_days' AS NUMERIC), 0)      AS lop_days,
        COALESCE(CAST(i.calc_meta->>'ot_hours' AS NUMERIC), 0)      AS ot_hours,

        -- bank
        bank.bank_name,
        bank.ifsc                                        AS bank_ifsc,
        bank.account_number                              AS bank_account_number,

        -- statutory ids (MISSING in model, selecting NULL)
        NULL AS pf_number,
        NULL AS uan,
        NULL AS esi_number,
        NULL AS pan,

        -- money from item (gross/net)
        COALESCE(i.gross, 0)                             AS gross_pay,
        COALESCE(i.net, COALESCE(i.gross, 0))            AS net_pay,

        -- line-level
        l.amount                                         AS line_amount,
        sc.code                                          AS comp_code,
        sc.type                                          AS comp_type
    FROM run r
    JOIN pay_run_items i       ON i.pay_run_id = r.id
    JOIN employees e           ON e.id = i.employee_id
    LEFT JOIN departments d    ON d.id = e.department_id
    LEFT JOIN designations desig ON desig.id = e.designation_id
    LEFT JOIN locations loc    ON loc.id = e.location_id
    LEFT JOIN grades g         ON g.id = e.grade_id
    LEFT JOIN cost_centers cc  ON cc.id = e.cost_center_id
    LEFT JOIN employee_bank_accounts bank
           ON bank.employee_id = e.id AND bank.is_primary = TRUE
    LEFT JOIN pay_run_item_lines l
           ON l.item_id = i.id
    LEFT JOIN salary_components sc
           ON sc.id = l.component_id
)
SELECT
    -- run / period
    b.company_id,
    c.name                                AS company_name,
    b.pay_run_id,
    EXTRACT(YEAR FROM b.period_start)::int  AS year,
    EXTRACT(MONTH FROM b.period_start)::int AS month,
    b.period_start,
    b.period_end,

    -- employee / org
    b.employee_id,
    b.emp_code,
    b.employee_name,
    b.department,
    b.designation,
    b.location,
    b.grade,
    b.cost_center,
    b.doj,
    b.dol,
    b.employment_type,
    b.status,

    -- attendance for payroll
    b.days_in_period,
    b.days_worked,
    b.lop_days,
    b.ot_hours,

    -- earnings
    SUM(CASE WHEN b.comp_code = 'BASIC' THEN b.line_amount ELSE 0 END)                     AS basic,
    SUM(CASE WHEN b.comp_code = 'HRA'   THEN b.line_amount ELSE 0 END)                     AS hra,
    SUM(CASE WHEN b.comp_code IN ('SPL_ALLOW','SPECIAL') THEN b.line_amount ELSE 0 END)    AS special_allowance,
    SUM(CASE WHEN b.comp_type = 'earning'
              AND b.comp_code NOT IN ('BASIC','HRA','SPL_ALLOW','SPECIAL',
                                      'PF_EMP','ESI_EMP','PT','PT_MH','LWF_EMP',
                                      'PF_ER','PF_ER_EPF','PF_ER_EPS','ESI_ER','LWF_ER')
        THEN b.line_amount ELSE 0 END)                                                     AS other_earnings,
    SUM(CASE WHEN b.comp_type = 'earning' THEN b.line_amount ELSE 0 END)                   AS gross_earnings,

    -- employee deductions
    SUM(CASE WHEN b.comp_code = 'PF_EMP' THEN b.line_amount ELSE 0 END)                    AS pf_employee,
    SUM(CASE WHEN b.comp_code = 'ESI_EMP' THEN b.line_amount ELSE 0 END)                   AS esi_employee,
    SUM(CASE WHEN b.comp_code IN ('PT','PT_MH') THEN b.line_amount ELSE 0 END)             AS professional_tax,
    SUM(CASE WHEN b.comp_code IN ('LWF','LWF_EMP') THEN b.line_amount ELSE 0 END)          AS lwf_employee,
    SUM(CASE WHEN b.comp_type = 'deduction'
              AND b.comp_code NOT IN ('PF_EMP','ESI_EMP','PT','PT_MH','LWF','LWF_EMP')
        THEN b.line_amount ELSE 0 END)                                                     AS other_deductions,
    SUM(CASE WHEN b.comp_type = 'deduction' THEN b.line_amount ELSE 0 END)                 AS total_deductions,

    -- employer contributions
    SUM(CASE WHEN b.comp_code IN ('PF_ER','PF_ER_EPF','PF_ER_EPS') THEN b.line_amount ELSE 0 END) AS pf_employer,
    SUM(CASE WHEN b.comp_code = 'ESI_ER' THEN b.line_amount ELSE 0 END)                           AS esi_employer,
    SUM(CASE WHEN b.comp_code = 'LWF_ER' THEN b.line_amount ELSE 0 END)                           AS lwf_employer,
    SUM(CASE WHEN b.comp_code IN ('PF_ER','PF_ER_EPF','PF_ER_EPS','ESI_ER','LWF_ER')
        THEN b.line_amount ELSE 0 END)                                                             AS total_employer_contrib,

    -- final money fields (item + computed)
    b.gross_pay,
    b.net_pay,
    (SUM(CASE WHEN b.comp_type = 'earning' THEN b.line_amount ELSE 0 END)
     + SUM(CASE WHEN b.comp_code IN ('PF_ER','PF_ER_EPF','PF_ER_EPS','ESI_ER','LWF_ER')
            THEN b.line_amount ELSE 0 END)
    ) AS ctc_monthly,

    -- bank
    b.bank_name,
    b.bank_ifsc,
    b.bank_account_number,

    -- statutory ids
    b.pf_number,
    b.uan,
    b.esi_number,
    b.pan

FROM base b
LEFT JOIN companies c ON c.id = b.company_id
GROUP BY
    b.company_id, c.name, b.pay_run_id, b.period_start, b.period_end,
    b.employee_id, b.emp_code, b.employee_name, b.department, b.designation,
    b.location, b.grade, b.cost_center, b.doj, b.dol, b.employment_type,
    b.status, b.days_in_period, b.days_worked, b.lop_days, b.ot_hours,
    b.bank_name, b.bank_ifsc, b.bank_account_number,
    b.pf_number, b.uan, b.esi_number, b.pan,
    b.gross_pay, b.net_pay
ORDER BY b.emp_code;
"""

with app.app_context():
    # Check if exists
    report = RgsReport.query.filter_by(code="PAYROLL_REGISTER").first()
    if not report:
        print("Creating PAYROLL_REGISTER report...")
        report = RgsReport(
            code="PAYROLL_REGISTER",
            name="Payroll - Monthly Pay Register",
            description="Detailed monthly payroll register with earnings, deductions, and net pay.",
            category="payroll",
            query_template=QUERY_TEMPLATE,
            output_format="xlsx",
            is_active=True,
            created_by_user_id=1 # Assuming admin user 1 exists
        )
        db.session.add(report)
        db.session.flush()
        
        # Add Parameters
        params = [
            RgsReportParameter(report_id=report.id, name="company_id", label="Company", type="int", is_required=True, order_index=1),
            RgsReportParameter(report_id=report.id, name="year", label="Year", type="int", is_required=True, order_index=2),
            RgsReportParameter(report_id=report.id, name="month", label="Month", type="int", is_required=True, order_index=3),
        ]
        db.session.add_all(params)
        db.session.commit()
        print("Report created successfully.")
    else:
        print("Updating PAYROLL_REGISTER report...")
        report.query_template = QUERY_TEMPLATE
        
        # Reset parameters to ensure correctness
        RgsReportParameter.query.filter_by(report_id=report.id).delete()
        
        params = [
            RgsReportParameter(report_id=report.id, name="company_id", label="Company", type="int", is_required=True, order_index=1),
            RgsReportParameter(report_id=report.id, name="year", label="Year", type="int", is_required=True, order_index=2),
            RgsReportParameter(report_id=report.id, name="month", label="Month", type="int", is_required=True, order_index=3),
        ]
        db.session.add_all(params)
        
        db.session.commit()
        print("Report updated successfully.")
