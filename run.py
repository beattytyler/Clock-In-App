import os
import sys

if __package__ is None:
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app import create_app, db

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # creates tables if they don't exist
    app.run(debug=True)
