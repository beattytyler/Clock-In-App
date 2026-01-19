from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from datetime import datetime, timedelta, date
from typing import Tuple
from .models import db, Employee, TimeRecord

main_bp = Blueprint('main', __name__)

PAY_PERIOD_LENGTH_DAYS = 14
# Anchor bi-weekly periods so 12/22/2025-01/04/2026 is the first window and
# 01/05/2026-01/18/2026 is the next.
REFERENCE_PAY_PERIOD_START = date(2025, 12, 22)
TEST_EMPLOYEE_CODE = "0430"
TEST_EMPLOYEE_NAME = "Test Employee"


def get_pay_period_bounds(target_date: date) -> Tuple[date, date]:
    """Return the start and end dates (inclusive) for the bi-weekly period that contains target_date."""
    diff_days = (target_date - REFERENCE_PAY_PERIOD_START).days
    period_index = diff_days // PAY_PERIOD_LENGTH_DAYS
    start = REFERENCE_PAY_PERIOD_START + timedelta(days=period_index * PAY_PERIOD_LENGTH_DAYS)
    end = start + timedelta(days=PAY_PERIOD_LENGTH_DAYS - 1)
    return start, end


def _blank_admin_report():
    return {
        "records": [],
        "total_hours": 0,
        "selected_employee": None,
        "view_mode": "custom",
        "error": None,
        "show_results": False,
        "start_date_value": "",
        "end_date_value": "",
        "pay_period_date_value": "",
        "custom_start": None,
        "custom_end": None,
        "pay_period_start": None,
        "pay_period_end": None,
    }


def _build_admin_report(form):
    report = _blank_admin_report()
    report["view_mode"] = form.get("view_mode", "custom")
    report["start_date_value"] = (form.get("start_date") or "").strip()
    report["end_date_value"] = (form.get("end_date") or "").strip()
    report["pay_period_date_value"] = (form.get("pay_period_date") or "").strip()

    employee_id = form.get("employee_id")
    if employee_id and employee_id != "all":
        report["selected_employee"] = Employee.query.get(employee_id)

    range_start = None
    range_end = None

    if report["view_mode"] == "pay_period":
        if report["pay_period_date_value"]:
            target_date = datetime.strptime(report["pay_period_date_value"], "%Y-%m-%d").date()
        else:
            target_date = datetime.now().date()
            report["pay_period_date_value"] = target_date.strftime("%Y-%m-%d")

        report["pay_period_start"], report["pay_period_end"] = get_pay_period_bounds(target_date)
        range_start = datetime.combine(report["pay_period_start"], datetime.min.time())
        range_end = datetime.combine(report["pay_period_end"] + timedelta(days=1), datetime.min.time())
    else:
        if report["start_date_value"] and report["end_date_value"]:
            report["custom_start"] = datetime.strptime(report["start_date_value"], "%Y-%m-%d").date()
            report["custom_end"] = datetime.strptime(report["end_date_value"], "%Y-%m-%d").date()
            range_start = datetime.combine(report["custom_start"], datetime.min.time())
            range_end = datetime.combine(report["custom_end"] + timedelta(days=1), datetime.min.time())
        else:
            report["error"] = "Select both start and end dates for a custom range."

    if range_start and range_end:
        query = TimeRecord.query.filter(
            TimeRecord.clock_in >= range_start,
            TimeRecord.clock_in < range_end
        )
        if report["selected_employee"]:
            query = query.filter_by(employee_id=report["selected_employee"].id)

        report["records"] = query.order_by(TimeRecord.clock_in).all()
        report["total_hours"] = sum(
            ((record.clock_out - record.clock_in).total_seconds() / 3600)
            for record in report["records"] if record.clock_out
        )
        report["show_results"] = True

    return report


def _serialize_admin_report(report):
    period_label = None
    if report["view_mode"] == "pay_period" and report["pay_period_start"] and report["pay_period_end"]:
        period_label = (
            f"Pay Period: {report['pay_period_start'].strftime('%b %d, %Y')} - "
            f"{report['pay_period_end'].strftime('%b %d, %Y')}"
        )
    elif report["custom_start"] and report["custom_end"]:
        period_label = (
            f"Custom Range: {report['custom_start'].strftime('%b %d, %Y')} - "
            f"{report['custom_end'].strftime('%b %d, %Y')}"
        )

    records = []
    for record in report["records"]:
        hours = None
        if record.clock_out:
            hours = round(
                (record.clock_out - record.clock_in).total_seconds() / 3600,
                2
            )

        records.append({
            "employee": record.employee.name,
            "clock_in": record.clock_in.strftime("%b %d, %Y %I:%M %p"),
            "clock_out": record.clock_out.strftime("%b %d, %Y %I:%M %p") if record.clock_out else None,
            "hours": hours,
        })

    return {
        "error": report["error"],
        "show_results": report["show_results"],
        "view_mode": report["view_mode"],
        "period_label": period_label,
        "total_hours": round(report["total_hours"], 2),
        "records": records,
        "empty_message": (
            "There are no hours logged for this period"
            if report["view_mode"] == "pay_period"
            else "No records found for this period."
        ),
        "pay_period_date_value": report["pay_period_date_value"],
    }


def _split_employee_name(name: str) -> Tuple[str, str]:
    parts = (name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _admin_guard(ajax_payload=None):
    if session.get("admin_authenticated"):
        return None
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        payload = ajax_payload or {"error": "Admin login required."}
        return jsonify(payload), 401
    return redirect(url_for("main.admin_login"))


def ensure_test_employee():
    """Guarantee that the hard-coded testing employee exists."""
    employee = Employee.query.filter_by(employee_code=TEST_EMPLOYEE_CODE).first()
    if not employee:
        employee = Employee(name=TEST_EMPLOYEE_NAME, employee_code=TEST_EMPLOYEE_CODE)
        db.session.add(employee)
        db.session.commit()
    return employee


@main_bp.route("/")
def home():
    return render_template("home.html")


@main_bp.route("/clock/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        code = (request.form.get("employee_code") or "").strip()
        employee = Employee.query.filter_by(employee_code=code).first()

        if code == TEST_EMPLOYEE_CODE and not employee:
            employee = ensure_test_employee()

        if employee:
            session["employee_id"] = employee.id
            return redirect(url_for("main.clock"))
        else:
            error = "Invalid employee code."

    return render_template("login.html", error=error)


@main_bp.route("/clock", methods=["GET", "POST"])
def clock():
    employee_id = session.get("employee_id")
    if not employee_id:
        return redirect(url_for("main.login"))

    employee = Employee.query.get(employee_id)
    if not employee:
        session.pop("employee_id", None)
        return redirect(url_for("main.login"))

    today = datetime.now().date()
    pay_period_start, pay_period_end = get_pay_period_bounds(today)
    period_start_dt = datetime.combine(pay_period_start, datetime.min.time())
    period_end_dt = datetime.combine(pay_period_end + timedelta(days=1), datetime.min.time())

    now = datetime.now()
    active_record = (
        TimeRecord.query.filter_by(employee_id=employee.id, clock_out=None)
        .order_by(TimeRecord.clock_in.desc())
        .first()
    )
    can_clock_in = active_record is None
    can_clock_out = active_record is not None

    error = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "in":
            if can_clock_in:
                new_record = TimeRecord(employee_id=employee.id, clock_in=now)
                db.session.add(new_record)
                db.session.commit()
                return redirect(url_for("main.clock"))
            else:
                error = "You already have an active shift."
        elif action == "out":
            if can_clock_out:
                active_record.clock_out = now
                db.session.commit()
                return redirect(url_for("main.clock"))
            else:
                error = "No active shift to clock out of."
        else:
            error = "Invalid action."

    records = (
        TimeRecord.query.filter(
            TimeRecord.employee_id == employee.id,
            TimeRecord.clock_in >= period_start_dt,
            TimeRecord.clock_in < period_end_dt
        )
        .order_by(TimeRecord.clock_in.desc())
        .all()
    )

    total_biweekly_hours = sum(
        ((record.clock_out - record.clock_in).total_seconds() / 3600)
        for record in records
        if record.clock_out
    )
    current_shift_hours = (
        (now - active_record.clock_in).total_seconds() / 3600 if active_record else 0
    )

    return render_template(
        "clock.html",
        employee=employee,
        records=records,
        pay_period_start=pay_period_start,
        pay_period_end=pay_period_end,
        can_clock_in=can_clock_in,
        can_clock_out=can_clock_out,
        current_shift_hours=current_shift_hours,
        total_biweekly_hours=total_biweekly_hours,
        active_record=active_record,
        error=error
    )


@main_bp.route("/admin", methods=["GET", "POST"])
def admin():
    guard = _admin_guard()
    if guard:
        return guard

    employees = Employee.query.all()
    report = _blank_admin_report()

    if request.method == "POST":
        report = _build_admin_report(request.form)

    return render_template(
        "admin.html",
        employees=employees,
        records=report["records"],
        total_hours=report["total_hours"],
        selected_employee=report["selected_employee"],
        view_mode=report["view_mode"],
        error=report["error"],
        show_results=report["show_results"],
        start_date_value=report["start_date_value"],
        end_date_value=report["end_date_value"],
        pay_period_date_value=report["pay_period_date_value"],
        custom_start=report["custom_start"],
        custom_end=report["custom_end"],
        pay_period_start=report["pay_period_start"],
        pay_period_end=report["pay_period_end"],
        active_nav="report"
    )


@main_bp.route("/admin/report", methods=["POST"])
def admin_report():
    guard = _admin_guard({"error": "Admin login required.", "show_results": False})
    if guard:
        return guard

    report = _build_admin_report(request.form)
    return jsonify(_serialize_admin_report(report))


@main_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_authenticated"):
        return redirect(url_for("main.admin"))

    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        if username == "admin" and password == "admin123":
            session["admin_authenticated"] = True
            return redirect(url_for("main.admin"))

        error = "Invalid admin credentials."

    return render_template("admin_login.html", error=error)


@main_bp.route("/admin/employee", methods=["POST"])
def admin_add_employee():
    guard = _admin_guard({"success": False, "message": "Admin login required."})
    if guard:
        return guard

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    employee_code = (request.form.get("employee_code") or "").strip()

    if not first_name or not last_name or not employee_code:
        payload = {
            "success": False,
            "message": "First name, last name, and employee code are required."
        }
        return jsonify(payload) if is_ajax else redirect(
            url_for("main.admin_add_employee_page", status="error", message=payload["message"])
        )

    if len(employee_code) != 4:
        payload = {
            "success": False,
            "message": "Employee code must be exactly 4 characters."
        }
        return jsonify(payload) if is_ajax else redirect(
            url_for("main.admin_add_employee_page", status="error", message=payload["message"])
        )

    existing_employee = Employee.query.filter_by(employee_code=employee_code).first()
    if existing_employee:
        payload = {
            "success": False,
            "message": "That employee code is already in use."
        }
        return jsonify(payload) if is_ajax else redirect(
            url_for("main.admin_add_employee_page", status="error", message=payload["message"])
        )

    full_name = f"{first_name} {last_name}"
    new_employee = Employee(name=full_name, employee_code=employee_code)
    db.session.add(new_employee)
    db.session.commit()

    payload = {
        "success": True,
        "message": "Employee added.",
        "employee": {
            "id": new_employee.id,
            "name": new_employee.name,
            "code": new_employee.employee_code
        }
    }
    return jsonify(payload) if is_ajax else redirect(
        url_for("main.admin_add_employee_page", status="success", message=payload["message"])
    )


@main_bp.route("/admin/add-employee", methods=["GET"])
def admin_add_employee_page():
    guard = _admin_guard()
    if guard:
        return guard

    status_message = request.args.get("message")
    status_type = request.args.get("status")
    return render_template(
        "admin_add_employee.html",
        status_message=status_message,
        status_type=status_type,
        active_nav="add"
    )


@main_bp.route("/admin/manage-employees", methods=["GET"])
def admin_manage_employees():
    guard = _admin_guard()
    if guard:
        return guard

    status_message = request.args.get("message")
    status_type = request.args.get("status")
    employees = Employee.query.order_by(Employee.name).all()
    employee_rows = []
    for employee in employees:
        first_name, last_name = _split_employee_name(employee.name)
        employee_rows.append({
            "id": employee.id,
            "first_name": first_name,
            "last_name": last_name,
            "employee_code": employee.employee_code
        })

    return render_template(
        "admin_manage_employees.html",
        employees=employee_rows,
        status_message=status_message,
        status_type=status_type,
        active_nav="manage"
    )


@main_bp.route("/admin/employees/update", methods=["POST"])
def admin_update_employee():
    guard = _admin_guard()
    if guard:
        return guard

    employee_id = request.form.get("employee_id")
    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    employee_code = (request.form.get("employee_code") or "").strip()

    if not employee_id:
        return redirect(url_for("main.admin_manage_employees", status="error", message="Employee not found."))

    employee = Employee.query.get(employee_id)
    if not employee:
        return redirect(url_for("main.admin_manage_employees", status="error", message="Employee not found."))

    if not first_name or not last_name or not employee_code:
        return redirect(url_for(
            "main.admin_manage_employees",
            status="error",
            message="First name, last name, and employee code are required."
        ))

    if len(employee_code) != 4:
        return redirect(url_for(
            "main.admin_manage_employees",
            status="error",
            message="Employee code must be exactly 4 characters."
        ))

    existing_employee = Employee.query.filter(
        Employee.employee_code == employee_code,
        Employee.id != employee.id
    ).first()
    if existing_employee:
        return redirect(url_for(
            "main.admin_manage_employees",
            status="error",
            message="That employee code is already in use."
        ))

    employee.name = " ".join(part for part in [first_name, last_name] if part)
    employee.employee_code = employee_code
    db.session.commit()
    return redirect(url_for("main.admin_manage_employees", status="success", message="Employee updated."))


@main_bp.route("/admin/employees/delete", methods=["POST"])
def admin_delete_employee():
    guard = _admin_guard()
    if guard:
        return guard

    employee_id = request.form.get("employee_id")
    if not employee_id:
        return redirect(url_for("main.admin_manage_employees", status="error", message="Employee not found."))

    employee = Employee.query.get(employee_id)
    if not employee:
        return redirect(url_for("main.admin_manage_employees", status="error", message="Employee not found."))

    has_records = TimeRecord.query.filter_by(employee_id=employee.id).first()
    if has_records:
        return redirect(url_for(
            "main.admin_manage_employees",
            status="error",
            message="Cannot remove an employee with time records."
        ))

    db.session.delete(employee)
    db.session.commit()
    return redirect(url_for("main.admin_manage_employees", status="success", message="Employee removed."))


@main_bp.route("/logout")
def logout():
    session.pop("employee_id", None)
    session.pop("admin_authenticated", None)
    return redirect(url_for("main.home"))
