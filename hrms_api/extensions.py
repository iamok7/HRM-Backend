# hrms_api/extensions.py
import os
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

db = SQLAlchemy()
migrate = Migrate()

def normalize_db_url(url: str) -> str:
    if not url:
        return url
    # Render / Heroku style → SQLAlchemy psycopg3 driver
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

def init_db(app):
    url = os.getenv("DATABASE_URL", app.config.get("SQLALCHEMY_DATABASE_URI", "")) or ""
    app.config["SQLALCHEMY_DATABASE_URI"] = normalize_db_url(url)

    # pool/ssl health — Render free tier friendly
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 270,
        "pool_size": 5,
        "max_overflow": 2,
        "pool_timeout": 30,
    }

    db.init_app(app)
