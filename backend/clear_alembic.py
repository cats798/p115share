import sqlite3
import os

db_path = "data/p115share.db"

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM alembic_version")
        conn.commit()
        print("alembic_version table cleared.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()
else:
    print("Database not found.")
