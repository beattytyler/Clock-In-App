from extensions import db
from datetime import datetime

class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    employee_code = db.Column(db.String(4), unique=True, nullable=False)
    is_manager = db.Column(db.Boolean, nullable=False, default=False)
    time_records = db.relationship('TimeRecord', backref='employee', lazy=True)
    bonuses = db.relationship('EmployeeBonus', backref='employee', lazy=True)
    hours_adjustments = db.relationship('EmployeeHoursAdjustment', backref='employee', lazy=True)

class TimeRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    clock_in = db.Column(db.DateTime, default=datetime.utcnow)
    clock_out = db.Column(db.DateTime)


class EmployeeBonus(db.Model):
    __table_args__ = (
        db.UniqueConstraint(
            "employee_id",
            "period_start",
            "period_end",
            name="uq_employee_bonus_period"
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Float, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class EmployeeHoursAdjustment(db.Model):
    __table_args__ = (
        db.UniqueConstraint(
            "employee_id",
            "period_start",
            "period_end",
            name="uq_employee_hours_period"
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    adjusted_hours = db.Column(db.Float, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
