from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from datetime import datetime, timedelta, date
import math
from typing import Tuple
from models import db, Employee, TimeRecord, EmployeeBonus, EmployeeHoursAdjustment

main_bp = Blueprint('main', __name__)

PAY_PERIOD_LENGTH_DAYS = 14
# Anchor bi-weekly periods so 12/22/2025-01/04/2026 is the first window and
# 01/05/2026-01/18/2026 is the next.
REFERENCE_PAY_PERIOD_START = date(2025, 12, 22)
TEST_EMPLOYEE_CODE = "0430"
TEST_EMPLOYEE_NAME = "Test Employee"
ROUNDING_INCREMENT_HOURS = 0.5


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


def _format_export_name(name: str) -> str:
    first_name, last_name = _split_employee_name(name)
    if not first_name:
        return (name or "").strip()
    last_initial = ""
    if last_name:
        last_initial = last_name.strip().split()[-1][:1]
    if last_initial:
        return f"{first_name} {last_initial}"
    return first_name


def _admin_guard(ajax_payload=None):
    if session.get("admin_authenticated"):
        return None
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        payload = ajax_payload or {"error": "Admin login required."}
        return jsonify(payload), 401
    return redirect(url_for("main.admin_login"))


def _default_pay_period_range():
    today = datetime.now().date()
    start, end = get_pay_period_bounds(today)
    return start, end


def _parse_date_value(value: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _resolve_date_range(start_value: str, end_value: str):
    error = None
    start_value = (start_value or "").strip()
    end_value = (end_value or "").strip()

    if start_value and end_value:
        start_date = _parse_date_value(start_value)
        end_date = _parse_date_value(end_value)
        if not start_date or not end_date:
            error = "Enter valid start and end dates."
            start_date, end_date = _default_pay_period_range()
        elif end_date < start_date:
            error = "End date must be on or after start date."
            start_date, end_date = _default_pay_period_range()
    else:
        start_date, end_date = _default_pay_period_range()

    start_value = start_date.strftime("%Y-%m-%d")
    end_value = end_date.strftime("%Y-%m-%d")
    return start_date, end_date, start_value, end_value, error


def _parse_datetime_local(value: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _hours_return_params(form):
    params = {
        "view_mode": (form.get("view_mode") or "total").strip() or "total",
        "employee_id": (form.get("employee_id") or "").strip(),
        "start_date": (form.get("start_date") or "").strip(),
        "end_date": (form.get("end_date") or "").strip(),
    }
    if params["view_mode"] != "shift":
        params.pop("employee_id", None)
    return {key: value for key, value in params.items() if value}


def _get_bonus_for_period(employee_id, start_date, end_date):
    if not employee_id:
        return None
    return EmployeeBonus.query.filter_by(
        employee_id=employee_id,
        period_start=start_date,
        period_end=end_date
    ).first()


def _get_hours_adjustment_for_period(employee_id, start_date, end_date):
    if not employee_id:
        return None
    return EmployeeHoursAdjustment.query.filter_by(
        employee_id=employee_id,
        period_start=start_date,
        period_end=end_date
    ).first()


def _round_hours(hours, direction):
    increment = ROUNDING_INCREMENT_HOURS
    if increment <= 0:
        return round(hours, 2)

    units = hours / increment
    if direction == "up":
        rounded = math.ceil(units) * increment
    else:
        rounded = math.floor(units) * increment
    return round(rounded, 2)


def _round_hours_nearest(hours):
    increment = ROUNDING_INCREMENT_HOURS
    if increment <= 0:
        return round(hours, 2)
    units = hours / increment
    rounded_units = math.floor(units + 0.5)
    return round(rounded_units * increment, 2)


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
    is_manager = (request.form.get("is_manager") or "").strip() == "1"

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
    new_employee = Employee(
        name=full_name,
        employee_code=employee_code,
        is_manager=is_manager
    )
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


@main_bp.route("/admin/hours-bonuses", methods=["GET"])
def admin_hours_bonuses():
    guard = _admin_guard()
    if guard:
        return guard

    employees = Employee.query.order_by(Employee.is_manager, Employee.name).all()
    view_mode = (request.args.get("view_mode") or "total").strip()
    if view_mode not in ("shift", "total"):
        view_mode = "total"

    employee_id = (request.args.get("employee_id") or "").strip()
    start_value = request.args.get("start_date")
    end_value = request.args.get("end_date")
    start_date, end_date, start_value, end_value, range_error = _resolve_date_range(start_value, end_value)
    period_label = f"{start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')}"

    status_message = request.args.get("message")
    status_type = request.args.get("status")
    if range_error and not status_message:
        status_message = range_error
        status_type = "error"

    data = {
        "view_mode": view_mode,
        "employees": employees,
        "selected_employee": None,
        "records": [],
        "total_hours": 0,
        "total_rows": [],
        "overall_hours": 0,
        "bonus_amount": "",
        "period_label": period_label,
        "start_date_value": start_value,
        "end_date_value": end_value,
        "status_message": status_message,
        "status_type": status_type,
        "error": None,
        "rounding_increment": ROUNDING_INCREMENT_HOURS,
    }

    range_start = datetime.combine(start_date, datetime.min.time())
    range_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    if view_mode == "shift":
        if employee_id:
            data["selected_employee"] = Employee.query.get(employee_id)
        if not data["selected_employee"]:
            data["error"] = "Select an employee to view shift details."
        else:
            data["records"] = (
                TimeRecord.query.filter(
                    TimeRecord.employee_id == data["selected_employee"].id,
                    TimeRecord.clock_in >= range_start,
                    TimeRecord.clock_in < range_end
                )
                .order_by(TimeRecord.clock_in.desc())
                .all()
            )
            data["total_hours"] = sum(
                ((record.clock_out - record.clock_in).total_seconds() / 3600)
                for record in data["records"] if record.clock_out
            )
            bonus = _get_bonus_for_period(data["selected_employee"].id, start_date, end_date)
            data["bonus_amount"] = f"{bonus.amount:.2f}" if bonus else ""
    else:
        totals = {}
        for record in TimeRecord.query.filter(
            TimeRecord.clock_in >= range_start,
            TimeRecord.clock_in < range_end
        ).all():
            if record.clock_out:
                totals[record.employee_id] = totals.get(record.employee_id, 0) + (
                    (record.clock_out - record.clock_in).total_seconds() / 3600
                )

        bonuses = EmployeeBonus.query.filter_by(
            period_start=start_date,
            period_end=end_date
        ).all()
        bonus_map = {bonus.employee_id: bonus for bonus in bonuses}

        adjustments = EmployeeHoursAdjustment.query.filter_by(
            period_start=start_date,
            period_end=end_date
        ).all()
        adjustment_map = {adjustment.employee_id: adjustment for adjustment in adjustments}

        for employee in employees:
            actual_hours = totals.get(employee.id, 0)
            adjustment = adjustment_map.get(employee.id)
            adjusted_hours = adjustment.adjusted_hours if adjustment else actual_hours
            bonus = bonus_map.get(employee.id)
            data["total_rows"].append({
                "employee": employee,
                "actual_hours": actual_hours,
                "adjusted_hours": adjusted_hours,
                "adjusted_hours_value": f"{adjusted_hours:.2f}",
                "show_actual": adjustment is not None and abs(adjusted_hours - actual_hours) > 0.005,
                "bonus_amount": f"{bonus.amount:.2f}" if bonus else "",
            })
            data["overall_hours"] += adjusted_hours

    return render_template("admin_hours_bonuses.html", active_nav="hours", **data)


@main_bp.route("/admin/export-hours", methods=["GET"])
def admin_export_hours():
    guard = _admin_guard()
    if guard:
        return guard

    employees = Employee.query.order_by(Employee.is_manager, Employee.name).all()
    start_value = request.args.get("start_date")
    end_value = request.args.get("end_date")
    email = (request.args.get("email") or "").strip()

    start_date, end_date, start_value, end_value, range_error = _resolve_date_range(start_value, end_value)
    period_label = f"{start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')}"

    status_message = request.args.get("message")
    status_type = request.args.get("status")
    if range_error and not status_message:
        status_message = range_error
        status_type = "error"

    range_start = datetime.combine(start_date, datetime.min.time())
    range_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    totals = {}
    for record in TimeRecord.query.filter(
        TimeRecord.clock_in >= range_start,
        TimeRecord.clock_in < range_end
    ).all():
        if record.clock_out:
            totals[record.employee_id] = totals.get(record.employee_id, 0) + (
                (record.clock_out - record.clock_in).total_seconds() / 3600
            )

    adjustments = EmployeeHoursAdjustment.query.filter_by(
        period_start=start_date,
        period_end=end_date
    ).all()
    adjustment_map = {adjustment.employee_id: adjustment for adjustment in adjustments}

    bonuses = EmployeeBonus.query.filter_by(
        period_start=start_date,
        period_end=end_date
    ).all()
    bonus_map = {bonus.employee_id: bonus for bonus in bonuses}

    export_rows = []
    export_lines = []
    for employee in employees:
        actual_hours = totals.get(employee.id, 0)
        adjustment = adjustment_map.get(employee.id)
        hours_base = adjustment.adjusted_hours if adjustment else actual_hours
        rounded_hours = _round_hours_nearest(hours_base)
        bonus = bonus_map.get(employee.id)
        bonus_amount = bonus.amount if bonus else 0

        display_name = _format_export_name(employee.name)
        salary_suffix = " (Salary)" if employee.is_manager else ""
        bonus_text = f", Bonus, ${bonus_amount:.0f}" if bonus_amount > 0 else ""
        line = f"{display_name}, {rounded_hours:.1f}{bonus_text}{salary_suffix}"
        export_rows.append({
            "employee": employee,
            "display_name": display_name,
            "rounded_hours": rounded_hours,
            "bonus_amount": bonus_amount,
            "line": line,
        })
        export_lines.append(line)

    export_body = "\n".join(export_lines)

    return render_template(
        "admin_export_hours.html",
        active_nav="export",
        employees=employees,
        export_rows=export_rows,
        export_body=export_body,
        email=email,
        period_label=period_label,
        start_date_value=start_value,
        end_date_value=end_value,
        status_message=status_message,
        status_type=status_type
    )


@main_bp.route("/admin/hours-bonuses/shift", methods=["POST"])
def admin_update_shift():
    guard = _admin_guard()
    if guard:
        return guard

    params = _hours_return_params(request.form)
    record_id = request.form.get("record_id")
    record = TimeRecord.query.get(record_id) if record_id else None
    if not record:
        return redirect(url_for(
            "main.admin_hours_bonuses",
            status="error",
            message="Shift not found.",
            **params
        ))

    clock_in_value = (request.form.get("clock_in") or "").strip()
    clock_out_value = (request.form.get("clock_out") or "").strip()
    clock_in = _parse_datetime_local(clock_in_value)
    clock_out = _parse_datetime_local(clock_out_value) if clock_out_value else None

    if clock_in_value and not clock_in:
        return redirect(url_for(
            "main.admin_hours_bonuses",
            status="error",
            message="Enter a valid clock-in time.",
            **params
        ))
    if clock_out_value and not clock_out:
        return redirect(url_for(
            "main.admin_hours_bonuses",
            status="error",
            message="Enter a valid clock-out time.",
            **params
        ))
    if not clock_in:
        return redirect(url_for(
            "main.admin_hours_bonuses",
            status="error",
            message="Clock-in time is required.",
            **params
        ))
    if clock_out and clock_out < clock_in:
        return redirect(url_for(
            "main.admin_hours_bonuses",
            status="error",
            message="Clock-out must be after clock-in.",
            **params
        ))

    record.clock_in = clock_in
    record.clock_out = clock_out
    db.session.commit()

    return redirect(url_for(
        "main.admin_hours_bonuses",
        status="success",
        message="Shift updated.",
        **params
    ))


@main_bp.route("/admin/hours-bonuses/bonus", methods=["POST"])
def admin_update_bonus():
    guard = _admin_guard()
    if guard:
        return guard

    params = _hours_return_params(request.form)
    employee_id = (request.form.get("employee_id") or "").strip()
    employee = Employee.query.get(employee_id) if employee_id else None
    if not employee:
        return redirect(url_for(
            "main.admin_hours_bonuses",
            status="error",
            message="Employee not found.",
            **params
        ))

    start_date = _parse_date_value(request.form.get("start_date") or "")
    end_date = _parse_date_value(request.form.get("end_date") or "")
    if not start_date or not end_date:
        start_date, end_date = _default_pay_period_range()

    amount_value = (request.form.get("bonus_amount") or "").strip()
    if amount_value:
        try:
            amount = float(amount_value)
        except ValueError:
            return redirect(url_for(
                "main.admin_hours_bonuses",
                status="error",
                message="Bonus amount must be a number.",
                **params
            ))
        if amount < 0:
            return redirect(url_for(
                "main.admin_hours_bonuses",
                status="error",
                message="Bonus amount cannot be negative.",
                **params
            ))

        bonus = _get_bonus_for_period(employee.id, start_date, end_date)
        if not bonus:
            bonus = EmployeeBonus(
                employee_id=employee.id,
                period_start=start_date,
                period_end=end_date
            )
        bonus.amount = amount
        db.session.add(bonus)
        db.session.commit()
        message = "Bonus saved."
    else:
        bonus = _get_bonus_for_period(employee.id, start_date, end_date)
        if bonus:
            db.session.delete(bonus)
            db.session.commit()
        message = "Bonus cleared."

    return redirect(url_for(
        "main.admin_hours_bonuses",
        status="success",
        message=message,
        **params
    ))


@main_bp.route("/admin/hours-bonuses/round", methods=["POST"])
def admin_round_hours():
    guard = _admin_guard()
    if guard:
        return guard

    params = _hours_return_params(request.form)
    employee_id = (request.form.get("employee_id") or "").strip()
    employee = Employee.query.get(employee_id) if employee_id else None
    if not employee:
        return redirect(url_for(
            "main.admin_hours_bonuses",
            status="error",
            message="Employee not found.",
            **params
        ))

    direction = (request.form.get("direction") or "").strip().lower()
    if direction not in ("up", "down"):
        return redirect(url_for(
            "main.admin_hours_bonuses",
            status="error",
            message="Select a valid rounding option.",
            **params
        ))

    start_date = _parse_date_value(request.form.get("start_date") or "")
    end_date = _parse_date_value(request.form.get("end_date") or "")
    if not start_date or not end_date:
        start_date, end_date = _default_pay_period_range()

    range_start = datetime.combine(start_date, datetime.min.time())
    range_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    total_hours = sum(
        ((record.clock_out - record.clock_in).total_seconds() / 3600)
        for record in TimeRecord.query.filter(
            TimeRecord.employee_id == employee.id,
            TimeRecord.clock_in >= range_start,
            TimeRecord.clock_in < range_end
        ).all()
        if record.clock_out
    )

    rounded_hours = _round_hours(total_hours, direction)
    adjustment = _get_hours_adjustment_for_period(employee.id, start_date, end_date)
    if not adjustment:
        adjustment = EmployeeHoursAdjustment(
            employee_id=employee.id,
            period_start=start_date,
            period_end=end_date
        )
    adjustment.adjusted_hours = rounded_hours
    db.session.add(adjustment)
    db.session.commit()

    message = "Hours rounded up." if direction == "up" else "Hours rounded down."
    return redirect(url_for(
        "main.admin_hours_bonuses",
        status="success",
        message=message,
        **params
    ))


@main_bp.route("/admin/hours-bonuses/adjust", methods=["POST"])
def admin_adjust_hours():
    guard = _admin_guard()
    if guard:
        return guard

    params = _hours_return_params(request.form)
    employee_id = (request.form.get("employee_id") or "").strip()
    employee = Employee.query.get(employee_id) if employee_id else None
    if not employee:
        return redirect(url_for(
            "main.admin_hours_bonuses",
            status="error",
            message="Employee not found.",
            **params
        ))

    start_date = _parse_date_value(request.form.get("start_date") or "")
    end_date = _parse_date_value(request.form.get("end_date") or "")
    if not start_date or not end_date:
        start_date, end_date = _default_pay_period_range()

    adjusted_value = (request.form.get("adjusted_hours") or "").strip()
    if adjusted_value:
        try:
            adjusted_hours = float(adjusted_value)
        except ValueError:
            return redirect(url_for(
                "main.admin_hours_bonuses",
                status="error",
                message="Hours must be a number.",
                **params
            ))
        if adjusted_hours < 0:
            return redirect(url_for(
                "main.admin_hours_bonuses",
                status="error",
                message="Hours cannot be negative.",
                **params
            ))

        adjustment = _get_hours_adjustment_for_period(employee.id, start_date, end_date)
        if not adjustment:
            adjustment = EmployeeHoursAdjustment(
                employee_id=employee.id,
                period_start=start_date,
                period_end=end_date
            )
        adjustment.adjusted_hours = adjusted_hours
        db.session.add(adjustment)
        db.session.commit()
        message = "Hours updated."
    else:
        adjustment = _get_hours_adjustment_for_period(employee.id, start_date, end_date)
        if adjustment:
            db.session.delete(adjustment)
            db.session.commit()
        message = "Hours reset to actual."

    return redirect(url_for(
        "main.admin_hours_bonuses",
        status="success",
        message=message,
        **params
    ))


@main_bp.route("/admin/hours-bonuses/adjust-bulk", methods=["POST"])
def admin_adjust_hours_bulk():
    guard = _admin_guard()
    if guard:
        return guard

    params = _hours_return_params(request.form)
    start_date = _parse_date_value(request.form.get("start_date") or "")
    end_date = _parse_date_value(request.form.get("end_date") or "")
    if not start_date or not end_date:
        start_date, end_date = _default_pay_period_range()

    employee_ids = request.form.getlist("employee_id")
    dirty_hours_ids = [
        emp_id for emp_id in employee_ids
        if (request.form.get(f"hours_dirty_{emp_id}") or "0") == "1"
    ]
    dirty_bonus_ids = [
        emp_id for emp_id in employee_ids
        if (request.form.get(f"bonus_dirty_{emp_id}") or "0") == "1"
    ]
    dirty_ids = list({*dirty_hours_ids, *dirty_bonus_ids})

    if not dirty_ids:
        return redirect(url_for(
            "main.admin_hours_bonuses",
            status="success",
            message="No changes to save.",
            **params
        ))

    employees = Employee.query.filter(Employee.id.in_(dirty_ids)).all()
    employee_map = {str(employee.id): employee for employee in employees}

    for emp_id in dirty_ids:
        employee = employee_map.get(str(emp_id))
        if not employee:
            return redirect(url_for(
                "main.admin_hours_bonuses",
                status="error",
                message="Employee not found.",
                **params
            ))

        if emp_id in dirty_hours_ids:
            value = (request.form.get(f"adjusted_hours_{emp_id}") or "").strip()
            if value:
                try:
                    adjusted_hours = float(value)
                except ValueError:
                    return redirect(url_for(
                        "main.admin_hours_bonuses",
                        status="error",
                        message=f"Hours must be a number for {employee.name}.",
                        **params
                    ))
                if adjusted_hours < 0:
                    return redirect(url_for(
                        "main.admin_hours_bonuses",
                        status="error",
                        message=f"Hours cannot be negative for {employee.name}.",
                        **params
                    ))
                adjustment = _get_hours_adjustment_for_period(employee.id, start_date, end_date)
                if not adjustment:
                    adjustment = EmployeeHoursAdjustment(
                        employee_id=employee.id,
                        period_start=start_date,
                        period_end=end_date
                    )
                adjustment.adjusted_hours = adjusted_hours
                db.session.add(adjustment)
            else:
                adjustment = _get_hours_adjustment_for_period(employee.id, start_date, end_date)
                if adjustment:
                    db.session.delete(adjustment)

        if emp_id in dirty_bonus_ids:
            value = (request.form.get(f"bonus_amount_{emp_id}") or "").strip()
            if value:
                try:
                    bonus_amount = float(value)
                except ValueError:
                    return redirect(url_for(
                        "main.admin_hours_bonuses",
                        status="error",
                        message=f"Bonus must be a number for {employee.name}.",
                        **params
                    ))
                if bonus_amount < 0:
                    return redirect(url_for(
                        "main.admin_hours_bonuses",
                        status="error",
                        message=f"Bonus cannot be negative for {employee.name}.",
                        **params
                    ))
                bonus = _get_bonus_for_period(employee.id, start_date, end_date)
                if not bonus:
                    bonus = EmployeeBonus(
                        employee_id=employee.id,
                        period_start=start_date,
                        period_end=end_date
                    )
                bonus.amount = bonus_amount
                db.session.add(bonus)
            else:
                bonus = _get_bonus_for_period(employee.id, start_date, end_date)
                if bonus:
                    db.session.delete(bonus)

    db.session.commit()

    return redirect(url_for(
        "main.admin_hours_bonuses",
        status="success",
        message="Updates saved.",
        **params
    ))


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
