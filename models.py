from . import db
from datetime import datetime

class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    employee_code = db.Column(db.String(4), unique=True, nullable=False)
    time_records = db.relationship('TimeRecord', backref='employee', lazy=True)

class TimeRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    clock_in = db.Column(db.DateTime, default=datetime.utcnow)
    clock_out = db.Column(db.DateTime)
