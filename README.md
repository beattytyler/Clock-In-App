# Clock-In-App

Simple Flask-based clock-in system with admin reporting, hours adjustments, bonuses, and export output.

## Requirements
- Python 3.9+

## Setup
1) Create and activate a virtual environment.
2) Install dependencies:
   - `pip install -r requirements.txt`

## Configuration
Create a `.env` file (already included in this repo) with:
- `DATABASE_URL` (example: `sqlite:///clock.db`)
- `SECRET_KEY` (optional)

## Run
- `python app.py`

This will create any missing tables and start the Flask dev server.

## Admin Access
- Admin login: `admin` / `admin123`
- Employee login: uses employee codes created by admin

## Features
- Clock in/out and pay-period summaries
- Admin report by custom range or pay period
- Manage employees
- Hours & bonuses:
  - Total hours view (editable)
  - Shift view per employee
  - Round up/down buttons (0.5 hour increments)
- Export hours:
  - Format: `First L, 12.5, Bonus, $100`
  - Omits bonus section when the bonus is 0
  - Managers append `(Salary)`

## Notes
- Database uses SQLite by default.
- The app performs a lightweight schema check at startup to add the
  `is_manager` column if needed.
