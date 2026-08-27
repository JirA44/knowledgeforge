BEGIN;

CREATE TABLE sources (
    id UUID PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    publisher TEXT,
    published_at TIMESTAMPTZ,
    reliability NUMERIC(5,4) NOT NULL CHECK (reliability BETWEEN 0 AND 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE claims (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE claim_versions (
    id UUID PRIMARY KEY,
    claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE RESTRICT,
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    statement TEXT NOT NULL,
    context TEXT,
    declared_confidence NUMERIC(6,5) CHECK (declared_confidence BETWEEN 0 AND 1),
    change_note TEXT,
    supersedes_version_id UUID REFERENCES claim_versions(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (claim_id, version_number)
);

CREATE TABLE evidence (
    id UUID PRIMARY KEY,
    source_id UUID NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    excerpt TEXT NOT NULL,
    locator TEXT,
    strength NUMERIC(5,4) NOT NULL CHECK (strength BETWEEN 0 AND 1),
    content_hash CHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE evidence_links (
    id UUID PRIMARY KEY,
    evidence_id UUID NOT NULL REFERENCES evidence(id) ON DELETE RESTRICT,
    claim_version_id UUID NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    relation TEXT NOT NULL CHECK (relation IN ('SUPPORTS', 'CONTRADICTS')),
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (evidence_id, claim_version_id, relation)
);

CREATE TABLE claim_relations (
    id UUID PRIMARY KEY,
    from_claim_version_id UUID NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    to_claim_version_id UUID NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    relation TEXT NOT NULL CHECK (relation IN ('SUPPORTS', 'CONTRADICTS')),
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (from_claim_version_id <> to_claim_version_id),
    UNIQUE (from_claim_version_id, to_claim_version_id, relation)
);

CREATE TABLE coverage_reports (
    id UUID PRIMARY KEY,
    claim_version_id UUID NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    as_of DATE NOT NULL,
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('CURRENT', 'STALE', 'INSUFFICIENT', 'CONTESTED')),
    report_hash CHAR(64) NOT NULL UNIQUE,
    snapshot_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (claim_version_id, as_of, report_hash)
);

CREATE TABLE impact_reports (
    id UUID PRIMARY KEY,
    root_claim_version_id UUID NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    max_depth INTEGER NOT NULL CHECK (max_depth BETWEEN 1 AND 8),
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('LOCALIZED', 'WIDESPREAD', 'INSUFFICIENT', 'CYCLE_DETECTED')),
    report_hash CHAR(64) NOT NULL UNIQUE,
    snapshot_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (root_claim_version_id, max_depth, report_hash)
);

CREATE TABLE contradiction_dossiers (
    id UUID PRIMARY KEY,
    claim_version_ids UUID[] NOT NULL,
    selected_version_count INTEGER NOT NULL CHECK (selected_version_count BETWEEN 2 AND 20),
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('UNRESOLVED', 'EVIDENCE_ASYMMETRY',
                                 'INSUFFICIENT', 'NO_CONTRADICTION')),
    dossier_hash CHAR(64) NOT NULL UNIQUE,
    snapshot_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (cardinality(claim_version_ids) = selected_version_count)
);

CREATE TABLE contradiction_dossier_versions (
    dossier_id UUID NOT NULL REFERENCES contradiction_dossiers(id) ON DELETE RESTRICT,
    claim_version_id UUID NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 0 AND 19),
    PRIMARY KEY (dossier_id, claim_version_id),
    UNIQUE (dossier_id, ordinal)
);

CREATE TABLE provenance_dossiers (
    id UUID PRIMARY KEY,
    claim_version_ids UUID[] NOT NULL,
    selected_version_count INTEGER NOT NULL CHECK (selected_version_count BETWEEN 1 AND 20),
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('DIVERSE', 'CONCENTRATED',
                                 'INSUFFICIENT', 'UNKNOWN_PROVENANCE')),
    dossier_hash CHAR(64) NOT NULL UNIQUE,
    snapshot_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (cardinality(claim_version_ids) = selected_version_count)
);

CREATE TABLE provenance_dossier_versions (
    dossier_id UUID NOT NULL REFERENCES provenance_dossiers(id) ON DELETE RESTRICT,
    claim_version_id UUID NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 0 AND 19),
    PRIMARY KEY (dossier_id, claim_version_id),
    UNIQUE (dossier_id, ordinal)
);

CREATE TABLE trust_alignment_dossiers (
    id UUID PRIMARY KEY,
    claim_version_ids UUID[] NOT NULL,
    selected_version_count INTEGER NOT NULL CHECK (selected_version_count BETWEEN 1 AND 20),
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('ALIGNED', 'OVERCONFIDENT', 'UNDERCONFIDENT',
                                 'INSUFFICIENT', 'INCOMPATIBLE')),
    dossier_hash CHAR(64) NOT NULL UNIQUE,
    snapshot_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (cardinality(claim_version_ids) = selected_version_count)
);

CREATE TABLE trust_alignment_dossier_versions (
    dossier_id UUID NOT NULL REFERENCES trust_alignment_dossiers(id) ON DELETE RESTRICT,
    claim_version_id UUID NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 0 AND 19),
    PRIMARY KEY (dossier_id, claim_version_id),
    UNIQUE (dossier_id, ordinal)
);

CREATE TABLE claim_evolution_dossiers (
    id UUID PRIMARY KEY,
    claim_version_ids UUID[] NOT NULL,
    claim_id UUID REFERENCES claims(id) ON DELETE RESTRICT,
    selected_version_count INTEGER NOT NULL CHECK (selected_version_count BETWEEN 2 AND 50),
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('STABLE', 'ESCALATING', 'RESOLVING',
                                 'INSUFFICIENT', 'INCOMPATIBLE')),
    dossier_hash CHAR(64) NOT NULL UNIQUE,
    snapshot_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (cardinality(claim_version_ids) = selected_version_count)
);

CREATE TABLE claim_evolution_dossier_versions (
    dossier_id UUID NOT NULL REFERENCES claim_evolution_dossiers(id) ON DELETE RESTRICT,
    claim_version_id UUID NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 0 AND 49),
    PRIMARY KEY (dossier_id, claim_version_id),
    UNIQUE (dossier_id, ordinal)
);

CREATE TABLE evidence_gap_dossiers (
    id UUID PRIMARY KEY,
    claim_id UUID REFERENCES claims(id) ON DELETE RESTRICT,
    claim_version_ids UUID[] NOT NULL,
    evidence_ids UUID[] NOT NULL,
    reference_count INTEGER NOT NULL CHECK (reference_count BETWEEN 2 AND 100),
    algorithm_version TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('COVERED', 'GAPPED', 'INSUFFICIENT', 'INCOMPATIBLE')),
    dossier_hash CHAR(64) NOT NULL UNIQUE,
    snapshot_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (cardinality(claim_version_ids) + cardinality(evidence_ids) = reference_count)
);

CREATE TABLE evidence_gap_dossier_versions (
    dossier_id UUID NOT NULL REFERENCES evidence_gap_dossiers(id) ON DELETE RESTRICT,
    claim_version_id UUID NOT NULL REFERENCES claim_versions(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 0 AND 99),
    PRIMARY KEY (dossier_id, claim_version_id),
    UNIQUE (dossier_id, ordinal)
);

CREATE TABLE audit_events (
    id UUID PRIMARY KEY,
    event_type TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id UUID NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE OR REPLACE FUNCTION reject_coverage_report_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'coverage reports are immutable';
END;
$$;

CREATE TRIGGER coverage_reports_no_update_or_delete
BEFORE UPDATE OR DELETE ON coverage_reports
FOR EACH ROW EXECUTE FUNCTION reject_coverage_report_mutation();

CREATE OR REPLACE FUNCTION reject_impact_report_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'impact reports are immutable';
END;
$$;

CREATE TRIGGER impact_reports_no_update_or_delete
BEFORE UPDATE OR DELETE ON impact_reports
FOR EACH ROW EXECUTE FUNCTION reject_impact_report_mutation();

CREATE OR REPLACE FUNCTION reject_contradiction_dossier_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'contradiction dossiers are immutable';
END;
$$;

CREATE TRIGGER contradiction_dossiers_no_update_or_delete
BEFORE UPDATE OR DELETE ON contradiction_dossiers
FOR EACH ROW EXECUTE FUNCTION reject_contradiction_dossier_mutation();

CREATE TRIGGER contradiction_dossier_versions_no_update_or_delete
BEFORE UPDATE OR DELETE ON contradiction_dossier_versions
FOR EACH ROW EXECUTE FUNCTION reject_contradiction_dossier_mutation();

CREATE OR REPLACE FUNCTION reject_provenance_dossier_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'provenance dossiers are immutable';
END;
$$;

CREATE TRIGGER provenance_dossiers_no_update_or_delete
BEFORE UPDATE OR DELETE ON provenance_dossiers
FOR EACH ROW EXECUTE FUNCTION reject_provenance_dossier_mutation();

CREATE TRIGGER provenance_dossier_versions_no_update_or_delete
BEFORE UPDATE OR DELETE ON provenance_dossier_versions
FOR EACH ROW EXECUTE FUNCTION reject_provenance_dossier_mutation();

CREATE OR REPLACE FUNCTION reject_trust_alignment_dossier_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'trust alignment dossiers are immutable';
END;
$$;

CREATE TRIGGER trust_alignment_dossiers_no_update_or_delete
BEFORE UPDATE OR DELETE ON trust_alignment_dossiers
FOR EACH ROW EXECUTE FUNCTION reject_trust_alignment_dossier_mutation();

CREATE TRIGGER trust_alignment_dossier_versions_no_update_or_delete
BEFORE UPDATE OR DELETE ON trust_alignment_dossier_versions
FOR EACH ROW EXECUTE FUNCTION reject_trust_alignment_dossier_mutation();

CREATE OR REPLACE FUNCTION reject_claim_evolution_dossier_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'claim evolution dossiers are immutable';
END;
$$;

CREATE TRIGGER claim_evolution_dossiers_no_update_or_delete
BEFORE UPDATE OR DELETE ON claim_evolution_dossiers
FOR EACH ROW EXECUTE FUNCTION reject_claim_evolution_dossier_mutation();

CREATE TRIGGER claim_evolution_dossier_versions_no_update_or_delete
BEFORE UPDATE OR DELETE ON claim_evolution_dossier_versions
FOR EACH ROW EXECUTE FUNCTION reject_claim_evolution_dossier_mutation();

CREATE OR REPLACE FUNCTION reject_evidence_gap_dossier_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'evidence gap dossiers are immutable';
END;
$$;

CREATE TRIGGER evidence_gap_dossiers_no_update_or_delete
BEFORE UPDATE OR DELETE ON evidence_gap_dossiers
FOR EACH ROW EXECUTE FUNCTION reject_evidence_gap_dossier_mutation();

CREATE TRIGGER evidence_gap_dossier_versions_no_update_or_delete
BEFORE UPDATE OR DELETE ON evidence_gap_dossier_versions
FOR EACH ROW EXECUTE FUNCTION reject_evidence_gap_dossier_mutation();

CREATE OR REPLACE FUNCTION reject_audit_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit events are append-only';
END;
$$;

CREATE TRIGGER audit_events_no_update_or_delete
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION reject_audit_event_mutation();

CREATE INDEX ix_claim_versions_claim ON claim_versions (claim_id, version_number DESC);
CREATE INDEX ix_evidence_links_version ON evidence_links (claim_version_id, relation);
CREATE INDEX ix_claim_relations_from ON claim_relations (from_claim_version_id, relation);
CREATE INDEX ix_claim_relations_to ON claim_relations (to_claim_version_id, relation);
CREATE INDEX ix_coverage_reports_version ON coverage_reports (claim_version_id, as_of DESC);
CREATE INDEX ix_impact_reports_root ON impact_reports (root_claim_version_id, created_at DESC);
CREATE INDEX ix_contradiction_dossiers_created ON contradiction_dossiers (created_at DESC);
CREATE INDEX ix_contradiction_dossier_versions_version
    ON contradiction_dossier_versions (claim_version_id);
CREATE INDEX ix_provenance_dossiers_created
    ON provenance_dossiers (created_at DESC, id DESC);
CREATE INDEX ix_provenance_dossier_versions_version
    ON provenance_dossier_versions (claim_version_id);
CREATE INDEX ix_trust_alignment_dossiers_created
    ON trust_alignment_dossiers (created_at DESC, id DESC);
CREATE INDEX ix_trust_alignment_dossier_versions_version
    ON trust_alignment_dossier_versions (claim_version_id);
CREATE INDEX ix_claim_evolution_dossiers_created
    ON claim_evolution_dossiers (created_at DESC, id DESC);
CREATE INDEX ix_claim_evolution_dossier_versions_version
    ON claim_evolution_dossier_versions (claim_version_id);
CREATE INDEX ix_evidence_gap_dossiers_created
    ON evidence_gap_dossiers (created_at DESC, id DESC);
CREATE INDEX ix_evidence_gap_dossier_versions_version
    ON evidence_gap_dossier_versions (claim_version_id);
CREATE INDEX ix_audit_events_subject ON audit_events (subject_type, subject_id, created_at);

COMMIT;
