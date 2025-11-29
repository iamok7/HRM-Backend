import json
import os

# Define the structure
collection = {
    "info": {
        "name": "HRMS Face Attendance",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
    },
    "item": [],
    "auth": {
        "type": "bearer",
        "bearer": [
            {
                "key": "token",
                "value": "{{access_token}}",
                "type": "string"
            }
        ]
    },
    "event": [
        {
            "listen": "prerequest",
            "script": {
                "type": "text/javascript",
                "exec": [""]
            }
        },
        {
            "listen": "test",
            "script": {
                "type": "text/javascript",
                "exec": [""]
            }
        }
    ],
    "variable": [
        {
            "key": "base_url",
            "value": "http://localhost:5001",
            "type": "string"
        },
        {
            "key": "access_token",
            "value": "",
            "type": "string"
        }
    ]
}

# Login Request (Admin)
login_request = {
    "name": "Login (Admin)",
    "request": {
        "method": "POST",
        "header": [],
        "body": {
            "mode": "raw",
            "raw": "{\n    \"email\": \"admin@demo.local\",\n    \"password\": \"4445\"\n}",
            "options": {
                "raw": {
                    "language": "json"
                }
            }
        },
        "url": {
            "raw": "{{base_url}}/api/v1/auth/login",
            "host": ["{{base_url}}"],
            "path": ["api", "v1", "auth", "login"]
        }
    },
    "event": [
        {
            "listen": "test",
            "script": {
                "type": "text/javascript",
                "exec": [
                    "var jsonData = pm.response.json();",
                    "pm.collectionVariables.set(\"access_token\", jsonData.access);"
                ]
            }
        }
    ],
    "response": []
}

# Login Request (Employee)
login_employee_request = {
    "name": "Login (Employee)",
    "request": {
        "method": "POST",
        "header": [],
        "body": {
            "mode": "raw",
            "raw": "{\n    \"email\": \"emp@swstk.in\",\n    \"password\": \"4445\"\n}",
            "options": {
                "raw": {
                    "language": "json"
                }
            }
        },
        "url": {
            "raw": "{{base_url}}/api/v1/auth/login",
            "host": ["{{base_url}}"],
            "path": ["api", "v1", "auth", "login"]
        }
    },
    "event": [
        {
            "listen": "test",
            "script": {
                "type": "text/javascript",
                "exec": [
                    "var jsonData = pm.response.json();",
                    "pm.collectionVariables.set(\"access_token\", jsonData.access);"
                ]
            }
        }
    ],
    "response": []
}

# Auth Folder
auth_folder = {
    "name": "Auth",
    "item": [login_request, login_employee_request]
}

# HR Folder Items
hr_items = [
    {
        "name": "Enroll Face",
        "request": {
            "auth": {
                "type": "bearer",
                "bearer": [
                    {"key": "token", "value": "{{access_token}}", "type": "string"}
                ]
            },
            "method": "POST",
            "header": [],
            "body": {
                "mode": "formdata",
                "formdata": [
                    {"key": "employee_id", "value": "1", "type": "text"},
                    {"key": "image", "type": "file", "src": []},
                    {"key": "label", "value": "primary", "type": "text"}
                ]
            },
            "url": {
                "raw": "{{base_url}}/api/v1/attendance/face/enroll",
                "host": ["{{base_url}}"],
                "path": ["api", "v1", "attendance", "face", "enroll"]
            }
        },
        "response": []
    },
    {
        "name": "List Profiles",
        "request": {
            "method": "GET",
            "header": [],
            "url": {
                "raw": "{{base_url}}/api/v1/attendance/face/profiles?employee_id=1",
                "host": ["{{base_url}}"],
                "path": ["api", "v1", "attendance", "face", "profiles"],
                "query": [{"key": "employee_id", "value": "1"}]
            }
        },
        "response": []
    },
    {
        "name": "Deactivate Profile",
        "request": {
            "method": "POST",
            "header": [],
            "url": {
                "raw": "{{base_url}}/api/v1/attendance/face/profiles/1/deactivate",
                "host": ["{{base_url}}"],
                "path": ["api", "v1", "attendance", "face", "profiles", "1", "deactivate"]
            }
        },
        "response": []
    },
    {
        "name": "Verify Face Match",
        "request": {
            "method": "POST",
            "header": [],
            "body": {
                "mode": "formdata",
                "formdata": [
                    {"key": "image", "type": "file", "src": []}
                ]
            },
            "url": {
                "raw": "{{base_url}}/api/v1/attendance/face/verify-match",
                "host": ["{{base_url}}"],
                "path": ["api", "v1", "attendance", "face", "verify-match"]
            }
        },
        "response": []
    },
    {
        "name": "View Logs",
        "request": {
            "method": "GET",
            "header": [],
            "url": {
                "raw": "{{base_url}}/api/v1/attendance/face/logs?employee_id=1&page=1&limit=20",
                "host": ["{{base_url}}"],
                "path": ["api", "v1", "attendance", "face", "logs"],
                "query": [
                    {"key": "employee_id", "value": "1"},
                    {"key": "page", "value": "1"},
                    {"key": "limit", "value": "20"}
                ]
            }
        },
        "response": []
    }
]

hr_folder = {
    "name": "Face Attendance (HR)",
    "item": hr_items
}

# Employee Folder Items
employee_items = [
    {
        "name": "Self Punch",
        "request": {
            "method": "POST",
            "header": [],
            "body": {
                "mode": "formdata",
                "formdata": [
                    {"key": "image", "type": "file", "src": []},
                    {"key": "lat", "value": "18.5204", "type": "text"},
                    {"key": "lng", "value": "73.8567", "type": "text"},
                    {"key": "punch_type", "value": "IN", "type": "text"},
                    {"key": "device_id", "value": "mobile-123", "type": "text"}
                ]
            },
            "url": {
                "raw": "{{base_url}}/api/v1/attendance/face/self-punch",
                "host": ["{{base_url}}"],
                "path": ["api", "v1", "attendance", "face", "self-punch"]
            }
        },
        "response": []
    }
]

employee_folder = {
    "name": "Face Attendance (Employee)",
    "item": employee_items
}

# Assemble Collection
collection["item"] = [auth_folder, hr_folder, employee_folder]

# Write to file
file_path = r'c:\Users\omkar\Desktop\HRMS\apps\backend\HRMS_Face_Attendance.postman_collection.json'
with open(file_path, 'w') as f:
    json.dump(collection, f, indent=4)

print(f"Successfully restored {file_path}")
