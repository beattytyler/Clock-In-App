from flask import Flask
from dotenv import load_dotenv
from sqlalchemy import inspect, text
import os

from extensions import db


def create_app():
    load_dotenv()  # load environment variables from .env

    app = Flask(__name__)
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')  # must be set!
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change-this-secret')

    db.init_app(app)

    # Import and register routes
    from routes import main_bp
    app.register_blueprint(main_bp)

    return app


def _ensure_schema():
    inspector = inspect(db.engine)
    if "employee" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("employee")}
    if "is_manager" not in columns:
        db.session.execute(
            text("ALTER TABLE employee ADD COLUMN is_manager BOOLEAN NOT NULL DEFAULT 0")
        )
        db.session.commit()


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        db.create_all()  # creates tables if they don't exist
        _ensure_schema()
    app.run(debug=True)
