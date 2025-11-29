import requests
import io
import os

BASE_URL = "http://localhost:5001/api/v1"
ADMIN_EMAIL = "admin@hrms.local"
PASSWORD = "password"

def verify_payslips():
    session = requests.Session()
    
    # 1. Login as Admin
    print("Logging in as Admin...")
    resp = session.post(f"{BASE_URL}/auth/login", json={"email": ADMIN_EMAIL, "password": PASSWORD})
    if resp.status_code != 200:
        print("Login failed:", resp.text)
        return
    token = resp.json()["access"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # 2. List Payslips (Admin)
    print("\n[Admin] Listing Payslips for Company 13, Nov 2025...")
    resp = session.get(
        f"{BASE_URL}/payroll/payslips",
        headers=headers,
        params={"company_id": 13, "year": 2025, "month": 11}
    )
    if resp.status_code != 200:
        print("List failed:", resp.text)
        return
    
    data = resp.json()["data"]
    print(f"Found {len(data['items'])} items.")
    if not data["items"]:
        print("No items found to test detail/download.")
        return
        
    emp_id = data["items"][0]["employee_id"]
    print(f"Testing with Employee ID: {emp_id}")
    
    # 3. Get Single Payslip DTO (Admin)
    print(f"\n[Admin] Getting Payslip DTO for Emp {emp_id}...")
    resp = session.get(
        f"{BASE_URL}/payroll/payslips/{emp_id}",
        headers=headers,
        params={"company_id": 13, "year": 2025, "month": 11}
    )
    if resp.status_code != 200:
        print("Get DTO failed:", resp.text)
    else:
        dto = resp.json()
        print("DTO Keys:", dto.keys())
        print("Net Pay:", dto["totals"]["net_pay"])
        
    # 4. Download HTML (Admin)
    print(f"\n[Admin] Downloading HTML for Emp {emp_id}...")
    resp = session.get(
        f"{BASE_URL}/payroll/payslips/{emp_id}/download",
        headers=headers,
        params={"company_id": 13, "year": 2025, "month": 11, "format": "html"}
    )
    if resp.status_code != 200:
        print("Download failed:", resp.text)
    else:
        print("Content-Type:", resp.headers.get("Content-Type"))
        print("Content-Disposition:", resp.headers.get("Content-Disposition"))
        if "<html>" in resp.text:
            print("SUCCESS: HTML content received.")
        else:
            print("WARNING: HTML tag not found in response.")

    # 5. ESS Test (Mocking ESS by using Admin token but hitting self endpoints - Admin usually has employee record too?)
    # Wait, admin@hrms.local might not be linked to an employee record.
    # We need a user who IS an employee.
    # Let's try to find a user who is an employee from the list we just fetched.
    # But we don't have their credentials easily unless we reset them or use a known demo user.
    # The seed script created 'emp@swstk.in' / '4445' linked to an employee.
    # Let's try that.
    
    print("\nLogging in as Employee (emp@swstk.in)...")
    resp = session.post(f"{BASE_URL}/auth/login", json={"email": "emp@swstk.in", "password": "4445"})
    if resp.status_code != 200:
        print("Employee Login failed (skipping ESS tests):", resp.text)
        return
        
    emp_token = resp.json()["access"]
    emp_headers = {"Authorization": f"Bearer {emp_token}"}
    
    # 6. List Own Payslips (ESS)
    print("\n[ESS] Listing Own Payslips...")
    resp = session.get(f"{BASE_URL}/self/payslips", headers=emp_headers)
    if resp.status_code != 200:
        print("ESS List failed:", resp.text)
    else:
        data = resp.json()["data"]
        print(f"Found {len(data['items'])} payslips.")
        
    # 7. Download Own Payslip (ESS)
    # Need year/month from the list if available, or just try 2025/11
    print("\n[ESS] Downloading Own Payslip (Nov 2025)...")
    resp = session.get(
        f"{BASE_URL}/self/payslips/download",
        headers=emp_headers,
        params={"year": 2025, "month": 11, "format": "html"}
    )
    if resp.status_code == 404:
        print("Payslip not found (expected if this employee wasn't in the seeded run).")
    elif resp.status_code != 200:
        print("ESS Download failed:", resp.text)
    else:
        print("SUCCESS: ESS Download worked.")

if __name__ == "__main__":
    verify_payslips()
