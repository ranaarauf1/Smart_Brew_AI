"""SmartBrew AI application package: shared extensions and the app factory."""

import os
from pathlib import Path

from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "smartbrew.db"

db = SQLAlchemy()


def format_euro(cents):
    """
    Purpose: Formats an amount in cents as a euro price for templates.

    Args:
        cents (int): Amount in euro cents, e.g. 330.

    Returns:
        str: Formatted price, e.g. "€3.30".
    """
    return f"€{cents / 100:.2f}"


def page_not_found(error):
    """
    Purpose: Shows a touchscreen-style page for unknown URLs, drinks or
        categories.

    Args:
        error (NotFound): The 404 error raised by Flask.

    Returns:
        tuple[str, int]: The rendered 404 page and status code 404.
    """
    return render_template("404.html"), 404


def create_app(config_overrides=None):
    """
    Purpose: Creates and configures the SmartBrew Flask application.

    Args:
        config_overrides (dict | None): Config values that replace the
            defaults, e.g. an in-memory database for tests.

    Returns:
        Flask: The configured application with all blueprints registered.
    """
    app = Flask(__name__)

    DATA_DIR.mkdir(exist_ok=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        # as_posix() keeps the SQLite URI valid on Windows paths too
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{DB_PATH.as_posix()}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        OUTBOX_DIR=str(DATA_DIR / "outbox"),
        SESSION_TIMEOUT_SECONDS=60,  # NFR-14: log out after 60 s inactivity
        BCRYPT_ROUNDS=12,
        # Session cookie not readable by JS and not sent on cross-site POSTs
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if config_overrides:
        app.config.update(config_overrides)

    db.init_app(app)

    from app.routes.auth import auth_bp
    from app.routes.customer import customer_bp
    from app.routes.simulation import simulation_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(simulation_bp)

    app.add_template_filter(format_euro, "euro")
    app.register_error_handler(404, page_not_found)

    with app.app_context():
        from app import models  # noqa: F401  (registers tables before create_all)

        db.create_all()

    return app
