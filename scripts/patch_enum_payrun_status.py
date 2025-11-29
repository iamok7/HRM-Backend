import os
import psycopg


def main():
    dsn = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:4445@127.0.0.1:5432/hrms_dev",
    )
    dsn = dsn.replace("postgresql+psycopg2", "postgresql").replace(
        "postgresql+psycopg", "postgresql"
    )
    print("Using DSN:", dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid = e.enumtypid
                        WHERE t.typname = 'payrun_status_enum' AND e.enumlabel = 'calculated'
                    ) THEN
                        ALTER TYPE payrun_status_enum ADD VALUE 'calculated';
                    END IF;
                END $$;
                """
            )
            cur.execute(
                """
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid = e.enumtypid
                        WHERE t.typname = 'payrun_status_enum' AND e.enumlabel = 'approved'
                    ) THEN
                        ALTER TYPE payrun_status_enum ADD VALUE 'approved';
                    END IF;
                END $$;
                """
            )
    print("Enum patched successfully.")


if __name__ == "__main__":
    main()

