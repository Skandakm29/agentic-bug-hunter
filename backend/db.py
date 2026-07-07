import os
import json
import hashlib
import psycopg2
import psycopg2.pool
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")

# Lazy pool — created on first use, not at import time
_pool = None

def get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DATABASE_URL
        )
    return _pool

def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()

def get_cached_analysis(code: str):
    code_hash = hash_code(code)
    conn = get_pool().getconn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT static_findings, llm_result, total_issues, llm_available FROM analyses WHERE code_hash = %s",
            (code_hash,)
        )
        row = cursor.fetchone()
        cursor.close()
        if row:
            return {
                "static_findings": row[0] or [],
                "llm_result":      row[1],
                "total_issues":    row[2],
                "llm_available":   row[3],
                "cached":          True
            }
        return None
    except Exception as e:
        print(f"[DB] cache check error: {e}")
        return None
    finally:
        get_pool().putconn(conn)

def save_analysis(code: str, result: dict):
    code_hash = hash_code(code)
    conn = get_pool().getconn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO analyses
               (code_hash, code_text, static_findings, llm_result,
                total_issues, llm_available)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (code_hash) DO NOTHING""",
            (
                code_hash,
                code,
                json.dumps(result.get("static_findings", [])),
                json.dumps(result.get("llm_result")),
                result.get("total_issues", 0),
                result.get("llm_available", False)
            )
        )
        conn.commit()
        cursor.close()
    except Exception as e:
        conn.rollback()
        print(f"[DB] save error: {e}")
    finally:
        get_pool().putconn(conn)