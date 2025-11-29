import json
import sys

try:
    with open(r'c:\Users\omkar\Desktop\HRMS\apps\backend\HRMS_Face_Attendance.postman_collection.json', 'r') as f:
        content = f.read()
        # print first 500 chars to debug
        print("--- START OF FILE ---")
        print(content[:500])
        print("--- END OF START ---")
        json.loads(content)
    print("JSON is valid")
except json.JSONDecodeError as e:
    print(f"JSON error: {e}")
    print(f"At line {e.lineno}, column {e.colno}")
    # Print context
    lines = content.splitlines()
    if e.lineno - 1 < len(lines):
        print(f"Line {e.lineno}: {lines[e.lineno-1]}")
except Exception as e:
    print(f"Error: {e}")
