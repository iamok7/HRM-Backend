# migrations/env.py  — DROP-IN REPLACEMENT

from __future__ import annotations
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool

# ---- Load Flask app + db ----
# IMPORTANT: tweak import path only if your wsgi is elsewhere
from hrms_api.wsgi import app as flask_app
from hrms_api.extensions import db

# Alembic Config object
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    try:
        # Some alembic.ini in dev may not define full logging sections.
        # Be forgiving and skip logging config if it's incomplete.
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    except Exception:
        pass

# Use DB URL from Flask config
with flask_app.app_context():
    db_uri = flask_app.config["SQLALCHEMY_DATABASE_URI"]

# overwrite sqlalchemy.url from alembic.ini, always source from Flask
config.set_main_option("sqlalchemy.url", db_uri)

# Target metadata for 'autogenerate'
target_metadata = db.metadata

# Optional: if you need batch mode for SQLite
render_as_batch = False

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=render_as_batch,
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode' WITH Flask app context."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=render_as_batch,
        )

        with flask_app.app_context():  # <-- key bit
            with context.begin_transaction():
                context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
