from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import os

db = SQLAlchemy()

def create_app():
    load_dotenv()  # load environment variables from .env

    app = Flask(__name__)
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')  # must be set!
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change-this-secret')

    db.init_app(app)

    # Import and register routes
    from app.routes import main_bp
    app.register_blueprint(main_bp)

    return app
