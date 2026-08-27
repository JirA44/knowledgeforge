from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SQLITE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    publisher TEXT,
    published_at TEXT,
    reliability REAL NOT NULL CHECK (reliability >= 0 AND reliability <= 1),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_versions (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE RESTRICT,
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    statement TEXT NOT NULL,
    context TEXT,
    declared_confidence REAL CHECK (declared_confidence >= 0 AND declared_confidence <= 1),
    change_note TEXT,
    supersedes_version_id TEXT REFERENCES claim_versions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE (claim_id, version_number)
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    excerpt TEXT NOT NULL,
    locator TEXT,
    strength REAL NOT NULL CHECK (strength >= 0 AND strength <= 1),
    content_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_links (
    id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE RESTRICT,
    claim_version_id TEXT NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    relation TEXT NOT NULL CHECK (relation IN ('SUPPORTS', 'CONTRADICTS')),
    note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (evidence_id, claim_version_id, relation)
);

CREATE TABLE IF NOT EXISTS claim_relations (
    id TEXT PRIMARY KEY,
    from_claim_version_id TEXT NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    to_claim_version_id TEXT NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    relation TEXT NOT NULL CHECK (relation IN ('SUPPORTS', 'CONTRADICTS')),
    note TEXT,
    created_at TEXT NOT NULL,
    CHECK (from_claim_version_id <> to_claim_version_id),
    UNIQUE (from_claim_version_id, to_claim_version_id, relation)
);

CREATE TABLE IF NOT EXISTS coverage_reports (
    id TEXT PRIMARY KEY,
    claim_version_id TEXT NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    as_of TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('CURRENT', 'STALE', 'INSUFFICIENT', 'CONTESTED')),
    report_hash TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (claim_version_id, as_of, report_hash)
);

CREATE TABLE IF NOT EXISTS impact_reports (
    id TEXT PRIMARY KEY,
    root_claim_version_id TEXT NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    max_depth INTEGER NOT NULL CHECK (max_depth >= 1 AND max_depth <= 8),
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('LOCALIZED', 'WIDESPREAD', 'INSUFFICIENT', 'CYCLE_DETECTED')),
    report_hash TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (root_claim_version_id, max_depth, report_hash)
);

CREATE TABLE IF NOT EXISTS contradiction_dossiers (
    id TEXT PRIMARY KEY,
    claim_version_ids_json TEXT NOT NULL,
    selected_version_count INTEGER NOT NULL
        CHECK (selected_version_count >= 2 AND selected_version_count <= 20),
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('UNRESOLVED', 'EVIDENCE_ASYMMETRY',
                                 'INSUFFICIENT', 'NO_CONTRADICTION')),
    dossier_hash TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contradiction_dossier_versions (
    dossier_id TEXT NOT NULL REFERENCES contradiction_dossiers(id) ON DELETE RESTRICT,
    claim_version_id TEXT NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0 AND ordinal < 20),
    PRIMARY KEY (dossier_id, claim_version_id),
    UNIQUE (dossier_id, ordinal)
);

CREATE TABLE IF NOT EXISTS provenance_dossiers (
    id TEXT PRIMARY KEY,
    claim_version_ids_json TEXT NOT NULL,
    selected_version_count INTEGER NOT NULL
        CHECK (selected_version_count >= 1 AND selected_version_count <= 20),
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('DIVERSE', 'CONCENTRATED',
                                 'INSUFFICIENT', 'UNKNOWN_PROVENANCE')),
    dossier_hash TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provenance_dossier_versions (
    dossier_id TEXT NOT NULL REFERENCES provenance_dossiers(id) ON DELETE RESTRICT,
    claim_version_id TEXT NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0 AND ordinal < 20),
    PRIMARY KEY (dossier_id, claim_version_id),
    UNIQUE (dossier_id, ordinal)
);

CREATE TABLE IF NOT EXISTS trust_alignment_dossiers (
    id TEXT PRIMARY KEY,
    claim_version_ids_json TEXT NOT NULL,
    selected_version_count INTEGER NOT NULL
        CHECK (selected_version_count >= 1 AND selected_version_count <= 20),
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('ALIGNED', 'OVERCONFIDENT', 'UNDERCONFIDENT',
                                 'INSUFFICIENT', 'INCOMPATIBLE')),
    dossier_hash TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trust_alignment_dossier_versions (
    dossier_id TEXT NOT NULL REFERENCES trust_alignment_dossiers(id) ON DELETE RESTRICT,
    claim_version_id TEXT NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0 AND ordinal < 20),
    PRIMARY KEY (dossier_id, claim_version_id),
    UNIQUE (dossier_id, ordinal)
);

CREATE TABLE IF NOT EXISTS claim_evolution_dossiers (
    id TEXT PRIMARY KEY,
    claim_version_ids_json TEXT NOT NULL,
    claim_id TEXT REFERENCES claims(id) ON DELETE RESTRICT,
    selected_version_count INTEGER NOT NULL
        CHECK (selected_version_count >= 2 AND selected_version_count <= 50),
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('STABLE', 'ESCALATING', 'RESOLVING',
                                 'INSUFFICIENT', 'INCOMPATIBLE')),
    dossier_hash TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_evolution_dossier_versions (
    dossier_id TEXT NOT NULL REFERENCES claim_evolution_dossiers(id) ON DELETE RESTRICT,
    claim_version_id TEXT NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0 AND ordinal < 50),
    PRIMARY KEY (dossier_id, claim_version_id),
    UNIQUE (dossier_id, ordinal)
);

CREATE TABLE IF NOT EXISTS evidence_gap_dossiers (
    id TEXT PRIMARY KEY,
    claim_id TEXT REFERENCES claims(id) ON DELETE RESTRICT,
    claim_version_ids_json TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    reference_count INTEGER NOT NULL CHECK (reference_count >= 2 AND reference_count <= 100),
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('COVERED', 'GAPPED', 'INSUFFICIENT', 'INCOMPATIBLE')),
    dossier_hash TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_gap_dossier_versions (
    dossier_id TEXT NOT NULL REFERENCES evidence_gap_dossiers(id) ON DELETE RESTRICT,
    claim_version_id TEXT NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0 AND ordinal < 100),
    PRIMARY KEY (dossier_id, claim_version_id),
    UNIQUE (dossier_id, ordinal)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_claim_versions_claim
    ON claim_versions (claim_id, version_number DESC);
CREATE INDEX IF NOT EXISTS ix_evidence_links_version
    ON evidence_links (claim_version_id, relation);
CREATE INDEX IF NOT EXISTS ix_claim_relations_from
    ON claim_relations (from_claim_version_id, relation);
CREATE INDEX IF NOT EXISTS ix_claim_relations_to
    ON claim_relations (to_claim_version_id, relation);
CREATE INDEX IF NOT EXISTS ix_coverage_reports_version
    ON coverage_reports (claim_version_id, as_of DESC);
CREATE INDEX IF NOT EXISTS ix_impact_reports_root
    ON impact_reports (root_claim_version_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_contradiction_dossiers_created
    ON contradiction_dossiers (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_contradiction_dossier_versions_version
    ON contradiction_dossier_versions (claim_version_id);
CREATE INDEX IF NOT EXISTS ix_provenance_dossiers_created
    ON provenance_dossiers (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_provenance_dossier_versions_version
    ON provenance_dossier_versions (claim_version_id);
CREATE INDEX IF NOT EXISTS ix_trust_alignment_dossiers_created
    ON trust_alignment_dossiers (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_trust_alignment_dossier_versions_version
    ON trust_alignment_dossier_versions (claim_version_id);
CREATE INDEX IF NOT EXISTS ix_claim_evolution_dossiers_created
    ON claim_evolution_dossiers (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_claim_evolution_dossier_versions_version
    ON claim_evolution_dossier_versions (claim_version_id);
CREATE INDEX IF NOT EXISTS ix_evidence_gap_dossiers_created
    ON evidence_gap_dossiers (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_evidence_gap_dossier_versions_version
    ON evidence_gap_dossier_versions (claim_version_id);
CREATE INDEX IF NOT EXISTS ix_audit_events_subject
    ON audit_events (subject_type, subject_id, created_at);

CREATE TRIGGER IF NOT EXISTS coverage_reports_no_update
BEFORE UPDATE ON coverage_reports
BEGIN
    SELECT RAISE(ABORT, 'coverage reports are immutable');
END;

CREATE TRIGGER IF NOT EXISTS coverage_reports_no_delete
BEFORE DELETE ON coverage_reports
BEGIN
    SELECT RAISE(ABORT, 'coverage reports are immutable');
END;

CREATE TRIGGER IF NOT EXISTS impact_reports_no_update
BEFORE UPDATE ON impact_reports
BEGIN
    SELECT RAISE(ABORT, 'impact reports are immutable');
END;

CREATE TRIGGER IF NOT EXISTS impact_reports_no_delete
BEFORE DELETE ON impact_reports
BEGIN
    SELECT RAISE(ABORT, 'impact reports are immutable');
END;

CREATE TRIGGER IF NOT EXISTS contradiction_dossiers_no_update
BEFORE UPDATE ON contradiction_dossiers
BEGIN
    SELECT RAISE(ABORT, 'contradiction dossiers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS contradiction_dossiers_no_delete
BEFORE DELETE ON contradiction_dossiers
BEGIN
    SELECT RAISE(ABORT, 'contradiction dossiers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS contradiction_dossier_versions_no_update
BEFORE UPDATE ON contradiction_dossier_versions
BEGIN
    SELECT RAISE(ABORT, 'contradiction dossier versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS contradiction_dossier_versions_no_delete
BEFORE DELETE ON contradiction_dossier_versions
BEGIN
    SELECT RAISE(ABORT, 'contradiction dossier versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS provenance_dossiers_no_update
BEFORE UPDATE ON provenance_dossiers
BEGIN
    SELECT RAISE(ABORT, 'provenance dossiers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS provenance_dossiers_no_delete
BEFORE DELETE ON provenance_dossiers
BEGIN
    SELECT RAISE(ABORT, 'provenance dossiers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS provenance_dossier_versions_no_update
BEFORE UPDATE ON provenance_dossier_versions
BEGIN
    SELECT RAISE(ABORT, 'provenance dossier versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS provenance_dossier_versions_no_delete
BEFORE DELETE ON provenance_dossier_versions
BEGIN
    SELECT RAISE(ABORT, 'provenance dossier versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trust_alignment_dossiers_no_update
BEFORE UPDATE ON trust_alignment_dossiers
BEGIN
    SELECT RAISE(ABORT, 'trust alignment dossiers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trust_alignment_dossiers_no_delete
BEFORE DELETE ON trust_alignment_dossiers
BEGIN
    SELECT RAISE(ABORT, 'trust alignment dossiers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trust_alignment_dossier_versions_no_update
BEFORE UPDATE ON trust_alignment_dossier_versions
BEGIN
    SELECT RAISE(ABORT, 'trust alignment dossier versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trust_alignment_dossier_versions_no_delete
BEFORE DELETE ON trust_alignment_dossier_versions
BEGIN
    SELECT RAISE(ABORT, 'trust alignment dossier versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS claim_evolution_dossiers_no_update
BEFORE UPDATE ON claim_evolution_dossiers
BEGIN
    SELECT RAISE(ABORT, 'claim evolution dossiers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS claim_evolution_dossiers_no_delete
BEFORE DELETE ON claim_evolution_dossiers
BEGIN
    SELECT RAISE(ABORT, 'claim evolution dossiers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS claim_evolution_dossier_versions_no_update
BEFORE UPDATE ON claim_evolution_dossier_versions
BEGIN
    SELECT RAISE(ABORT, 'claim evolution dossier versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS claim_evolution_dossier_versions_no_delete
BEFORE DELETE ON claim_evolution_dossier_versions
BEGIN
    SELECT RAISE(ABORT, 'claim evolution dossier versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS evidence_gap_dossiers_no_update
BEFORE UPDATE ON evidence_gap_dossiers
BEGIN
    SELECT RAISE(ABORT, 'evidence gap dossiers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS evidence_gap_dossiers_no_delete
BEFORE DELETE ON evidence_gap_dossiers
BEGIN
    SELECT RAISE(ABORT, 'evidence gap dossiers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS evidence_gap_dossier_versions_no_update
BEFORE UPDATE ON evidence_gap_dossier_versions
BEGIN
    SELECT RAISE(ABORT, 'evidence gap dossier versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS evidence_gap_dossier_versions_no_delete
BEFORE DELETE ON evidence_gap_dossier_versions
BEGIN
    SELECT RAISE(ABORT, 'evidence gap dossier versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit events are append-only');
END;
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SQLITE_SCHEMA)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(claim_versions)").fetchall()
            }
            if "declared_confidence" not in columns:
                connection.execute(
                    """ALTER TABLE claim_versions ADD COLUMN declared_confidence REAL
                       CHECK (declared_confidence >= 0 AND declared_confidence <= 1)"""
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
