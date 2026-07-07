"""
cache_manager.py — PostgreSQL Cache Layer

SOLID Principles Applied:
  S → CacheManager has ONE job: read and write the analysis cache
      Nothing else. No business logic.
  D → DATABASE_URL injected, not hardcoded
      Pool created lazily — no connection at import time
"""

import os
import json
import hashlib
from typing import Optional
import psycopg2
import psycopg2.pool


class CacheManager:
    """
    Manages PostgreSQL-backed caching of analysis results.

    Cache key → SHA-256 hash of the submitted code
    Cache value → full analysis result (static findings + LLM result)

    Why SHA-256?
      → Same code always produces same 64-character hash
      → Collision probability is negligible
      → Fast to compute

    Why PostgreSQL and not Redis?
      → Already using PostgreSQL for this project
      → JSONB columns store structured findings efficiently
      → UNIQUE constraint on code_hash prevents duplicates
      → ON CONFLICT DO NOTHING handles race conditions (idempotent)
    """

    def __init__(self, database_url: str):
        self._database_url = database_url
        self._pool: Optional[psycopg2.pool.SimpleConnectionPool] = None

    def _get_pool(self) -> psycopg2.pool.SimpleConnectionPool:
        """
        Lazy initialization — pool created on first use.
        This prevents connection errors at import time
        when DATABASE_URL might not be loaded yet.
        """
        if self._pool is None:
            self._pool = psycopg2.pool.SimpleConnectionPool(
                minconn=1,
                maxconn=10,
                dsn=self._database_url
            )
        return self._pool

    @staticmethod
    def hash_code(code: str) -> str:
        """SHA-256 hash of code string — always 64 characters."""
        return hashlib.sha256(code.encode()).hexdigest()

    def get(self, code: str) -> Optional[dict]:
        """
        Check if this exact code has been analyzed before.
        Returns cached result dict or None on cache miss.
        """
        code_hash = self.hash_code(code)
        conn = self._get_pool().getconn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT static_findings, llm_result,
                          total_issues, llm_available
                   FROM analyses
                   WHERE code_hash = %s""",
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
            print(f"[CACHE] get error: {e}")
            return None
        finally:
            self._get_pool().putconn(conn)

    def save(self, code: str, result: dict) -> None:
        """
        Save analysis result to database.
        ON CONFLICT DO NOTHING = idempotent write.
        Two simultaneous requests for same code
        won't cause duplicate rows or crashes.
        """
        code_hash = self.hash_code(code)
        conn = self._get_pool().getconn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO analyses
                   (code_hash, code_text, static_findings,
                    llm_result, total_issues, llm_available)
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
            print(f"[CACHE] Saved — hash: {code_hash[:8]}...")

        except Exception as e:
            conn.rollback()
            print(f"[CACHE] save error: {e}")
        finally:
            self._get_pool().putconn(conn)
