"""
Lightweight SQLite persistence for extracted fields.

Optional — the pipeline works without this. Wire it in after DeltaCalculator
to retain results across runs and support cache-hit detection via document hash.

Schema (auto-created on first use):

    extraction_runs
        id           INTEGER PK
        document_id  TEXT
        source_name  TEXT
        doc_hash     TEXT       -- MD5 of PDF bytes; used for cache invalidation
        stage_used   TEXT
        extracted_at TEXT       -- ISO-8601

    extracted_fields
        id              INTEGER PK
        run_id          INTEGER FK → extraction_runs.id
        field_name      TEXT
        section         TEXT
        extracted_value TEXT    -- stored as string; parse with Decimal on read
        raw_value       TEXT
        confidence      TEXT
        source_doc_type TEXT
        source_line_ref TEXT
        reference_value TEXT
        delta           TEXT
        severity        TEXT
"""

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Generator

from ..models.extracted_field import ExtractedField


class SqliteExtractionStore:
    """
    Thin SQLite wrapper. Not thread-safe across processes; fine for single-process
    pipelines. For multi-process use, swap this out for Postgres via SQLAlchemy.
    """

    def __init__(self, db_path: str = "extractions.db") -> None:
        self.db_path = Path(db_path)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS extraction_runs (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id  TEXT NOT NULL,
                    source_name  TEXT NOT NULL,
                    doc_hash     TEXT NOT NULL,
                    stage_used   TEXT,
                    extracted_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS extracted_fields (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id          INTEGER NOT NULL REFERENCES extraction_runs(id),
                    field_name      TEXT NOT NULL,
                    section         TEXT NOT NULL,
                    extracted_value TEXT,
                    raw_value       TEXT,
                    confidence      TEXT,
                    source_doc_type TEXT,
                    source_line_ref TEXT,
                    reference_value TEXT,
                    delta           TEXT,
                    severity        TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_runs_doc_hash
                    ON extraction_runs(document_id, doc_hash);
            """)

    # ------------------------------------------------------------------
    # Cache check
    # ------------------------------------------------------------------

    def is_cached(self, document_id: str, pdf_bytes: bytes) -> bool:
        """Return True if we already have a run for this exact document content."""
        doc_hash = _md5(pdf_bytes)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM extraction_runs WHERE document_id = ? AND doc_hash = ? LIMIT 1",
                (document_id, doc_hash),
            ).fetchone()
        return row is not None

    def load_cached(self, document_id: str, pdf_bytes: bytes) -> list[ExtractedField] | None:
        """Load previously extracted fields if the document hash matches. Returns None on miss."""
        doc_hash = _md5(pdf_bytes)
        with self._conn() as conn:
            run = conn.execute(
                "SELECT id FROM extraction_runs WHERE document_id = ? AND doc_hash = ? "
                "ORDER BY id DESC LIMIT 1",
                (document_id, doc_hash),
            ).fetchone()
            if run is None:
                return None
            rows = conn.execute(
                "SELECT * FROM extracted_fields WHERE run_id = ?", (run["id"],)
            ).fetchall()
        return [_row_to_field(r) for r in rows]

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------

    def save(
        self,
        document_id: str,
        source_name: str,
        pdf_bytes: bytes,
        stage_used: str,
        fields: list[ExtractedField],
    ) -> int:
        """Persist an extraction run and its fields. Returns the run id."""
        doc_hash = _md5(pdf_bytes)
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO extraction_runs (document_id, source_name, doc_hash, stage_used, extracted_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (document_id, source_name, doc_hash, stage_used, now),
            )
            run_id = cur.lastrowid
            conn.executemany(
                """
                INSERT INTO extracted_fields
                    (run_id, field_name, section, extracted_value, raw_value,
                     confidence, source_doc_type, source_line_ref,
                     reference_value, delta, severity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        f.field_name,
                        f.section,
                        str(f.extracted_value) if f.extracted_value is not None else None,
                        f.raw_value,
                        str(f.confidence),
                        f.source_doc_type,
                        f.source_line_ref,
                        str(f.reference_value) if f.reference_value is not None else None,
                        str(f.delta) if f.delta is not None else None,
                        f.severity.value if f.severity is not None else None,
                    )
                    for f in fields
                ],
            )
        return run_id


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _row_to_field(row: sqlite3.Row) -> ExtractedField:
    from ..models.extracted_field import Severity

    def _d(val: str | None) -> Decimal | None:
        return Decimal(val) if val is not None else None

    return ExtractedField(
        field_name=row["field_name"],
        extracted_value=_d(row["extracted_value"]),
        raw_value=row["raw_value"] or "",
        confidence=Decimal(row["confidence"]) if row["confidence"] else Decimal("0"),
        source_doc_type=row["source_doc_type"] or "",
        source_line_ref=row["source_line_ref"],
        section=row["section"],
        reference_value=_d(row["reference_value"]),
        delta=_d(row["delta"]),
        severity=Severity(row["severity"]) if row["severity"] else None,
    )
