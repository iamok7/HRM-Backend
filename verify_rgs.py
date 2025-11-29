import requests
import sys

BASE_URL = "http://127.0.0.1:5000/api/v1"

def login(email, password):
    resp = requests.post(f"{BASE_URL}/auth/login", json={"email": email, "password": password})
    if resp.status_code != 200:
        print(f"Login failed: {resp.text}")
        sys.exit(1)
    data = resp.json()
    print("Login response:", data)
    if "access" in data:
        return data["access"]
    if "access_token" in data:
        return data["access_token"]
    if "data" in data and "access_token" in data["data"]:
        return data["data"]["access_token"]
    raise KeyError("access_token not found in response")

def verify_rgs():
    print("1. Logging in as admin...")
    token = login("admin@demo.local", "4445")
    headers = {"Authorization": f"Bearer {token}"}

    print("\n2. Listing reports...")
    resp = requests.get(f"{BASE_URL}/rgs/reports", headers=headers)
    if resp.status_code != 200:
        print(f"List reports failed: {resp.text}")
        return
    reports = resp.json()["data"]
    print(f"Found {len(reports)} reports.")
    for r in reports:
        print(f" - {r['id']}: {r['code']} ({r['name']})")

    if not reports:
        print("No reports found. Seeding might have failed.")
        return

    # Find ATTENDANCE_MONTHLY
    report = next((r for r in reports if r["code"] == "ATTENDANCE_MONTHLY"), None)
    if not report:
        print("ATTENDANCE_MONTHLY not found.")
        return

    print(f"\n3. Getting details for report {report['id']}...")
    resp = requests.get(f"{BASE_URL}/rgs/reports/{report['id']}", headers=headers)
    details = resp.json()["data"]
    print("Params:", [p["name"] for p in details["params"]])

    print(f"\n4. Running report {report['id']}...")
    # Need valid params. Assuming company_id=1 exists (DEMO company usually id=1)
    params = {
        "company_id": 1,
        "month": 11,
        "year": 2025
    }
    resp = requests.post(f"{BASE_URL}/rgs/reports/{report['id']}/run", headers=headers, json={"params": params})
    if resp.status_code != 200:
        print(f"Run report failed: {resp.text}")
        return
    
    run_data = resp.json()["data"]
    run_id = run_data["run"]["id"]
    print(f"Run ID: {run_id}, Status: {run_data['run']['status']}")
    
    if run_data["run"]["status"] == "SUCCESS":
        outputs = run_data["outputs"]
        if outputs:
            out_id = outputs[0]["id"]
            print(f"Output generated: {outputs[0]['file_name']} (ID: {out_id})")
            
            print(f"\n5. Downloading output {out_id}...")
            resp = requests.get(f"{BASE_URL}/rgs/outputs/{out_id}/download", headers=headers)
            if resp.status_code == 200:
                print(f"Download success! Size: {len(resp.content)} bytes")
                with open("downloaded_report.xlsx", "wb") as f:
                    f.write(resp.content)
                print("Saved to downloaded_report.xlsx")
            else:
                print(f"Download failed: {resp.status_code}")
        else:
            print("No output generated?")
    else:
        print(f"Run failed: {run_data['run'].get('error_message')}")

if __name__ == "__main__":
    verify_rgs()
