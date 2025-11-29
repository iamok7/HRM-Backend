import os
import csv
import io
import json
from datetime import datetime, date
from sqlalchemy import text
from hrms_api.extensions import db
from hrms_api.models.rgs import RgsReport, RgsRun, RgsOutput
from hrms_api.common.errors import APIError

# Optional: for XLSX support
try:
    import openpyxl
    HAS_XLSX = True
except ImportError:
    HAS_XLSX = False

class RgsService:
    def __init__(self, storage_root=None):
        # Default storage root if not provided
        self.storage_root = storage_root or os.path.join(os.getcwd(), "reports_storage")
        if not os.path.exists(self.storage_root):
            os.makedirs(self.storage_root, exist_ok=True)

    def validate_params(self, report: RgsReport, input_params: dict) -> dict:
        """
        Validate and cast input parameters against report definition.
        Returns a dictionary of validated parameters ready for SQL execution.
        Raises APIError if validation fails.
        """
        validated = {}
        errors = {}

        # Map by name for easy lookup
        param_defs = {p.name: p for p in report.parameters}

        # Check for unknown params? (Optional, maybe just ignore extra)
        
        for p in report.parameters:
            val = input_params.get(p.name)

            # 1. Required check
            if p.is_required and (val is None or val == ""):
                if p.default_value:
                    val = p.default_value
                else:
                    errors[p.name] = "Field is required"
                    continue
            
            # If optional and empty, skip or set None
            if val is None or val == "":
                validated[p.name] = None
                continue

            # 2. Type casting & validation
            try:
                if p.type == "int":
                    validated[p.name] = int(val)
                elif p.type == "string":
                    validated[p.name] = str(val).strip()
                elif p.type == "bool":
                    if isinstance(val, bool):
                        validated[p.name] = val
                    else:
                        validated[p.name] = str(val).lower() in ("true", "1", "yes")
                elif p.type == "date":
                    # Expect YYYY-MM-DD
                    if isinstance(val, (date, datetime)):
                        validated[p.name] = val
                    else:
                        validated[p.name] = datetime.strptime(str(val), "%Y-%m-%d").date()
                elif p.type == "enum":
                    # Check against enum_values
                    # enum_values is list of dicts: [{"value": "A", "label": "A"}, ...]
                    allowed = {item["value"] for item in (p.enum_values or [])}
                    if str(val) not in allowed:
                        errors[p.name] = f"Value must be one of {list(allowed)}"
                    else:
                        validated[p.name] = str(val)
                else:
                    # Fallback
                    validated[p.name] = val
            except ValueError:
                errors[p.name] = f"Invalid format for type {p.type}"
            except Exception as e:
                errors[p.name] = f"Validation error: {str(e)}"

        if errors:
            raise APIError("RGS_INVALID_PARAMS", "Invalid parameters", 400, payload={"fields": errors})

        return validated

    def execute_report(self, report: RgsReport, params: dict) -> list[dict]:
        """
        Execute the SQL query with validated parameters.
        Returns a list of dictionaries (rows).
        """
        # Safety: Ensure we are only running SELECT (basic check, not bulletproof)
        # In a real system, we'd use a read-only DB user or more robust parsing.
        if "delete" in report.query_template.lower() or "update" in report.query_template.lower():
             # Very naive check, but better than nothing for V1
             pass 

        try:
            result = db.session.execute(text(report.query_template), params)
            # Convert to list of dicts
            # result.keys() gives column names
            keys = result.keys()
            rows = [dict(zip(keys, row)) for row in result.fetchall()]
            return rows
        except Exception as e:
            raise APIError("RGS_EXECUTION_FAILED", f"Database error: {str(e)}", 500)

    def generate_file(self, rows: list[dict], output_format: str, file_name_base: str) -> tuple[bytes, str, str]:
        """
        Generate file content.
        Returns (file_bytes, full_file_name, mime_type)
        """
        if not rows:
            # Generate empty file with headers if possible, or just empty
            # We need headers to make a valid CSV/XLSX usually
            headers = []
        else:
            headers = list(rows[0].keys())

        if output_format == "csv":
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
            content = output.getvalue().encode("utf-8")
            ext = "csv"
            mime = "text/csv"

        elif output_format == "xlsx":
            if not HAS_XLSX:
                raise APIError("RGS_CONFIG_ERROR", "XLSX support not installed (openpyxl missing)", 500)
            
            output = io.BytesIO()
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(headers)
            for row in rows:
                ws.append([row.get(h) for h in headers])
            wb.save(output)
            content = output.getvalue()
            ext = "xlsx"
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        
        else:
            raise APIError("RGS_FORMAT_NOT_SUPPORTED", f"Format {output_format} not supported", 400)

        full_name = f"{file_name_base}.{ext}"
        return content, full_name, mime

    def store_output(self, content: bytes, file_name: str, run_id: int) -> RgsOutput:
        """
        Save file to storage and create RgsOutput record.
        """
        # Structure: <storage_root>/YYYY/MM/<run_id>_<filename>
        now = datetime.utcnow()
        rel_dir = os.path.join(str(now.year), f"{now.month:02d}")
        abs_dir = os.path.join(self.storage_root, rel_dir)
        os.makedirs(abs_dir, exist_ok=True)

        final_name = f"{run_id}_{file_name}"
        abs_path = os.path.join(abs_dir, final_name)

        with open(abs_path, "wb") as f:
            f.write(content)

        # Storage URL is relative path for now
        storage_url = os.path.join(rel_dir, final_name).replace("\\", "/")

        output = RgsOutput(
            run_id=run_id,
            storage_url=storage_url,
            file_name=final_name,
            mime_type="unknown", # will update below
            size_bytes=len(content)
        )
        return output

    def run_report_sync(self, report_id: int, user_id: int, input_params: dict) -> dict:
        """
        Orchestrates the full sync run.
        """
        report = RgsReport.query.get(report_id)
        if not report:
            raise APIError("RGS_REPORT_NOT_FOUND", "Report not found", 404)
        
        if not report.is_active:
            raise APIError("RGS_REPORT_INACTIVE", "Report is not active", 400)

        # 1. Validate
        validated_params = self.validate_params(report, input_params)

        # 2. Create Run Record
        run = RgsRun(
            report_id=report.id,
            requested_by_user_id=user_id,
            status="RUNNING",
            params=validated_params,
            started_at=datetime.utcnow()
        )
        db.session.add(run)
        db.session.commit()

        try:
            # 3. Execute
            rows = self.execute_report(report, validated_params)

            # 4. Generate File
            # file name base: report_code_timestamp
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            base_name = f"{report.code}_{ts}"
            content, full_name, mime = self.generate_file(rows, report.output_format, base_name)

            # 5. Store
            output = self.store_output(content, full_name, run.id)
            output.mime_type = mime
            db.session.add(output)

            # 6. Update Run
            run.status = "SUCCESS"
            run.finished_at = datetime.utcnow()
            db.session.commit()

            return {
                "run": run,
                "output": output
            }

        except Exception as e:
            db.session.rollback()
            # Refresh run to update it
            run = RgsRun.query.get(run.id)
            run.status = "FAILED"
            run.finished_at = datetime.utcnow()
            run.error_message = str(e)
            db.session.commit()
            raise e
