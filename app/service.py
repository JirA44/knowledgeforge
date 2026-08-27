from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import unicodedata
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from .db import Database
from .models import (
    ClaimCreate,
    ClaimRelationCreate,
    ClaimVersionCreate,
    ClaimEvolutionDossierCreate,
    EvidenceGapDossierCreate,
    ContradictionDossierCreate,
    CoverageReportCreate,
    EvidenceCreate,
    ImpactReportCreate,
    ProvenanceDossierCreate,
    SourceCreate,
    TrustAlignmentDossierCreate,
)


class NotFoundError(RuntimeError):
    pass


class ConflictError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).split())


def evidence_hash(source_id: str, excerpt: str, locator: str | None, strength: float) -> str:
    canonical = json.dumps(
        {
            "source_id": source_id,
            "excerpt": normalize_text(excerpt),
            "locator": normalize_text(locator),
            "strength": round(float(strength), 10),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_hash(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_published_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def independent_source_key(source_id: str, publisher: str | None, url: str | None) -> str:
    normalized_publisher = normalize_text(publisher).casefold()
    if normalized_publisher:
        return f"publisher:{normalized_publisher}"
    hostname = (urlparse(url or "").hostname or "").casefold()
    return f"host:{hostname}" if hostname else f"source:{source_id}"


class KnowledgeService:
    SUPPORT_THRESHOLD = 0.55
    CONTEST_THRESHOLD = 0.35
    MIN_SUPPORT_SOURCES = 2
    MIN_MARGIN = 0.20
    COVERAGE_ALGORITHM_VERSION = "coverage-v1.0.1"
    IMPACT_ALGORITHM_VERSION = "impact-v1.0.2"
    CONTRADICTION_DOSSIER_ALGORITHM_VERSION = "contradiction-dossier-v1.0.3"
    FRESHNESS_MAX_DAYS = 365
    MIN_INDEPENDENT_SUPPORT_SOURCES = 2
    WIDESPREAD_MIN_AFFECTED_CLAIMS = 4
    CONTRADICTION_PATH_MAX_DEPTH = 3
    CONTRADICTION_MAX_PATHS_PER_PAIR = 20
    CONTRADICTION_MIN_SUPPORT_SOURCES_PER_SIDE = 2
    CONTRADICTION_ASYMMETRY_MIN_SOURCE_GAP = 2
    CONTRADICTION_ASYMMETRY_MIN_MASS_GAP = 0.25
    PROVENANCE_DOSSIER_ALGORITHM_VERSION = "provenance-dossier-v1.0.4"
    PROVENANCE_DIVERSE_MIN_INDEPENDENT_SOURCES = 3
    PROVENANCE_DIVERSE_MAX_DOMINANT_SHARE = 0.50
    TRUST_ALIGNMENT_ALGORITHM_VERSION = "trust-alignment-v1.0.5"
    TRUST_ALIGNMENT_TOLERANCE = 0.10
    TRUST_ALIGNMENT_MIN_SUPPORT_SOURCES = 2
    TRUST_ALIGNMENT_DIVERSITY_TARGET = 3
    CLAIM_EVOLUTION_ALGORITHM_VERSION = "claim-evolution-v1.0.6"
    CLAIM_EVOLUTION_MATERIAL_DELTA = 0.10
    CLAIM_EVOLUTION_DIRECTION_DEADBAND = 0.05
    EVIDENCE_GAP_ALGORITHM_VERSION = "evidence-gap-v1.0.7"
    EVIDENCE_GAP_STALE_DAYS = 365

    def __init__(self, database: Database):
        self.database = database

    def create_source(self, payload: SourceCreate) -> dict[str, Any]:
        item = {
            "id": new_id(),
            "url": payload.url,
            "title": payload.title,
            "publisher": payload.publisher,
            "published_at": payload.published_at.isoformat() if payload.published_at else None,
            "reliability": payload.reliability,
            "created_at": utc_now(),
        }
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO sources
                   (id, url, title, publisher, published_at, reliability, created_at)
                   VALUES (:id, :url, :title, :publisher, :published_at, :reliability, :created_at)""",
                item,
            )
        return item

    def get_source(self, source_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not row:
            raise NotFoundError("Source introuvable")
        return dict(row)

    def create_claim(self, payload: ClaimCreate) -> dict[str, Any]:
        claim_id = new_id()
        version_id = new_id()
        created_at = utc_now()
        with self.database.connect() as connection:
            connection.execute("INSERT INTO claims (id, created_at) VALUES (?, ?)", (claim_id, created_at))
            connection.execute(
                """INSERT INTO claim_versions
                   (id, claim_id, version_number, statement, context,
                    declared_confidence, change_note, supersedes_version_id, created_at)
                   VALUES (?, ?, 1, ?, ?, ?, ?, NULL, ?)""",
                (
                    version_id,
                    claim_id,
                    payload.statement,
                    payload.context,
                    payload.declared_confidence,
                    "Création",
                    created_at,
                ),
            )
        return self.get_claim(claim_id)

    def get_claim(self, claim_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            claim = connection.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
            if not claim:
                raise NotFoundError("Affirmation introuvable")
            versions = connection.execute(
                "SELECT * FROM claim_versions WHERE claim_id = ? ORDER BY version_number",
                (claim_id,),
            ).fetchall()
        serialized = [dict(version) for version in versions]
        return {
            "id": claim["id"],
            "created_at": claim["created_at"],
            "current_version": serialized[-1],
            "versions": serialized,
        }

    def create_claim_version(self, claim_id: str, payload: ClaimVersionCreate) -> dict[str, Any]:
        with self.database.connect() as connection:
            current = connection.execute(
                "SELECT * FROM claim_versions WHERE claim_id = ? ORDER BY version_number DESC LIMIT 1",
                (claim_id,),
            ).fetchone()
            if not current:
                raise NotFoundError("Affirmation introuvable")
            if payload.expected_current_version is not None and current["version_number"] != payload.expected_current_version:
                raise ConflictError(
                    f"Version courante {current['version_number']}; version attendue {payload.expected_current_version}"
                )
            version = {
                "id": new_id(),
                "claim_id": claim_id,
                "version_number": current["version_number"] + 1,
                "statement": payload.statement,
                "context": payload.context,
                "declared_confidence": payload.declared_confidence,
                "change_note": payload.change_note,
                "supersedes_version_id": current["id"],
                "created_at": utc_now(),
            }
            try:
                connection.execute(
                    """INSERT INTO claim_versions
                       (id, claim_id, version_number, statement, context,
                        declared_confidence, change_note, supersedes_version_id, created_at)
                       VALUES (:id, :claim_id, :version_number, :statement, :context,
                               :declared_confidence, :change_note,
                               :supersedes_version_id, :created_at)""",
                    version,
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("Conflit concurrent de version") from exc
        return version

    def _claim_version(self, connection: sqlite3.Connection, claim_id: str, number: int | None) -> sqlite3.Row:
        if number is None:
            row = connection.execute(
                "SELECT * FROM claim_versions WHERE claim_id = ? ORDER BY version_number DESC LIMIT 1",
                (claim_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM claim_versions WHERE claim_id = ? AND version_number = ?",
                (claim_id, number),
            ).fetchone()
        if not row:
            raise NotFoundError("Affirmation ou version introuvable")
        return row

    def add_evidence(self, claim_id: str, payload: EvidenceCreate) -> dict[str, Any]:
        digest = evidence_hash(payload.source_id, payload.excerpt, payload.locator, payload.strength)
        created_at = utc_now()
        deduplicated = False
        with self.database.connect() as connection:
            version = self._claim_version(connection, claim_id, payload.claim_version_number)
            if not connection.execute("SELECT 1 FROM sources WHERE id = ?", (payload.source_id,)).fetchone():
                raise NotFoundError("Source introuvable")
            evidence = connection.execute("SELECT * FROM evidence WHERE content_hash = ?", (digest,)).fetchone()
            if evidence:
                deduplicated = True
                evidence_id = evidence["id"]
            else:
                evidence_id = new_id()
                connection.execute(
                    """INSERT INTO evidence
                       (id, source_id, excerpt, locator, strength, content_hash, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        evidence_id,
                        payload.source_id,
                        payload.excerpt,
                        payload.locator,
                        payload.strength,
                        digest,
                        created_at,
                    ),
                )
            link_id = new_id()
            try:
                connection.execute(
                    """INSERT INTO evidence_links
                       (id, evidence_id, claim_version_id, relation, note, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (link_id, evidence_id, version["id"], payload.relation, payload.note, created_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("Cet élément probant est déjà lié ainsi à cette version") from exc
            stored = connection.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
        return {
            "evidence": dict(stored),
            "link": {
                "id": link_id,
                "claim_version_id": version["id"],
                "claim_version_number": version["version_number"],
                "relation": payload.relation,
                "note": payload.note,
                "created_at": created_at,
            },
            "deduplicated": deduplicated,
        }

    def get_evidence_verification(self, evidence_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            item = connection.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
        if not item:
            raise NotFoundError("Élément probant introuvable")
        recalculated = evidence_hash(item["source_id"], item["excerpt"], item["locator"], item["strength"])
        return {
            "evidence_id": item["id"],
            "algorithm": "sha256",
            "stored_hash": item["content_hash"],
            "recalculated_hash": recalculated,
            "valid": recalculated == item["content_hash"],
        }

    def create_claim_relation(self, claim_id: str, payload: ClaimRelationCreate) -> dict[str, Any]:
        with self.database.connect() as connection:
            origin = self._claim_version(connection, claim_id, payload.from_version_number)
            related = self._claim_version(connection, payload.related_claim_id, payload.related_version_number)
            if origin["id"] == related["id"]:
                raise ConflictError("Une version ne peut pas être reliée à elle-même")
            item = {
                "id": new_id(),
                "from_claim_version_id": origin["id"],
                "to_claim_version_id": related["id"],
                "relation": payload.relation,
                "note": payload.note,
                "created_at": utc_now(),
            }
            try:
                connection.execute(
                    """INSERT INTO claim_relations
                       (id, from_claim_version_id, to_claim_version_id, relation, note, created_at)
                       VALUES (:id, :from_claim_version_id, :to_claim_version_id, :relation, :note, :created_at)""",
                    item,
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("Relation déjà enregistrée ou invalide") from exc
        return item

    @staticmethod
    def _independent_mass(weights: list[float]) -> float:
        if not weights:
            return 0.0
        remainder = math.prod(1.0 - min(1.0, max(0.0, weight)) for weight in weights)
        return 1.0 - remainder

    def assess(self, claim_id: str, version_number: int | None = None) -> dict[str, Any]:
        with self.database.connect() as connection:
            version = self._claim_version(connection, claim_id, version_number)
            rows = connection.execute(
                """SELECT e.id AS evidence_id, e.content_hash, e.strength, e.excerpt,
                          e.locator, el.relation, el.note, s.id AS source_id,
                          s.title AS source_title, s.url AS source_url,
                          s.reliability AS source_reliability
                   FROM evidence_links el
                   JOIN evidence e ON e.id = el.evidence_id
                   JOIN sources s ON s.id = e.source_id
                   WHERE el.claim_version_id = ?
                   ORDER BY el.created_at, el.id""",
                (version["id"],),
            ).fetchall()

        items: list[dict[str, Any]] = []
        support_weights: list[float] = []
        contradict_weights: list[float] = []
        support_sources: set[str] = set()
        contradict_sources: set[str] = set()
        for row in rows:
            item = dict(row)
            effective_weight = round(item["strength"] * item["source_reliability"], 6)
            item["effective_weight"] = effective_weight
            items.append(item)
            if item["relation"] == "SUPPORTS":
                support_weights.append(effective_weight)
                support_sources.add(item["source_id"])
            else:
                contradict_weights.append(effective_weight)
                contradict_sources.add(item["source_id"])

        support_score = self._independent_mass(support_weights)
        contradict_score = self._independent_mass(contradict_weights)
        margin = support_score - contradict_score
        if (
            support_score >= self.SUPPORT_THRESHOLD
            and len(support_sources) >= self.MIN_SUPPORT_SOURCES
            and contradict_score < self.CONTEST_THRESHOLD
            and margin >= self.MIN_MARGIN
        ):
            verdict = "SUPPORTED"
        elif contradict_score >= self.CONTEST_THRESHOLD or (
            support_score >= self.CONTEST_THRESHOLD and contradict_score >= 0.25
        ):
            verdict = "CONTESTED"
        else:
            verdict = "INSUFFICIENT"

        diversity = 0.5 + 0.5 * min(1.0, len(support_sources) / self.MIN_SUPPORT_SOURCES)
        confidence = support_score * (1.0 - contradict_score) * diversity
        return {
            "claim_id": claim_id,
            "claim_version_id": version["id"],
            "claim_version_number": version["version_number"],
            "statement": version["statement"],
            "verdict": verdict,
            "confidence_score": round(confidence, 4),
            "support_score": round(support_score, 4),
            "contradict_score": round(contradict_score, 4),
            "evidence_count": len(items),
            "support_source_count": len(support_sources),
            "contradict_source_count": len(contradict_sources),
            "thresholds": {
                "support": self.SUPPORT_THRESHOLD,
                "contest": self.CONTEST_THRESHOLD,
                "minimum_support_sources": self.MIN_SUPPORT_SOURCES,
                "minimum_margin": self.MIN_MARGIN,
            },
            "evidence": items,
            "disclaimer": "Verdict documentaire calculé; il ne constitue pas une vérité factuelle.",
        }

    def contradictions(self, claim_id: str, version_number: int | None = None) -> dict[str, Any]:
        assessment = self.assess(claim_id, version_number)
        version_id = assessment["claim_version_id"]
        contradictory = [item for item in assessment["evidence"] if item["relation"] == "CONTRADICTS"]
        has_support = any(item["relation"] == "SUPPORTS" for item in assessment["evidence"])
        with self.database.connect() as connection:
            relations = connection.execute(
                """SELECT cr.*, fv.claim_id AS from_claim_id, fv.version_number AS from_version_number,
                          tv.claim_id AS to_claim_id, tv.version_number AS to_version_number
                   FROM claim_relations cr
                   JOIN claim_versions fv ON fv.id = cr.from_claim_version_id
                   JOIN claim_versions tv ON tv.id = cr.to_claim_version_id
                   WHERE cr.relation = 'CONTRADICTS'
                     AND (cr.from_claim_version_id = ? OR cr.to_claim_version_id = ?)
                   ORDER BY cr.created_at""",
                (version_id, version_id),
            ).fetchall()
        explicit = [dict(item) for item in relations]
        return {
            "claim_id": claim_id,
            "claim_version_number": assessment["claim_version_number"],
            "detected": bool(contradictory or explicit),
            "mixed_evidence": has_support and bool(contradictory),
            "contradictory_evidence": contradictory,
            "explicit_claim_conflicts": explicit,
            "method": "Relations structurées uniquement; aucune contradiction sémantique n'est inventée.",
        }

    @staticmethod
    def _serialize_coverage_report(row: sqlite3.Row, *, replay: bool = False) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot_json"])
        return {
            "id": row["id"],
            **snapshot,
            "report_hash": row["report_hash"],
            "created_at": row["created_at"],
            "idempotent_replay": replay,
        }

    def create_coverage_report(
        self, claim_id: str, payload: CoverageReportCreate
    ) -> dict[str, Any]:
        as_of = payload.as_of or datetime.now(timezone.utc).date()
        with self.database.connect() as connection:
            version = self._claim_version(connection, claim_id, payload.claim_version_number)
            rows = connection.execute(
                """SELECT e.id AS evidence_id, e.source_id, e.excerpt, e.locator,
                          e.strength, e.content_hash, el.relation,
                          s.id AS resolved_source_id, s.url AS source_url,
                          s.publisher AS source_publisher,
                          s.published_at AS source_published_at
                   FROM evidence_links el
                   JOIN evidence e ON e.id = el.evidence_id
                   LEFT JOIN sources s ON s.id = e.source_id
                   WHERE el.claim_version_id = ?
                   ORDER BY e.id, el.relation""",
                (version["id"],),
            ).fetchall()

            evidence_items: list[dict[str, Any]] = []
            support_groups: set[str] = set()
            contradict_groups: set[str] = set()
            fresh_support_groups: set[str] = set()
            usable_groups: set[str] = set()
            missing_source_ids: list[str] = []
            invalid_hash_ids: list[str] = []
            support_evidence_count = 0
            contradict_evidence_count = 0
            fresh_count = 0
            stale_count = 0
            unknown_date_count = 0
            future_date_count = 0

            for row in rows:
                item = dict(row)
                source_exists = item["resolved_source_id"] is not None
                recalculated = evidence_hash(
                    item["source_id"], item["excerpt"], item["locator"], item["strength"]
                )
                hash_valid = recalculated == item["content_hash"]
                published_date = parse_published_date(item["source_published_at"])
                age_days = (as_of - published_date).days if published_date else None
                freshness = "UNKNOWN"
                if age_days is not None:
                    if age_days < 0:
                        freshness = "FUTURE_DATED"
                        future_date_count += 1
                    elif age_days <= self.FRESHNESS_MAX_DAYS:
                        freshness = "FRESH"
                        fresh_count += 1
                    else:
                        freshness = "STALE"
                        stale_count += 1
                else:
                    unknown_date_count += 1

                group = independent_source_key(
                    item["source_id"], item["source_publisher"], item["source_url"]
                )
                usable = source_exists and hash_valid and freshness != "FUTURE_DATED"
                if item["relation"] == "SUPPORTS":
                    support_evidence_count += 1
                else:
                    contradict_evidence_count += 1
                if usable:
                    usable_groups.add(group)
                if usable and item["relation"] == "SUPPORTS":
                    support_groups.add(group)
                    if freshness == "FRESH":
                        fresh_support_groups.add(group)
                elif usable and item["relation"] == "CONTRADICTS":
                    contradict_groups.add(group)
                if not source_exists:
                    missing_source_ids.append(item["evidence_id"])
                if not hash_valid:
                    invalid_hash_ids.append(item["evidence_id"])

                evidence_items.append(
                    {
                        "evidence_id": item["evidence_id"],
                        "source_id": item["source_id"],
                        "relation": item["relation"],
                        "source_exists": source_exists,
                        "independent_source_key": group,
                        "hash_valid": hash_valid,
                        "published_at": item["source_published_at"],
                        "age_days": age_days,
                        "freshness": freshness,
                    }
                )

            if contradict_groups:
                qualification = "CONTESTED"
                reasons = ["Au moins une source exploitable contredit la version."]
            elif missing_source_ids or invalid_hash_ids:
                qualification = "INSUFFICIENT"
                reasons = ["Des éléments liés sont sans source résolue ou ont une empreinte invalide."]
            elif len(support_groups) < self.MIN_INDEPENDENT_SUPPORT_SOURCES:
                qualification = "INSUFFICIENT"
                reasons = ["Moins de deux sources de soutien indépendantes et exploitables."]
            elif len(fresh_support_groups) < self.MIN_INDEPENDENT_SUPPORT_SOURCES:
                qualification = "STALE"
                reasons = ["La couverture est suffisante, mais moins de deux sources indépendantes sont fraîches."]
            else:
                qualification = "CURRENT"
                reasons = ["Au moins deux sources de soutien indépendantes sont fraîches et intègres."]

            dated_count = fresh_count + stale_count
            snapshot = {
                "claim_id": claim_id,
                "claim_version_id": version["id"],
                "claim_version_number": version["version_number"],
                "claim_version_content_hash": canonical_hash(
                    {
                        "statement": version["statement"],
                        "context": version["context"],
                        "declared_confidence": version["declared_confidence"],
                        "version_number": version["version_number"],
                    }
                ),
                "as_of": as_of.isoformat(),
                "algorithm_version": self.COVERAGE_ALGORITHM_VERSION,
                "qualification": qualification,
                "reasons": reasons,
                "coverage": {
                    "evidence_count": len(evidence_items),
                    "support_evidence_count": support_evidence_count,
                    "contradict_evidence_count": contradict_evidence_count,
                    "independent_source_count": len(usable_groups),
                    "independent_support_source_count": len(support_groups),
                    "independent_contradict_source_count": len(contradict_groups),
                    "fresh_independent_support_source_count": len(fresh_support_groups),
                    "supports_present": bool(support_groups),
                    "contradicts_present": bool(contradict_groups),
                },
                "freshness": {
                    "maximum_age_days": self.FRESHNESS_MAX_DAYS,
                    "fresh_count": fresh_count,
                    "stale_count": stale_count,
                    "unknown_date_count": unknown_date_count,
                    "future_date_count": future_date_count,
                    "dated_fresh_ratio": round(fresh_count / dated_count, 4) if dated_count else None,
                },
                "integrity": {
                    "missing_source_evidence_ids": sorted(missing_source_ids),
                    "invalid_hash_evidence_ids": sorted(invalid_hash_ids),
                    "valid": not missing_source_ids and not invalid_hash_ids,
                },
                "evidence": evidence_items,
                "policy": {
                    "minimum_independent_support_sources": self.MIN_INDEPENDENT_SUPPORT_SOURCES,
                    "future_dates_are_usable": False,
                },
                "disclaimer": (
                    "Qualification de couverture documentaire et de fraîcheur; "
                    "elle ne constitue pas une vérité factuelle."
                ),
            }
            report_hash = canonical_hash(snapshot)
            existing = connection.execute(
                "SELECT * FROM coverage_reports WHERE report_hash = ?", (report_hash,)
            ).fetchone()
            if existing:
                return self._serialize_coverage_report(existing, replay=True)

            report_id = new_id()
            created_at = utc_now()
            try:
                connection.execute(
                    """INSERT INTO coverage_reports
                       (id, claim_version_id, as_of, algorithm_version, qualification,
                        report_hash, snapshot_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        report_id,
                        version["id"],
                        as_of.isoformat(),
                        self.COVERAGE_ALGORITHM_VERSION,
                        qualification,
                        report_hash,
                        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                        created_at,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    "SELECT * FROM coverage_reports WHERE report_hash = ?", (report_hash,)
                ).fetchone()
                if existing:
                    return self._serialize_coverage_report(existing, replay=True)
                raise
            connection.execute(
                """INSERT INTO audit_events
                   (id, event_type, subject_type, subject_id, payload_hash, created_at)
                   VALUES (?, 'COVERAGE_REPORT_CREATED', 'coverage_report', ?, ?, ?)""",
                (new_id(), report_id, report_hash, created_at),
            )
            stored = connection.execute(
                "SELECT * FROM coverage_reports WHERE id = ?", (report_id,)
            ).fetchone()
        return self._serialize_coverage_report(stored)

    def get_coverage_report(self, report_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM coverage_reports WHERE id = ?", (report_id,)
            ).fetchone()
        if not row:
            raise NotFoundError("Rapport de couverture introuvable")
        return self._serialize_coverage_report(row)

    @staticmethod
    def _serialize_impact_report(row: sqlite3.Row, *, replay: bool = False) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot_json"])
        return {
            "id": row["id"],
            **snapshot,
            "report_hash": row["report_hash"],
            "created_at": row["created_at"],
            "idempotent_replay": replay,
        }

    @staticmethod
    def _version_content_hash(version: sqlite3.Row) -> str:
        return canonical_hash(
            {
                "statement": version["statement"],
                "context": version["context"],
                "declared_confidence": version["declared_confidence"],
                "version_number": version["version_number"],
            }
        )

    @staticmethod
    def _latest_coverage_summary(
        connection: sqlite3.Connection, version_id: str
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """SELECT * FROM coverage_reports
               WHERE claim_version_id = ?
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (version_id,),
        ).fetchone()
        if not row:
            return None
        snapshot = json.loads(row["snapshot_json"])
        return {
            "id": row["id"],
            "report_hash": row["report_hash"],
            "as_of": snapshot["as_of"],
            "qualification": snapshot["qualification"],
            "coverage": snapshot["coverage"],
            "freshness": snapshot["freshness"],
            "integrity": snapshot["integrity"],
        }

    @staticmethod
    def _outgoing_relations(
        connection: sqlite3.Connection, version_id: str
    ) -> list[sqlite3.Row]:
        return connection.execute(
            """SELECT cr.id AS relation_id, cr.relation, cr.note,
                      fv.id AS from_version_id, fv.claim_id AS from_claim_id,
                      fv.version_number AS from_version_number,
                      tv.id AS to_version_id, tv.claim_id AS to_claim_id,
                      tv.version_number AS to_version_number
               FROM claim_relations cr
               JOIN claim_versions fv ON fv.id = cr.from_claim_version_id
               JOIN claim_versions tv ON tv.id = cr.to_claim_version_id
               WHERE cr.from_claim_version_id = ?
               ORDER BY tv.claim_id, tv.version_number, cr.relation, cr.id""",
            (version_id,),
        ).fetchall()

    @staticmethod
    def _impact_edge(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "relation_id": row["relation_id"],
            "relation": row["relation"],
            "from_claim_id": row["from_claim_id"],
            "from_claim_version_id": row["from_version_id"],
            "from_version_number": row["from_version_number"],
            "to_claim_id": row["to_claim_id"],
            "to_claim_version_id": row["to_version_id"],
            "to_version_number": row["to_version_number"],
        }

    def create_impact_report(
        self, claim_id: str, payload: ImpactReportCreate
    ) -> dict[str, Any]:
        """Snapshot the bounded downstream impact of one immutable claim version.

        Traversal is outgoing-only. A version is expanded at most once, while an
        alternative path is still recorded. Revisiting a version already present
        in the current path is recorded as a cycle and never expanded.
        """

        with self.database.connect() as connection:
            root = self._claim_version(connection, claim_id, payload.claim_version_number)
            root_current = self._claim_version(connection, claim_id, None)
            root_id = root["id"]

            queue: list[tuple[str, int, list[dict[str, Any]], tuple[str, ...]]] = [
                (root_id, 0, [], (root_id,))
            ]
            expanded: set[str] = set()
            affected_paths: dict[str, list[list[dict[str, Any]]]] = {}
            affected_depth: dict[str, int] = {}
            cycle_paths: list[list[dict[str, Any]]] = []
            traversed_relation_ids: set[str] = set()
            branching_node_count = 0
            depth_limited_relation_count = 0
            cursor = 0

            while cursor < len(queue):
                version_id, depth, path, path_nodes = queue[cursor]
                cursor += 1
                if version_id in expanded:
                    continue
                expanded.add(version_id)
                outgoing = self._outgoing_relations(connection, version_id)
                if depth >= payload.max_depth:
                    depth_limited_relation_count += len(outgoing)
                    continue
                if len(outgoing) > 1:
                    branching_node_count += 1

                for relation_row in outgoing:
                    edge = self._impact_edge(relation_row)
                    traversed_relation_ids.add(edge["relation_id"])
                    next_path = [*path, edge]
                    target_id = edge["to_claim_version_id"]
                    if target_id in path_nodes:
                        cycle_paths.append(next_path)
                        continue

                    known_paths = affected_paths.setdefault(target_id, [])
                    if next_path not in known_paths:
                        known_paths.append(next_path)
                    affected_depth[target_id] = min(
                        affected_depth.get(target_id, depth + 1), depth + 1
                    )
                    if target_id not in expanded:
                        queue.append((target_id, depth + 1, next_path, (*path_nodes, target_id)))

            affected: list[dict[str, Any]] = []
            for traversed_version_id in sorted(affected_paths):
                traversed = connection.execute(
                    "SELECT * FROM claim_versions WHERE id = ?", (traversed_version_id,)
                ).fetchone()
                if not traversed:
                    raise ConflictError("Le graphe référence une version introuvable")
                current = self._claim_version(connection, traversed["claim_id"], None)
                paths = sorted(
                    affected_paths[traversed_version_id],
                    key=lambda item: json.dumps(
                        item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ),
                )
                affected.append(
                    {
                        "claim_id": traversed["claim_id"],
                        "traversed_claim_version_id": traversed["id"],
                        "traversed_version_number": traversed["version_number"],
                        "traversed_version_content_hash": self._version_content_hash(traversed),
                        "current_claim_version_id": current["id"],
                        "current_version_number": current["version_number"],
                        "current_version_content_hash": self._version_content_hash(current),
                        "is_current_version": current["id"] == traversed["id"],
                        "depth": affected_depth[traversed_version_id],
                        "relationship_types": sorted(
                            {edge["relation"] for item in paths for edge in item}
                        ),
                        "paths": paths,
                        "coverage_report": self._latest_coverage_summary(
                            connection, traversed_version_id
                        ),
                    }
                )

            cycle_paths = sorted(
                cycle_paths,
                key=lambda item: json.dumps(
                    item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            )
            affected_claim_count = len({item["claim_id"] for item in affected})
            if cycle_paths:
                qualification = "CYCLE_DETECTED"
                reasons = [
                    "Au moins un retour vers une version déjà présente dans le chemin a été détecté."
                ]
            elif not affected:
                qualification = "INSUFFICIENT"
                reasons = ["Aucune affirmation affectée n'est reliée dans la profondeur demandée."]
            elif affected_claim_count >= self.WIDESPREAD_MIN_AFFECTED_CLAIMS:
                qualification = "WIDESPREAD"
                reasons = [
                    "Au moins quatre affirmations distinctes sont atteintes par le graphe directionnel."
                ]
            else:
                qualification = "LOCALIZED"
                reasons = ["Moins de quatre affirmations distinctes sont atteintes."]

            snapshot = {
                "root": {
                    "claim_id": claim_id,
                    "claim_version_id": root_id,
                    "claim_version_number": root["version_number"],
                    "claim_version_content_hash": self._version_content_hash(root),
                    "current_claim_version_id": root_current["id"],
                    "current_version_number": root_current["version_number"],
                    "current_version_content_hash": self._version_content_hash(root_current),
                    "is_current_version": root_current["id"] == root_id,
                    "coverage_report": self._latest_coverage_summary(connection, root_id),
                },
                "max_depth": payload.max_depth,
                "algorithm_version": self.IMPACT_ALGORITHM_VERSION,
                "qualification": qualification,
                "reasons": reasons,
                "metrics": {
                    "affected_claim_count": affected_claim_count,
                    "affected_version_count": len(affected),
                    "traversed_relation_count": len(traversed_relation_ids),
                    "maximum_reached_depth": max(
                        (item["depth"] for item in affected), default=0
                    ),
                    "branching_node_count": branching_node_count,
                    "cycle_count": len(cycle_paths),
                    "depth_limit_reached": depth_limited_relation_count > 0,
                    "depth_limited_relation_count": depth_limited_relation_count,
                },
                "affected_claims": affected,
                "cycles": cycle_paths,
                "policy": {
                    "direction": "OUTGOING",
                    "maximum_allowed_depth": 8,
                    "widespread_minimum_affected_claims": self.WIDESPREAD_MIN_AFFECTED_CLAIMS,
                    "cycle_expansion": False,
                    "client_supplied_qualification": False,
                },
                "disclaimer": (
                    "Qualification structurelle de propagation dans les relations enregistrées; "
                    "elle ne constitue ni une vérité factuelle ni une validation sémantique."
                ),
            }
            report_hash = canonical_hash(snapshot)
            existing = connection.execute(
                "SELECT * FROM impact_reports WHERE report_hash = ?", (report_hash,)
            ).fetchone()
            if existing:
                return self._serialize_impact_report(existing, replay=True)

            report_id = new_id()
            created_at = utc_now()
            try:
                connection.execute(
                    """INSERT INTO impact_reports
                       (id, root_claim_version_id, max_depth, algorithm_version,
                        qualification, report_hash, snapshot_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        report_id,
                        root_id,
                        payload.max_depth,
                        self.IMPACT_ALGORITHM_VERSION,
                        qualification,
                        report_hash,
                        json.dumps(
                            snapshot,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        created_at,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    "SELECT * FROM impact_reports WHERE report_hash = ?", (report_hash,)
                ).fetchone()
                if existing:
                    return self._serialize_impact_report(existing, replay=True)
                raise
            connection.execute(
                """INSERT INTO audit_events
                   (id, event_type, subject_type, subject_id, payload_hash, created_at)
                   VALUES (?, 'IMPACT_REPORT_CREATED', 'impact_report', ?, ?, ?)""",
                (new_id(), report_id, report_hash, created_at),
            )
            stored = connection.execute(
                "SELECT * FROM impact_reports WHERE id = ?", (report_id,)
            ).fetchone()
        return self._serialize_impact_report(stored)

    def get_impact_report(self, report_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM impact_reports WHERE id = ?", (report_id,)
            ).fetchone()
        if not row:
            raise NotFoundError("Rapport d'impact introuvable")
        return self._serialize_impact_report(row)

    @staticmethod
    def _serialize_contradiction_dossier(
        row: sqlite3.Row, *, replay: bool = False
    ) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot_json"])
        return {
            "id": row["id"],
            **snapshot,
            "dossier_hash": row["dossier_hash"],
            "created_at": row["created_at"],
            "idempotent_replay": replay,
        }

    @staticmethod
    def _claim_version_by_id(
        connection: sqlite3.Connection, version_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM claim_versions WHERE id = ?", (version_id,)
        ).fetchone()
        if not row:
            raise NotFoundError(f"Version d'affirmation introuvable: {version_id}")
        return row

    def _dossier_evidence_summary(
        self, connection: sqlite3.Connection, version_id: str
    ) -> dict[str, Any]:
        rows = connection.execute(
            """SELECT e.id AS evidence_id, e.source_id, e.excerpt, e.locator,
                      e.strength, e.content_hash, el.relation,
                      s.id AS resolved_source_id, s.url AS source_url,
                      s.publisher AS source_publisher,
                      s.reliability AS source_reliability
               FROM evidence_links el
               JOIN evidence e ON e.id = el.evidence_id
               LEFT JOIN sources s ON s.id = e.source_id
               WHERE el.claim_version_id = ?
               ORDER BY e.id, el.relation""",
            (version_id,),
        ).fetchall()
        support_groups: dict[str, float] = {}
        contradict_groups: dict[str, float] = {}
        invalid_hash_ids: list[str] = []
        missing_source_ids: list[str] = []
        items: list[dict[str, Any]] = []
        usable_count = 0
        for row in rows:
            item = dict(row)
            source_exists = item["resolved_source_id"] is not None
            hash_valid = evidence_hash(
                item["source_id"], item["excerpt"], item["locator"], item["strength"]
            ) == item["content_hash"]
            usable = source_exists and hash_valid
            effective_weight = (
                round(float(item["strength"]) * float(item["source_reliability"]), 6)
                if usable
                else None
            )
            group = independent_source_key(
                item["source_id"], item["source_publisher"], item["source_url"]
            )
            if usable:
                usable_count += 1
                target = support_groups if item["relation"] == "SUPPORTS" else contradict_groups
                target[group] = max(target.get(group, 0.0), effective_weight or 0.0)
            if not source_exists:
                missing_source_ids.append(item["evidence_id"])
            if not hash_valid:
                invalid_hash_ids.append(item["evidence_id"])
            items.append(
                {
                    "evidence_id": item["evidence_id"],
                    "source_id": item["source_id"],
                    "relation": item["relation"],
                    "content_hash": item["content_hash"],
                    "source_exists": source_exists,
                    "hash_valid": hash_valid,
                    "independent_source_key": group,
                    "effective_weight": effective_weight,
                }
            )
        return {
            "evidence_count": len(items),
            "usable_evidence_count": usable_count,
            "support_source_count": len(support_groups),
            "contradict_source_count": len(contradict_groups),
            "support_mass": round(self._independent_mass(list(support_groups.values())), 4),
            "contradict_mass": round(
                self._independent_mass(list(contradict_groups.values())), 4
            ),
            "integrity": {
                "valid": not invalid_hash_ids and not missing_source_ids,
                "invalid_hash_evidence_ids": sorted(invalid_hash_ids),
                "missing_source_evidence_ids": sorted(missing_source_ids),
            },
            "evidence": items,
        }

    def _bounded_relation_paths(
        self,
        connection: sqlite3.Connection,
        start_version_id: str,
        target_version_id: str,
    ) -> list[list[dict[str, Any]]]:
        queue: list[tuple[str, list[dict[str, Any]], tuple[str, ...]]] = [
            (start_version_id, [], (start_version_id,))
        ]
        found: list[list[dict[str, Any]]] = []
        cursor = 0
        while cursor < len(queue) and len(found) < self.CONTRADICTION_MAX_PATHS_PER_PAIR:
            version_id, path, path_nodes = queue[cursor]
            cursor += 1
            if len(path) >= self.CONTRADICTION_PATH_MAX_DEPTH:
                continue
            for relation_row in self._outgoing_relations(connection, version_id):
                edge = self._impact_edge(relation_row)
                next_id = edge["to_claim_version_id"]
                if next_id in path_nodes:
                    continue
                next_path = [*path, edge]
                if next_id == target_version_id:
                    if any(item["relation"] == "CONTRADICTS" for item in next_path):
                        found.append(next_path)
                    continue
                queue.append((next_id, next_path, (*path_nodes, next_id)))
        return found

    def create_contradiction_dossier(
        self, payload: ContradictionDossierCreate
    ) -> dict[str, Any]:
        selected_ids = sorted(payload.claim_version_ids)
        with self.database.connect() as connection:
            selected: list[dict[str, Any]] = []
            selected_by_id: dict[str, dict[str, Any]] = {}
            for version_id in selected_ids:
                version = self._claim_version_by_id(connection, version_id)
                current = self._claim_version(connection, version["claim_id"], None)
                side = {
                    "claim_version_id": version["id"],
                    "claim_id": version["claim_id"],
                    "version_number": version["version_number"],
                    "version_content_hash": self._version_content_hash(version),
                    "selection_state": "CURRENT" if current["id"] == version["id"] else "FROZEN",
                    "current_claim_version_id": current["id"],
                    "current_version_number": current["version_number"],
                    "current_version_content_hash": self._version_content_hash(current),
                    "evidence_summary": self._dossier_evidence_summary(connection, version["id"]),
                    "coverage_report": self._latest_coverage_summary(connection, version["id"]),
                }
                selected.append(side)
                selected_by_id[version_id] = side

            contradictory_pairs: list[dict[str, Any]] = []
            relation_contexts: list[dict[str, Any]] = []
            asymmetries: list[dict[str, Any]] = []

            for left_index, left_id in enumerate(selected_ids):
                for right_id in selected_ids[left_index + 1 :]:
                    direct_rows = connection.execute(
                        """SELECT id, from_claim_version_id, to_claim_version_id
                           FROM claim_relations
                           WHERE relation = 'CONTRADICTS'
                             AND ((from_claim_version_id = ? AND to_claim_version_id = ?)
                               OR (from_claim_version_id = ? AND to_claim_version_id = ?))
                           ORDER BY id""",
                        (left_id, right_id, right_id, left_id),
                    ).fetchall()
                    paths = [
                        *self._bounded_relation_paths(connection, left_id, right_id),
                        *self._bounded_relation_paths(connection, right_id, left_id),
                    ]
                    unique_paths: dict[str, list[dict[str, Any]]] = {}
                    for path in paths:
                        key = json.dumps(
                            path, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                        )
                        unique_paths[key] = path
                    ordered_paths = [
                        unique_paths[key]
                        for key in sorted(unique_paths)[
                            : self.CONTRADICTION_MAX_PATHS_PER_PAIR
                        ]
                    ]
                    context = {
                        "left_claim_version_id": left_id,
                        "right_claim_version_id": right_id,
                        "direct_explicit_contradiction": bool(direct_rows),
                        "contradiction_relation_ids": [row["id"] for row in direct_rows],
                        "bounded_paths": ordered_paths,
                    }
                    if direct_rows or ordered_paths:
                        relation_contexts.append(context)
                    if not direct_rows:
                        continue

                    left_evidence = selected_by_id[left_id]["evidence_summary"]
                    right_evidence = selected_by_id[right_id]["evidence_summary"]
                    left_sources = left_evidence["support_source_count"]
                    right_sources = right_evidence["support_source_count"]
                    left_mass = left_evidence["support_mass"]
                    right_mass = right_evidence["support_mass"]
                    source_gap = abs(left_sources - right_sources)
                    mass_gap = round(abs(left_mass - right_mass), 4)
                    if (left_sources, left_mass, left_id) >= (
                        right_sources,
                        right_mass,
                        right_id,
                    ):
                        stronger_id, weaker_id = left_id, right_id
                        stronger_sources = left_sources
                    else:
                        stronger_id, weaker_id = right_id, left_id
                        stronger_sources = right_sources
                    material_asymmetry = (
                        stronger_sources >= self.CONTRADICTION_MIN_SUPPORT_SOURCES_PER_SIDE
                        and (
                            source_gap >= self.CONTRADICTION_ASYMMETRY_MIN_SOURCE_GAP
                            or mass_gap >= self.CONTRADICTION_ASYMMETRY_MIN_MASS_GAP
                        )
                    )
                    pair = {
                        **context,
                        "both_sides_sufficiently_supported": (
                            left_sources >= self.CONTRADICTION_MIN_SUPPORT_SOURCES_PER_SIDE
                            and right_sources >= self.CONTRADICTION_MIN_SUPPORT_SOURCES_PER_SIDE
                        ),
                        "support_source_gap": source_gap,
                        "support_mass_gap": mass_gap,
                        "material_evidence_asymmetry": material_asymmetry,
                    }
                    contradictory_pairs.append(pair)
                    if material_asymmetry:
                        asymmetries.append(
                            {
                                "left_claim_version_id": left_id,
                                "right_claim_version_id": right_id,
                                "stronger_documented_side_id": stronger_id,
                                "weaker_documented_side_id": weaker_id,
                                "support_source_gap": source_gap,
                                "support_mass_gap": mass_gap,
                                "warning": (
                                    "Une asymétrie de preuves enregistrées ne prouve pas "
                                    "que le côté le plus documenté est vrai."
                                ),
                            }
                        )

            if not contradictory_pairs:
                qualification = "NO_CONTRADICTION"
                reasons = [
                    "Aucune relation directe CONTRADICTS ne relie les versions sélectionnées."
                ]
            elif asymmetries:
                qualification = "EVIDENCE_ASYMMETRY"
                reasons = [
                    "Au moins une paire présente une asymétrie documentaire dépassant les seuils fixes."
                ]
            elif all(
                pair["both_sides_sufficiently_supported"] for pair in contradictory_pairs
            ):
                qualification = "UNRESOLVED"
                reasons = [
                    "Les côtés contradictoires sont documentés, sans résolution de vérité par le système."
                ]
            else:
                qualification = "INSUFFICIENT"
                reasons = [
                    "Au moins un côté contradictoire dispose de moins de deux sources de soutien indépendantes."
                ]

            snapshot = {
                "claim_version_ids": selected_ids,
                "algorithm_version": self.CONTRADICTION_DOSSIER_ALGORITHM_VERSION,
                "qualification": qualification,
                "reasons": reasons,
                "selected_versions": selected,
                "contradictory_pairs": contradictory_pairs,
                "relation_contexts": relation_contexts,
                "evidence_asymmetries": asymmetries,
                "metrics": {
                    "selected_version_count": len(selected),
                    "current_version_count": sum(
                        item["selection_state"] == "CURRENT" for item in selected
                    ),
                    "frozen_version_count": sum(
                        item["selection_state"] == "FROZEN" for item in selected
                    ),
                    "contradictory_pair_count": len(contradictory_pairs),
                    "context_path_count": sum(
                        len(item["bounded_paths"]) for item in relation_contexts
                    ),
                    "evidence_asymmetry_count": len(asymmetries),
                },
                "policy": {
                    "claim_version_count_minimum": 2,
                    "claim_version_count_maximum": 20,
                    "path_maximum_depth": self.CONTRADICTION_PATH_MAX_DEPTH,
                    "maximum_paths_per_pair": self.CONTRADICTION_MAX_PATHS_PER_PAIR,
                    "minimum_support_sources_per_side": (
                        self.CONTRADICTION_MIN_SUPPORT_SOURCES_PER_SIDE
                    ),
                    "asymmetry_minimum_source_gap": (
                        self.CONTRADICTION_ASYMMETRY_MIN_SOURCE_GAP
                    ),
                    "asymmetry_minimum_support_mass_gap": (
                        self.CONTRADICTION_ASYMMETRY_MIN_MASS_GAP
                    ),
                    "indirect_paths_establish_contradiction": False,
                    "client_supplied_verdict_or_resolution": False,
                },
                "warning": (
                    "Ce dossier décrit des contradictions et des asymétries documentaires. "
                    "Une asymétrie de preuves ne prouve pas la vérité d'un côté; aucune "
                    "résolution factuelle n'est produite."
                ),
            }
            dossier_hash = canonical_hash(snapshot)
            existing = connection.execute(
                "SELECT * FROM contradiction_dossiers WHERE dossier_hash = ?",
                (dossier_hash,),
            ).fetchone()
            if existing:
                return self._serialize_contradiction_dossier(existing, replay=True)

            dossier_id = new_id()
            created_at = utc_now()
            try:
                connection.execute(
                    """INSERT INTO contradiction_dossiers
                       (id, claim_version_ids_json, selected_version_count,
                        algorithm_version, qualification, dossier_hash,
                        snapshot_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        dossier_id,
                        json.dumps(selected_ids, separators=(",", ":")),
                        len(selected_ids),
                        self.CONTRADICTION_DOSSIER_ALGORITHM_VERSION,
                        qualification,
                        dossier_hash,
                        json.dumps(
                            snapshot,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        created_at,
                    ),
                )
                connection.executemany(
                    """INSERT INTO contradiction_dossier_versions
                       (dossier_id, claim_version_id, ordinal) VALUES (?, ?, ?)""",
                    [
                        (dossier_id, version_id, ordinal)
                        for ordinal, version_id in enumerate(selected_ids)
                    ],
                )
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    "SELECT * FROM contradiction_dossiers WHERE dossier_hash = ?",
                    (dossier_hash,),
                ).fetchone()
                if existing:
                    return self._serialize_contradiction_dossier(existing, replay=True)
                raise
            connection.execute(
                """INSERT INTO audit_events
                   (id, event_type, subject_type, subject_id, payload_hash, created_at)
                   VALUES (?, 'CONTRADICTION_DOSSIER_CREATED',
                           'contradiction_dossier', ?, ?, ?)""",
                (new_id(), dossier_id, dossier_hash, created_at),
            )
            stored = connection.execute(
                "SELECT * FROM contradiction_dossiers WHERE id = ?", (dossier_id,)
            ).fetchone()
        return self._serialize_contradiction_dossier(stored)

    def get_contradiction_dossier(self, dossier_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM contradiction_dossiers WHERE id = ?", (dossier_id,)
            ).fetchone()
        if not row:
            raise NotFoundError("Dossier de contradiction introuvable")
        return self._serialize_contradiction_dossier(row)

    @staticmethod
    def _serialize_provenance_dossier(
        row: sqlite3.Row, *, replay: bool = False
    ) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot_json"])
        return {
            "id": row["id"],
            **snapshot,
            "dossier_hash": row["dossier_hash"],
            "created_at": row["created_at"],
            "idempotent_replay": replay,
        }

    @staticmethod
    def _provenance_identity(
        source_id: str, publisher: str | None, url: str | None
    ) -> tuple[str, str, str]:
        key = independent_source_key(source_id, publisher, url)
        prefix, identifier = key.split(":", 1)
        kind = {
            "publisher": "PUBLISHER",
            "host": "DOMAIN",
            "source": "SOURCE_ID",
        }[prefix]
        return key, kind, identifier

    def create_provenance_dossier(
        self, payload: ProvenanceDossierCreate
    ) -> dict[str, Any]:
        selected_ids = sorted(payload.claim_version_ids)
        with self.database.connect() as connection:
            selected_versions: list[dict[str, Any]] = []
            claim_coverage_by_id: dict[str, dict[str, Any]] = {}
            evidence_links: list[dict[str, Any]] = []
            source_groups: dict[str, dict[str, Any]] = {}
            content_occurrences: dict[str, dict[str, Any]] = {}
            missing_source_evidence_ids: set[str] = set()
            invalid_hash_evidence_ids: set[str] = set()

            for version_id in selected_ids:
                version = self._claim_version_by_id(connection, version_id)
                current = self._claim_version(connection, version["claim_id"], None)
                version_item = {
                    "claim_version_id": version["id"],
                    "claim_id": version["claim_id"],
                    "version_number": version["version_number"],
                    "version_content_hash": self._version_content_hash(version),
                    "selection_state": "CURRENT" if current["id"] == version["id"] else "FROZEN",
                    "current_claim_version_id": current["id"],
                    "current_version_number": current["version_number"],
                    "current_version_content_hash": self._version_content_hash(current),
                }
                selected_versions.append(version_item)
                claim_coverage_by_id[version_id] = {
                    **version_item,
                    "evidence_link_count": 0,
                    "usable_evidence_link_count": 0,
                    "unique_content_hashes": set(),
                    "source_group_keys": set(),
                    "coverage_report": self._latest_coverage_summary(connection, version_id),
                }

                rows = connection.execute(
                    """SELECT e.id AS evidence_id, e.source_id, e.excerpt, e.locator,
                              e.strength, e.content_hash, el.relation,
                              s.id AS resolved_source_id, s.url AS source_url,
                              s.publisher AS source_publisher
                       FROM evidence_links el
                       JOIN evidence e ON e.id = el.evidence_id
                       LEFT JOIN sources s ON s.id = e.source_id
                       WHERE el.claim_version_id = ?
                       ORDER BY e.id, el.relation""",
                    (version_id,),
                ).fetchall()

                for row in rows:
                    item = dict(row)
                    source_exists = item["resolved_source_id"] is not None
                    hash_valid = evidence_hash(
                        item["source_id"],
                        item["excerpt"],
                        item["locator"],
                        item["strength"],
                    ) == item["content_hash"]
                    usable = source_exists and hash_valid
                    group_key: str | None = None
                    provenance_kind: str | None = None
                    stable_identifier: str | None = None
                    if source_exists:
                        group_key, provenance_kind, stable_identifier = self._provenance_identity(
                            item["source_id"],
                            item["source_publisher"],
                            item["source_url"],
                        )
                    if not source_exists:
                        missing_source_evidence_ids.add(item["evidence_id"])
                    if not hash_valid:
                        invalid_hash_evidence_ids.add(item["evidence_id"])

                    occurrence = content_occurrences.setdefault(
                        canonical_hash({"excerpt": normalize_text(item["excerpt"])}),
                        {
                            "content_fingerprint": canonical_hash(
                                {"excerpt": normalize_text(item["excerpt"])}
                            ),
                            "stored_content_hashes": set(),
                            "evidence_ids": set(),
                            "claim_version_ids": set(),
                            "source_ids": set(),
                            "link_count": 0,
                        },
                    )
                    occurrence["evidence_ids"].add(item["evidence_id"])
                    occurrence["stored_content_hashes"].add(item["content_hash"])
                    occurrence["claim_version_ids"].add(version_id)
                    occurrence["source_ids"].add(item["source_id"])
                    occurrence["link_count"] += 1

                    coverage = claim_coverage_by_id[version_id]
                    coverage["evidence_link_count"] += 1
                    if usable and group_key is not None:
                        coverage["usable_evidence_link_count"] += 1
                        coverage["unique_content_hashes"].add(
                            occurrence["content_fingerprint"]
                        )
                        coverage["source_group_keys"].add(group_key)
                        group = source_groups.setdefault(
                            group_key,
                            {
                                "group_key": group_key,
                                "provenance_kind": provenance_kind,
                                "stable_identifier": stable_identifier,
                                "source_ids": set(),
                                "claim_version_ids": set(),
                                "content_hashes": set(),
                                "evidence_link_count": 0,
                            },
                        )
                        group["source_ids"].add(item["source_id"])
                        group["claim_version_ids"].add(version_id)
                        group["content_hashes"].add(
                            occurrence["content_fingerprint"]
                        )
                        group["evidence_link_count"] += 1

                    evidence_links.append(
                        {
                            "claim_version_id": version_id,
                            "evidence_id": item["evidence_id"],
                            "content_hash": item["content_hash"],
                            "content_fingerprint": occurrence["content_fingerprint"],
                            "relation": item["relation"],
                            "source_id": item["source_id"],
                            "source_exists": source_exists,
                            "hash_valid": hash_valid,
                            "usable": usable,
                            "source_group_key": group_key,
                            "provenance_kind": provenance_kind,
                        }
                    )

            unique_usable_content_hashes = {
                content_hash
                for group in source_groups.values()
                for content_hash in group["content_hashes"]
            }
            total_unique_usable_content = len(unique_usable_content_hashes)
            serialized_groups: list[dict[str, Any]] = []
            for group_key in sorted(source_groups):
                group = source_groups[group_key]
                unique_content_count = len(group["content_hashes"])
                serialized_groups.append(
                    {
                        "group_key": group_key,
                        "provenance_kind": group["provenance_kind"],
                        "stable_identifier": group["stable_identifier"],
                        "source_ids": sorted(group["source_ids"]),
                        "claim_version_ids": sorted(group["claim_version_ids"]),
                        "unique_content_fingerprints": sorted(group["content_hashes"]),
                        "unique_content_count": unique_content_count,
                        "evidence_link_count": group["evidence_link_count"],
                        "unique_content_share": round(
                            unique_content_count / total_unique_usable_content, 4
                        )
                        if total_unique_usable_content
                        else 0.0,
                    }
                )

            dominant_source = None
            if serialized_groups:
                dominant = sorted(
                    serialized_groups,
                    key=lambda group: (-group["unique_content_count"], group["group_key"]),
                )[0]
                dominant_source = {
                    "group_key": dominant["group_key"],
                    "provenance_kind": dominant["provenance_kind"],
                    "stable_identifier": dominant["stable_identifier"],
                    "unique_content_count": dominant["unique_content_count"],
                    "unique_content_share": dominant["unique_content_share"],
                }

            duplicate_content_groups: list[dict[str, Any]] = []
            for content_fingerprint in sorted(content_occurrences):
                occurrence = content_occurrences[content_fingerprint]
                if occurrence["link_count"] <= 1:
                    continue
                duplicate_content_groups.append(
                    {
                        "content_fingerprint": content_fingerprint,
                        "stored_content_hashes": sorted(
                            occurrence["stored_content_hashes"]
                        ),
                        "evidence_ids": sorted(occurrence["evidence_ids"]),
                        "claim_version_ids": sorted(occurrence["claim_version_ids"]),
                        "source_ids": sorted(occurrence["source_ids"]),
                        "link_count": occurrence["link_count"],
                        "duplicate_link_count": occurrence["link_count"] - 1,
                    }
                )

            claim_coverage: list[dict[str, Any]] = []
            for version_id in selected_ids:
                coverage = claim_coverage_by_id[version_id]
                claim_coverage.append(
                    {
                        key: value
                        for key, value in coverage.items()
                        if key not in {"unique_content_hashes", "source_group_keys"}
                    }
                    | {
                        "unique_content_count": len(coverage["unique_content_hashes"]),
                        "independent_source_count": len(coverage["source_group_keys"]),
                        "source_group_keys": sorted(coverage["source_group_keys"]),
                    }
                )

            independent_source_count = len(serialized_groups)
            dominant_share = (
                dominant_source["unique_content_share"] if dominant_source else 0.0
            )
            unknown_provenance_groups = [
                group["group_key"]
                for group in serialized_groups
                if group["provenance_kind"] == "SOURCE_ID"
            ]
            integrity_valid = not (
                missing_source_evidence_ids or invalid_hash_evidence_ids
            )
            if not integrity_valid or not total_unique_usable_content:
                qualification = "INSUFFICIENT"
                reasons = [
                    "Aucune preuve intègre exploitable n'est disponible ou l'intégrité est incomplète."
                ]
            elif unknown_provenance_groups:
                qualification = "UNKNOWN_PROVENANCE"
                reasons = [
                    "Au moins un groupe ne dispose ni d'un éditeur ni d'un domaine enregistré."
                ]
            elif independent_source_count < self.PROVENANCE_DIVERSE_MIN_INDEPENDENT_SOURCES:
                qualification = "INSUFFICIENT"
                reasons = ["Moins de trois groupes de sources indépendantes sont disponibles."]
            elif dominant_share > self.PROVENANCE_DIVERSE_MAX_DOMINANT_SHARE:
                qualification = "CONCENTRATED"
                reasons = ["La source dominante représente plus de 50 % du contenu unique."]
            else:
                qualification = "DIVERSE"
                reasons = [
                    "Au moins trois sources indépendantes sont présentes et aucune ne dépasse 50 %."
                ]

            snapshot = {
                "claim_version_ids": selected_ids,
                "algorithm_version": self.PROVENANCE_DOSSIER_ALGORITHM_VERSION,
                "qualification": qualification,
                "reasons": reasons,
                "selected_versions": selected_versions,
                "claim_coverage": claim_coverage,
                "source_groups": serialized_groups,
                "dominant_source": dominant_source,
                "duplicate_content_groups": duplicate_content_groups,
                "evidence_links": evidence_links,
                "integrity": {
                    "valid": integrity_valid,
                    "missing_source_evidence_ids": sorted(missing_source_evidence_ids),
                    "invalid_hash_evidence_ids": sorted(invalid_hash_evidence_ids),
                },
                "metrics": {
                    "selected_version_count": len(selected_versions),
                    "evidence_link_count": len(evidence_links),
                    "unique_usable_content_count": total_unique_usable_content,
                    "independent_source_count": independent_source_count,
                    "dominant_source_share": dominant_share,
                    "duplicate_content_group_count": len(duplicate_content_groups),
                    "duplicate_link_count": sum(
                        group["duplicate_link_count"]
                        for group in duplicate_content_groups
                    ),
                    "unknown_provenance_group_count": len(unknown_provenance_groups),
                },
                "policy": {
                    "claim_version_count_minimum": 1,
                    "claim_version_count_maximum": 20,
                    "diverse_minimum_independent_sources": (
                        self.PROVENANCE_DIVERSE_MIN_INDEPENDENT_SOURCES
                    ),
                    "diverse_maximum_dominant_source_share": (
                        self.PROVENANCE_DIVERSE_MAX_DOMINANT_SHARE
                    ),
                    "duplicate_links_inflate_unique_content": False,
                    "source_grouping_order": ["PUBLISHER", "DOMAIN", "SOURCE_ID"],
                    "client_supplied_score_verdict_or_provenance": False,
                },
                "warning": (
                    "La diversité et l'indépendance apparentes des sources enregistrées "
                    "ne prouvent pas la vérité des affirmations ni l'authenticité des sources."
                ),
            }
            dossier_hash = canonical_hash(snapshot)
            existing = connection.execute(
                "SELECT * FROM provenance_dossiers WHERE dossier_hash = ?",
                (dossier_hash,),
            ).fetchone()
            if existing:
                return self._serialize_provenance_dossier(existing, replay=True)

            dossier_id = new_id()
            created_at = utc_now()
            try:
                connection.execute(
                    """INSERT INTO provenance_dossiers
                       (id, claim_version_ids_json, selected_version_count,
                        algorithm_version, qualification, dossier_hash,
                        snapshot_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        dossier_id,
                        json.dumps(selected_ids, separators=(",", ":")),
                        len(selected_ids),
                        self.PROVENANCE_DOSSIER_ALGORITHM_VERSION,
                        qualification,
                        dossier_hash,
                        json.dumps(
                            snapshot,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        created_at,
                    ),
                )
                connection.executemany(
                    """INSERT INTO provenance_dossier_versions
                       (dossier_id, claim_version_id, ordinal) VALUES (?, ?, ?)""",
                    [
                        (dossier_id, version_id, ordinal)
                        for ordinal, version_id in enumerate(selected_ids)
                    ],
                )
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    "SELECT * FROM provenance_dossiers WHERE dossier_hash = ?",
                    (dossier_hash,),
                ).fetchone()
                if existing:
                    return self._serialize_provenance_dossier(existing, replay=True)
                raise
            connection.execute(
                """INSERT INTO audit_events
                   (id, event_type, subject_type, subject_id, payload_hash, created_at)
                   VALUES (?, 'PROVENANCE_DOSSIER_CREATED',
                           'provenance_dossier', ?, ?, ?)""",
                (new_id(), dossier_id, dossier_hash, created_at),
            )
            stored = connection.execute(
                "SELECT * FROM provenance_dossiers WHERE id = ?", (dossier_id,)
            ).fetchone()
        return self._serialize_provenance_dossier(stored)

    def get_provenance_dossier(self, dossier_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM provenance_dossiers WHERE id = ?", (dossier_id,)
            ).fetchone()
        if not row:
            raise NotFoundError("Dossier de provenance introuvable")
        return self._serialize_provenance_dossier(row)

    def list_provenance_dossiers(self, limit: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM provenance_dossiers"
            ).fetchone()[0]
            rows = connection.execute(
                """SELECT * FROM provenance_dossiers
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            snapshot = json.loads(row["snapshot_json"])
            items.append(
                {
                    "id": row["id"],
                    "claim_version_ids": snapshot["claim_version_ids"],
                    "qualification": snapshot["qualification"],
                    "metrics": snapshot["metrics"],
                    "dominant_source": snapshot["dominant_source"],
                    "dossier_hash": row["dossier_hash"],
                    "created_at": row["created_at"],
                }
            )
        return {"items": items, "count": len(items), "total": total, "limit": limit}

    @staticmethod
    def _serialize_trust_alignment_dossier(
        row: sqlite3.Row, *, replay: bool = False
    ) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot_json"])
        return {
            "id": row["id"],
            **snapshot,
            "dossier_hash": row["dossier_hash"],
            "created_at": row["created_at"],
            "idempotent_replay": replay,
        }

    @staticmethod
    def _latest_provenance_summary(
        connection: sqlite3.Connection, version_id: str
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """SELECT pd.* FROM provenance_dossiers pd
               JOIN provenance_dossier_versions pdv ON pdv.dossier_id = pd.id
               WHERE pdv.claim_version_id = ?
               ORDER BY pd.created_at DESC, pd.id DESC LIMIT 1""",
            (version_id,),
        ).fetchone()
        if not row:
            return None
        snapshot = json.loads(row["snapshot_json"])
        return {
            "id": row["id"],
            "dossier_hash": row["dossier_hash"],
            "qualification": snapshot["qualification"],
            "metrics": snapshot["metrics"],
            "dominant_source": snapshot["dominant_source"],
            "integrity": snapshot["integrity"],
        }

    def create_trust_alignment_dossier(
        self, payload: TrustAlignmentDossierCreate
    ) -> dict[str, Any]:
        selected_ids = sorted(payload.claim_version_ids)
        with self.database.connect() as connection:
            claim_measures: list[dict[str, Any]] = []
            for version_id in selected_ids:
                version = self._claim_version_by_id(connection, version_id)
                current = self._claim_version(connection, version["claim_id"], None)
                evidence = self._dossier_evidence_summary(connection, version_id)
                support_source_count = evidence["support_source_count"]
                diversity_factor = round(
                    min(1.0, support_source_count / self.TRUST_ALIGNMENT_DIVERSITY_TARGET),
                    4,
                )
                evidentiary_confidence = round(
                    evidence["support_mass"]
                    * (1.0 - evidence["contradict_mass"])
                    * diversity_factor,
                    4,
                )
                declared_confidence = version["declared_confidence"]
                confidence_gap = (
                    round(float(declared_confidence) - evidentiary_confidence, 4)
                    if declared_confidence is not None
                    else None
                )
                insufficiency_reasons: list[str] = []
                if declared_confidence is None:
                    insufficiency_reasons.append(
                        "La version ne contient aucune confiance déclarée explicite."
                    )
                if not evidence["integrity"]["valid"]:
                    insufficiency_reasons.append(
                        "Une ou plusieurs preuves sont manquantes ou ont une empreinte invalide."
                    )
                if support_source_count < self.TRUST_ALIGNMENT_MIN_SUPPORT_SOURCES:
                    insufficiency_reasons.append(
                        "Moins de deux sources indépendantes de soutien sont exploitables."
                    )
                claim_measures.append(
                    {
                        "claim_version_id": version["id"],
                        "claim_id": version["claim_id"],
                        "version_number": version["version_number"],
                        "version_content_hash": self._version_content_hash(version),
                        "selection_state": (
                            "CURRENT" if current["id"] == version["id"] else "FROZEN"
                        ),
                        "current_claim_version_id": current["id"],
                        "current_version_number": current["version_number"],
                        "declared_confidence": declared_confidence,
                        "evidentiary_confidence": evidentiary_confidence,
                        "confidence_gap": confidence_gap,
                        "absolute_gap": (
                            round(abs(confidence_gap), 4)
                            if confidence_gap is not None
                            else None
                        ),
                        "support_mass": evidence["support_mass"],
                        "contradict_mass": evidence["contradict_mass"],
                        "support_source_count": support_source_count,
                        "contradict_source_count": evidence["contradict_source_count"],
                        "evidence_count": evidence["evidence_count"],
                        "usable_evidence_count": evidence["usable_evidence_count"],
                        "diversity_factor": diversity_factor,
                        "evidence_integrity": evidence["integrity"],
                        "coverage_report": self._latest_coverage_summary(
                            connection, version_id
                        ),
                        "provenance_dossier": self._latest_provenance_summary(
                            connection, version_id
                        ),
                        "eligible_for_alignment": not insufficiency_reasons,
                        "insufficiency_reasons": insufficiency_reasons,
                    }
                )

            placeholders = ",".join("?" for _ in selected_ids)
            contradiction_rows = connection.execute(
                f"""SELECT id, from_claim_version_id, to_claim_version_id
                    FROM claim_relations
                    WHERE relation = 'CONTRADICTS'
                      AND from_claim_version_id IN ({placeholders})
                      AND to_claim_version_id IN ({placeholders})
                    ORDER BY id""",
                (*selected_ids, *selected_ids),
            ).fetchall()
            incompatible_pairs = [
                {
                    "relation_id": row["id"],
                    "from_claim_version_id": row["from_claim_version_id"],
                    "to_claim_version_id": row["to_claim_version_id"],
                }
                for row in contradiction_rows
            ]

            comparable = [
                item for item in claim_measures if item["confidence_gap"] is not None
            ]
            worst_claim = None
            if comparable:
                worst = sorted(
                    comparable,
                    key=lambda item: (-item["absolute_gap"], item["claim_version_id"]),
                )[0]
                worst_claim = {
                    "claim_version_id": worst["claim_version_id"],
                    "claim_id": worst["claim_id"],
                    "declared_confidence": worst["declared_confidence"],
                    "evidentiary_confidence": worst["evidentiary_confidence"],
                    "confidence_gap": worst["confidence_gap"],
                    "absolute_gap": worst["absolute_gap"],
                }

            if incompatible_pairs:
                qualification = "INCOMPATIBLE"
                reasons = [
                    "Au moins une relation directe CONTRADICTS relie les versions sélectionnées."
                ]
            elif any(not item["eligible_for_alignment"] for item in claim_measures):
                qualification = "INSUFFICIENT"
                reasons = [
                    "Une confiance déclarée ou une base probante suffisante manque pour au moins une version."
                ]
            elif worst_claim is None:
                qualification = "INSUFFICIENT"
                reasons = ["Aucun écart de confiance comparable ne peut être calculé."]
            elif worst_claim["absolute_gap"] <= self.TRUST_ALIGNMENT_TOLERANCE:
                qualification = "ALIGNED"
                reasons = [
                    "Tous les écarts absolus restent dans la tolérance fixe de 0,10."
                ]
            elif worst_claim["confidence_gap"] > 0:
                qualification = "OVERCONFIDENT"
                reasons = [
                    "Le pire écart montre une confiance déclarée supérieure à la confiance probante."
                ]
            else:
                qualification = "UNDERCONFIDENT"
                reasons = [
                    "Le pire écart montre une confiance déclarée inférieure à la confiance probante."
                ]

            snapshot = {
                "claim_version_ids": selected_ids,
                "algorithm_version": self.TRUST_ALIGNMENT_ALGORITHM_VERSION,
                "qualification": qualification,
                "reasons": reasons,
                "claim_measures": claim_measures,
                "incompatible_pairs": incompatible_pairs,
                "worst_claim": worst_claim,
                "metrics": {
                    "selected_version_count": len(selected_ids),
                    "comparable_claim_count": len(comparable),
                    "eligible_claim_count": sum(
                        item["eligible_for_alignment"] for item in claim_measures
                    ),
                    "incompatible_pair_count": len(incompatible_pairs),
                    "maximum_absolute_gap": (
                        worst_claim["absolute_gap"] if worst_claim else None
                    ),
                    "mean_support_mass": round(
                        sum(item["support_mass"] for item in claim_measures)
                        / len(claim_measures),
                        4,
                    ),
                    "mean_contradict_mass": round(
                        sum(item["contradict_mass"] for item in claim_measures)
                        / len(claim_measures),
                        4,
                    ),
                    "coverage_report_count": sum(
                        item["coverage_report"] is not None for item in claim_measures
                    ),
                    "provenance_dossier_count": sum(
                        item["provenance_dossier"] is not None for item in claim_measures
                    ),
                },
                "policy": {
                    "claim_version_count_minimum": 1,
                    "claim_version_count_maximum": 20,
                    "alignment_absolute_tolerance": self.TRUST_ALIGNMENT_TOLERANCE,
                    "minimum_support_sources": self.TRUST_ALIGNMENT_MIN_SUPPORT_SOURCES,
                    "diversity_target_sources": self.TRUST_ALIGNMENT_DIVERSITY_TARGET,
                    "evidentiary_confidence_formula": (
                        "support_mass * (1 - contradict_mass) * diversity_factor"
                    ),
                    "diversity_factor_formula": "min(1, support_source_count / 3)",
                    "direct_contradiction_is_incompatible": True,
                    "missing_declared_confidence_is_insufficient": True,
                    "client_supplied_result": False,
                },
                "warning": (
                    "La confiance déclarée et la confiance probante sont des mesures "
                    "documentaires; leur alignement ne prouve pas la vérité de l'affirmation."
                ),
            }
            dossier_hash = canonical_hash(snapshot)
            existing = connection.execute(
                "SELECT * FROM trust_alignment_dossiers WHERE dossier_hash = ?",
                (dossier_hash,),
            ).fetchone()
            if existing:
                return self._serialize_trust_alignment_dossier(existing, replay=True)

            dossier_id = new_id()
            created_at = utc_now()
            try:
                connection.execute(
                    """INSERT INTO trust_alignment_dossiers
                       (id, claim_version_ids_json, selected_version_count,
                        algorithm_version, qualification, dossier_hash,
                        snapshot_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        dossier_id,
                        json.dumps(selected_ids, separators=(",", ":")),
                        len(selected_ids),
                        self.TRUST_ALIGNMENT_ALGORITHM_VERSION,
                        qualification,
                        dossier_hash,
                        json.dumps(
                            snapshot,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        created_at,
                    ),
                )
                connection.executemany(
                    """INSERT INTO trust_alignment_dossier_versions
                       (dossier_id, claim_version_id, ordinal) VALUES (?, ?, ?)""",
                    [
                        (dossier_id, version_id, ordinal)
                        for ordinal, version_id in enumerate(selected_ids)
                    ],
                )
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    "SELECT * FROM trust_alignment_dossiers WHERE dossier_hash = ?",
                    (dossier_hash,),
                ).fetchone()
                if existing:
                    return self._serialize_trust_alignment_dossier(existing, replay=True)
                raise
            connection.execute(
                """INSERT INTO audit_events
                   (id, event_type, subject_type, subject_id, payload_hash, created_at)
                   VALUES (?, 'TRUST_ALIGNMENT_DOSSIER_CREATED',
                           'trust_alignment_dossier', ?, ?, ?)""",
                (new_id(), dossier_id, dossier_hash, created_at),
            )
            stored = connection.execute(
                "SELECT * FROM trust_alignment_dossiers WHERE id = ?", (dossier_id,)
            ).fetchone()
        return self._serialize_trust_alignment_dossier(stored)

    def get_trust_alignment_dossier(self, dossier_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM trust_alignment_dossiers WHERE id = ?", (dossier_id,)
            ).fetchone()
        if not row:
            raise NotFoundError("Dossier d'alignement de confiance introuvable")
        return self._serialize_trust_alignment_dossier(row)

    def list_trust_alignment_dossiers(self, limit: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM trust_alignment_dossiers"
            ).fetchone()[0]
            rows = connection.execute(
                """SELECT * FROM trust_alignment_dossiers
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            snapshot = json.loads(row["snapshot_json"])
            items.append(
                {
                    "id": row["id"],
                    "claim_version_ids": snapshot["claim_version_ids"],
                    "qualification": snapshot["qualification"],
                    "worst_claim": snapshot["worst_claim"],
                    "metrics": snapshot["metrics"],
                    "dossier_hash": row["dossier_hash"],
                    "created_at": row["created_at"],
                }
            )
        return {"items": items, "count": len(items), "total": total, "limit": limit}

    @staticmethod
    def _serialize_claim_evolution_dossier(
        row: sqlite3.Row, *, replay: bool = False
    ) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot_json"])
        return {
            "id": row["id"],
            **snapshot,
            "dossier_hash": row["dossier_hash"],
            "created_at": row["created_at"],
            "idempotent_replay": replay,
        }

    def _evidence_direction(self, support_mass: float, contradict_mass: float) -> str:
        balance = support_mass - contradict_mass
        if balance >= self.CLAIM_EVOLUTION_DIRECTION_DEADBAND:
            return "SUPPORTING"
        if balance <= -self.CLAIM_EVOLUTION_DIRECTION_DEADBAND:
            return "CONTRADICTING"
        return "BALANCED"

    def create_claim_evolution_dossier(
        self, payload: ClaimEvolutionDossierCreate
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            versions = [
                self._claim_version_by_id(connection, version_id)
                for version_id in payload.claim_version_ids
            ]
            versions.sort(
                key=lambda row: (row["version_number"], row["created_at"], row["id"])
            )
            claim_ids = sorted({row["claim_id"] for row in versions})
            compatible = len(claim_ids) == 1
            measures: list[dict[str, Any]] = []
            for version in versions:
                evidence = self._dossier_evidence_summary(connection, version["id"])
                diversity_factor = round(
                    min(
                        1.0,
                        evidence["support_source_count"]
                        / self.TRUST_ALIGNMENT_DIVERSITY_TARGET,
                    ),
                    4,
                )
                evidentiary_confidence = round(
                    evidence["support_mass"]
                    * (1.0 - evidence["contradict_mass"])
                    * diversity_factor,
                    4,
                )
                declared = version["declared_confidence"]
                gap = (
                    round(float(declared) - evidentiary_confidence, 4)
                    if declared is not None
                    else None
                )
                if gap is None:
                    alignment = "UNDECLARED"
                elif abs(gap) <= self.TRUST_ALIGNMENT_TOLERANCE:
                    alignment = "ALIGNED"
                elif gap > 0:
                    alignment = "OVERCONFIDENT"
                else:
                    alignment = "UNDERCONFIDENT"
                relation_rows = connection.execute(
                    """SELECT id, from_claim_version_id, to_claim_version_id, created_at
                       FROM claim_relations
                       WHERE relation = 'CONTRADICTS'
                         AND (from_claim_version_id = ? OR to_claim_version_id = ?)
                       ORDER BY created_at, id""",
                    (version["id"], version["id"]),
                ).fetchall()
                coverage = self._latest_coverage_summary(connection, version["id"])
                measures.append(
                    {
                        "claim_version_id": version["id"],
                        "claim_id": version["claim_id"],
                        "version_number": version["version_number"],
                        "created_at": version["created_at"],
                        "version_content_hash": self._version_content_hash(version),
                        "declared_confidence": declared,
                        "evidentiary_confidence": evidentiary_confidence,
                        "confidence_gap": gap,
                        "alignment": alignment,
                        "support_mass": evidence["support_mass"],
                        "contradict_mass": evidence["contradict_mass"],
                        "support_source_count": evidence["support_source_count"],
                        "contradict_source_count": evidence["contradict_source_count"],
                        "evidence_count": evidence["evidence_count"],
                        "usable_evidence_count": evidence["usable_evidence_count"],
                        "evidence_direction": self._evidence_direction(
                            evidence["support_mass"], evidence["contradict_mass"]
                        ),
                        "evidence_integrity": evidence["integrity"],
                        "evidence": evidence["evidence"],
                        "explicit_contradictions": [dict(row) for row in relation_rows],
                        "explicit_contradiction_count": len(relation_rows),
                        "coverage_report": coverage,
                    }
                )

            transitions: list[dict[str, Any]] = []
            direction_rank = {"SUPPORTING": 0, "BALANCED": 1, "CONTRADICTING": 2}
            for previous, current in zip(measures, measures[1:]):
                contradiction_delta = round(
                    current["contradict_mass"] - previous["contradict_mass"], 4
                )
                support_delta = round(
                    current["support_mass"] - previous["support_mass"], 4
                )
                confidence_delta = round(
                    current["evidentiary_confidence"]
                    - previous["evidentiary_confidence"],
                    4,
                )
                declared_delta = (
                    round(
                        float(current["declared_confidence"])
                        - float(previous["declared_confidence"]),
                        4,
                    )
                    if current["declared_confidence"] is not None
                    and previous["declared_confidence"] is not None
                    else None
                )
                explicit_delta = (
                    current["explicit_contradiction_count"]
                    - previous["explicit_contradiction_count"]
                )
                rank_delta = (
                    direction_rank[current["evidence_direction"]]
                    - direction_rank[previous["evidence_direction"]]
                )
                escalating = (
                    contradiction_delta >= self.CLAIM_EVOLUTION_MATERIAL_DELTA
                    or confidence_delta <= -self.CLAIM_EVOLUTION_MATERIAL_DELTA
                    or explicit_delta > 0
                    or rank_delta > 0
                )
                resolving = (
                    contradiction_delta <= -self.CLAIM_EVOLUTION_MATERIAL_DELTA
                    or confidence_delta >= self.CLAIM_EVOLUTION_MATERIAL_DELTA
                    or explicit_delta < 0
                    or rank_delta < 0
                )
                risk_delta = round(contradiction_delta - confidence_delta, 4)
                transitions.append(
                    {
                        "from_claim_version_id": previous["claim_version_id"],
                        "to_claim_version_id": current["claim_version_id"],
                        "from_version_number": previous["version_number"],
                        "to_version_number": current["version_number"],
                        "support_delta": support_delta,
                        "contradiction_delta": contradiction_delta,
                        "evidentiary_confidence_delta": confidence_delta,
                        "declared_confidence_delta": declared_delta,
                        "explicit_contradiction_count_delta": explicit_delta,
                        "from_evidence_direction": previous["evidence_direction"],
                        "to_evidence_direction": current["evidence_direction"],
                        "direction_changed": rank_delta != 0,
                        "from_coverage_qualification": (
                            previous["coverage_report"]["qualification"]
                            if previous["coverage_report"] else None
                        ),
                        "to_coverage_qualification": (
                            current["coverage_report"]["qualification"]
                            if current["coverage_report"] else None
                        ),
                        "coverage_changed": (
                            (previous["coverage_report"] or {}).get("qualification")
                            != (current["coverage_report"] or {}).get("qualification")
                        ),
                        "alignment_changed": previous["alignment"] != current["alignment"],
                        "risk_delta": risk_delta,
                        "escalating": escalating,
                        "resolving": resolving,
                    }
                )

            worst_transition = (
                sorted(
                    transitions,
                    key=lambda item: (-item["risk_delta"], item["from_version_number"], item["to_version_number"]),
                )[0]
                if transitions
                else None
            )
            insufficient = any(
                not item["evidence_integrity"]["valid"]
                or item["usable_evidence_count"] == 0
                for item in measures
            )
            if not compatible:
                qualification = "INCOMPATIBLE"
                reasons = ["Les versions sélectionnées n'appartiennent pas à la même affirmation."]
            elif insufficient:
                qualification = "INSUFFICIENT"
                reasons = ["Au moins une version ne dispose pas de preuve intègre exploitable."]
            elif any(item["escalating"] for item in transitions):
                qualification = "ESCALATING"
                reasons = ["Au moins une transition augmente matériellement le risque documentaire."]
            elif any(item["resolving"] for item in transitions):
                qualification = "RESOLVING"
                reasons = ["Sans escalade concurrente, au moins une transition réduit le risque documentaire."]
            else:
                qualification = "STABLE"
                reasons = ["Aucune transition ne franchit les seuils fixes d'évolution."]

            ordered_ids = [item["claim_version_id"] for item in measures]
            snapshot = {
                "claim_version_ids": ordered_ids,
                "claim_id": claim_ids[0] if compatible else None,
                "algorithm_version": self.CLAIM_EVOLUTION_ALGORITHM_VERSION,
                "qualification": qualification,
                "reasons": reasons,
                "versions": measures,
                "transitions": transitions,
                "worst_transition": worst_transition,
                "metrics": {
                    "selected_version_count": len(measures),
                    "transition_count": len(transitions),
                    "escalating_transition_count": sum(item["escalating"] for item in transitions),
                    "resolving_transition_count": sum(item["resolving"] for item in transitions),
                    "coverage_report_count": sum(item["coverage_report"] is not None for item in measures),
                    "maximum_contradict_mass": max(item["contradict_mass"] for item in measures),
                    "minimum_evidentiary_confidence": min(item["evidentiary_confidence"] for item in measures),
                },
                "policy": {
                    "claim_version_count_minimum": 2,
                    "claim_version_count_maximum": 50,
                    "material_absolute_delta": self.CLAIM_EVOLUTION_MATERIAL_DELTA,
                    "direction_deadband": self.CLAIM_EVOLUTION_DIRECTION_DEADBAND,
                    "ordering": ["version_number", "created_at", "claim_version_id"],
                    "escalating_priority_over_resolving": True,
                    "missing_declared_confidence_is_invented": False,
                    "client_supplied_result": False,
                },
                "warning": (
                    "Ce dossier décrit une évolution documentaire chronologique; "
                    "il ne constitue ni verdict factuel, ni preuve de vérité."
                ),
            }
            dossier_hash = canonical_hash(snapshot)
            existing = connection.execute(
                "SELECT * FROM claim_evolution_dossiers WHERE dossier_hash = ?",
                (dossier_hash,),
            ).fetchone()
            if existing:
                return self._serialize_claim_evolution_dossier(existing, replay=True)
            dossier_id, created_at = new_id(), utc_now()
            connection.execute(
                """INSERT INTO claim_evolution_dossiers
                   (id, claim_version_ids_json, claim_id, selected_version_count,
                    algorithm_version, qualification, dossier_hash, snapshot_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    dossier_id,
                    json.dumps(ordered_ids, separators=(",", ":")),
                    claim_ids[0] if compatible else None,
                    len(ordered_ids),
                    self.CLAIM_EVOLUTION_ALGORITHM_VERSION,
                    qualification,
                    dossier_hash,
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    created_at,
                ),
            )
            connection.executemany(
                """INSERT INTO claim_evolution_dossier_versions
                   (dossier_id, claim_version_id, ordinal) VALUES (?, ?, ?)""",
                [(dossier_id, version_id, ordinal) for ordinal, version_id in enumerate(ordered_ids)],
            )
            connection.execute(
                """INSERT INTO audit_events
                   (id, event_type, subject_type, subject_id, payload_hash, created_at)
                   VALUES (?, 'CLAIM_EVOLUTION_DOSSIER_CREATED',
                           'claim_evolution_dossier', ?, ?, ?)""",
                (new_id(), dossier_id, dossier_hash, created_at),
            )
            stored = connection.execute(
                "SELECT * FROM claim_evolution_dossiers WHERE id = ?", (dossier_id,)
            ).fetchone()
        return self._serialize_claim_evolution_dossier(stored)

    def get_claim_evolution_dossier(self, dossier_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM claim_evolution_dossiers WHERE id = ?", (dossier_id,)
            ).fetchone()
        if not row:
            raise NotFoundError("Dossier d'évolution d'affirmation introuvable")
        return self._serialize_claim_evolution_dossier(row)

    def list_claim_evolution_dossiers(self, limit: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM claim_evolution_dossiers"
            ).fetchone()[0]
            rows = connection.execute(
                """SELECT * FROM claim_evolution_dossiers
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        items = []
        for row in rows:
            snapshot = json.loads(row["snapshot_json"])
            items.append({
                "id": row["id"],
                "claim_version_ids": snapshot["claim_version_ids"],
                "claim_id": snapshot["claim_id"],
                "qualification": snapshot["qualification"],
                "worst_transition": snapshot["worst_transition"],
                "metrics": snapshot["metrics"],
                "dossier_hash": row["dossier_hash"],
                "created_at": row["created_at"],
            })
        return {"items": items, "count": len(items), "total": total, "limit": limit}

    @staticmethod
    def _serialize_evidence_gap_dossier(
        row: sqlite3.Row, *, replay: bool = False
    ) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot_json"])
        return {
            "id": row["id"],
            **snapshot,
            "dossier_hash": row["dossier_hash"],
            "created_at": row["created_at"],
            "idempotent_replay": replay,
        }

    def create_evidence_gap_dossier(
        self, payload: EvidenceGapDossierCreate
    ) -> dict[str, Any]:
        selected_evidence_ids = sorted(payload.evidence_ids)
        with self.database.connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM claims WHERE id = ?", (payload.claim_id,)
            ).fetchone():
                raise NotFoundError("Affirmation introuvable")
            versions = [
                self._claim_version_by_id(connection, version_id)
                for version_id in payload.claim_version_ids
            ]
            versions.sort(
                key=lambda row: (row["version_number"], row["created_at"], row["id"])
            )
            selected_version_ids = [row["id"] for row in versions]
            compatible_versions = all(
                row["claim_id"] == payload.claim_id for row in versions
            )
            missing_evidence_ids: list[str] = []
            incompatible_evidence_ids: list[str] = []
            if selected_evidence_ids:
                placeholders = ",".join("?" for _ in selected_evidence_ids)
                existing = {
                    row["id"]
                    for row in connection.execute(
                        f"SELECT id FROM evidence WHERE id IN ({placeholders})",
                        selected_evidence_ids,
                    ).fetchall()
                }
                missing_evidence_ids = sorted(set(selected_evidence_ids) - existing)
                if missing_evidence_ids:
                    raise NotFoundError(
                        f"Élément probant introuvable: {missing_evidence_ids[0]}"
                    )
                version_placeholders = ",".join("?" for _ in selected_version_ids)
                linked = {
                    row["evidence_id"]
                    for row in connection.execute(
                        f"""SELECT DISTINCT evidence_id FROM evidence_links
                            WHERE evidence_id IN ({placeholders})
                              AND claim_version_id IN ({version_placeholders})""",
                        (*selected_evidence_ids, *selected_version_ids),
                    ).fetchall()
                }
                incompatible_evidence_ids = sorted(set(selected_evidence_ids) - linked)

            measures: list[dict[str, Any]] = []
            selected_version_set = set(selected_version_ids)
            for version in versions:
                parameters: list[Any] = [version["id"]]
                evidence_filter = ""
                if selected_evidence_ids:
                    placeholders = ",".join("?" for _ in selected_evidence_ids)
                    evidence_filter = f" AND e.id IN ({placeholders})"
                    parameters.extend(selected_evidence_ids)
                rows = connection.execute(
                    f"""SELECT e.id AS evidence_id, e.source_id, e.excerpt, e.locator,
                               e.strength, e.content_hash, el.relation, el.id AS link_id,
                               s.id AS resolved_source_id, s.url AS source_url,
                               s.publisher AS source_publisher,
                               s.published_at AS source_published_at,
                               s.reliability AS source_reliability
                        FROM evidence_links el
                        JOIN evidence e ON e.id = el.evidence_id
                        LEFT JOIN sources s ON s.id = e.source_id
                        WHERE el.claim_version_id = ?{evidence_filter}
                        ORDER BY e.id, el.relation, el.id""",
                    parameters,
                ).fetchall()
                reference_date = datetime.fromisoformat(
                    version["created_at"].replace("Z", "+00:00")
                ).date()
                evidence_items: list[dict[str, Any]] = []
                witnesses: list[dict[str, Any]] = []
                gap_codes: set[str] = set()
                support_ids: list[str] = []
                contradict_ids: list[str] = []
                missing_source_ids: list[str] = []
                stale_source_ids: list[str] = []
                invalid_hash_ids: list[str] = []
                usable_count = 0
                for row in rows:
                    item = dict(row)
                    source_exists = item["resolved_source_id"] is not None
                    hash_valid = evidence_hash(
                        item["source_id"], item["excerpt"], item["locator"], item["strength"]
                    ) == item["content_hash"]
                    published = parse_published_date(item["source_published_at"])
                    stale = bool(
                        published
                        and reference_date - published
                        > timedelta(days=self.EVIDENCE_GAP_STALE_DAYS)
                    )
                    usable = source_exists and hash_valid
                    if usable:
                        usable_count += 1
                    if item["relation"] == "SUPPORTS" and usable:
                        support_ids.append(item["evidence_id"])
                    if item["relation"] == "CONTRADICTS" and usable:
                        contradict_ids.append(item["evidence_id"])
                    if not source_exists:
                        missing_source_ids.append(item["evidence_id"])
                    if stale:
                        stale_source_ids.append(item["source_id"])
                    if not hash_valid:
                        invalid_hash_ids.append(item["evidence_id"])
                    evidence_items.append({
                        "evidence_id": item["evidence_id"],
                        "link_id": item["link_id"],
                        "source_id": item["source_id"],
                        "relation": item["relation"],
                        "content_hash": item["content_hash"],
                        "recalculated_hash": evidence_hash(
                            item["source_id"], item["excerpt"], item["locator"], item["strength"]
                        ),
                        "hash_valid": hash_valid,
                        "source_exists": source_exists,
                        "source_published_at": item["source_published_at"],
                        "source_stale": stale,
                        "source_accessibility_state": "UNKNOWN_NOT_PERSISTED",
                        "usable": usable,
                    })
                if not rows:
                    gap_codes.add("MISSING_EVIDENCE")
                    witnesses.append({"code": "MISSING_EVIDENCE", "claim_version_id": version["id"]})
                if not support_ids and rows:
                    gap_codes.add("NO_SUPPORTING_EVIDENCE")
                    witnesses.append({"code": "NO_SUPPORTING_EVIDENCE", "claim_version_id": version["id"]})
                if contradict_ids:
                    gap_codes.add("CONTRADICTORY_EVIDENCE")
                    witnesses.append({"code": "CONTRADICTORY_EVIDENCE", "evidence_ids": sorted(contradict_ids)})
                if missing_source_ids:
                    gap_codes.add("MISSING_SOURCE")
                    witnesses.append({"code": "MISSING_SOURCE", "evidence_ids": sorted(missing_source_ids)})
                if stale_source_ids:
                    gap_codes.add("STALE_SOURCE")
                    witnesses.append({"code": "STALE_SOURCE", "source_ids": sorted(set(stale_source_ids))})
                if invalid_hash_ids:
                    gap_codes.add("INVALID_EVIDENCE_HASH")
                    witnesses.append({"code": "INVALID_EVIDENCE_HASH", "evidence_ids": sorted(invalid_hash_ids)})
                explicit = [
                    dict(row)
                    for row in connection.execute(
                        """SELECT id, from_claim_version_id, to_claim_version_id, created_at
                           FROM claim_relations
                           WHERE relation = 'CONTRADICTS'
                             AND (from_claim_version_id = ? OR to_claim_version_id = ?)
                           ORDER BY created_at, id""",
                        (version["id"], version["id"]),
                    ).fetchall()
                ]
                if explicit:
                    gap_codes.add("EXPLICIT_CONTRADICTION")
                    witnesses.append({"code": "EXPLICIT_CONTRADICTION", "relation_ids": [row["id"] for row in explicit]})
                dependencies = [
                    dict(row)
                    for row in connection.execute(
                        """SELECT id, from_claim_version_id, to_claim_version_id, created_at
                           FROM claim_relations
                           WHERE relation = 'SUPPORTS' AND from_claim_version_id = ?
                           ORDER BY created_at, id""",
                        (version["id"],),
                    ).fetchall()
                    if row["to_claim_version_id"] not in selected_version_set
                ]
                if dependencies:
                    gap_codes.add("MISSING_DEPENDENCY")
                    witnesses.append({
                        "code": "MISSING_DEPENDENCY",
                        "relation_ids": [row["id"] for row in dependencies],
                        "missing_claim_version_ids": sorted({row["to_claim_version_id"] for row in dependencies}),
                    })
                measures.append({
                    "claim_version_id": version["id"],
                    "claim_id": version["claim_id"],
                    "version_number": version["version_number"],
                    "created_at": version["created_at"],
                    "version_content_hash": self._version_content_hash(version),
                    "reference_date": reference_date.isoformat(),
                    "evidence_count": len(rows),
                    "usable_evidence_count": usable_count,
                    "supported_evidence_ids": sorted(support_ids),
                    "contradicted_evidence_ids": sorted(contradict_ids),
                    "missing_source_evidence_ids": sorted(missing_source_ids),
                    "stale_source_ids": sorted(set(stale_source_ids)),
                    "inaccessible_source_ids": [],
                    "invalid_hash_evidence_ids": sorted(invalid_hash_ids),
                    "missing_dependencies": dependencies,
                    "explicit_contradictions": explicit,
                    "coverage_report": self._latest_coverage_summary(connection, version["id"]),
                    "gap_codes": sorted(gap_codes),
                    "gap_count": len(gap_codes),
                    "witnesses": witnesses,
                    "evidence": evidence_items,
                })

            transitions: list[dict[str, Any]] = []
            for previous, current in zip(measures, measures[1:]):
                previous_gaps = set(previous["gap_codes"])
                current_gaps = set(current["gap_codes"])
                transitions.append({
                    "from_claim_version_id": previous["claim_version_id"],
                    "to_claim_version_id": current["claim_version_id"],
                    "from_version_number": previous["version_number"],
                    "to_version_number": current["version_number"],
                    "new_gaps": sorted(current_gaps - previous_gaps),
                    "resolved_gaps": sorted(previous_gaps - current_gaps),
                    "persistent_gaps": sorted(previous_gaps & current_gaps),
                    "gap_count_delta": current["gap_count"] - previous["gap_count"],
                    "from_gap_count": previous["gap_count"],
                    "to_gap_count": current["gap_count"],
                    "witnesses": current["witnesses"],
                })
            worst_transition = (
                sorted(
                    transitions,
                    key=lambda item: (-item["to_gap_count"], -len(item["new_gaps"]), item["to_version_number"]),
                )[0]
                if transitions else None
            )
            incompatible = not compatible_versions or bool(incompatible_evidence_ids)
            insufficient = any(
                item["evidence_count"] == 0
                or item["usable_evidence_count"] == 0
                or item["missing_source_evidence_ids"]
                or item["invalid_hash_evidence_ids"]
                for item in measures
            )
            if incompatible:
                qualification = "INCOMPATIBLE"
                reasons = ["Les références ne correspondent pas toutes au claim et aux versions sélectionnés."]
            elif insufficient:
                qualification = "INSUFFICIENT"
                reasons = ["Au moins une version ne possède pas de preuve intègre et sourcée exploitable."]
            elif any(item["gap_codes"] for item in measures):
                qualification = "GAPPED"
                reasons = ["Au moins une lacune documentaire persistée a été détectée."]
            else:
                qualification = "COVERED"
                reasons = ["Chaque version sélectionnée possède un soutien sourcé sans lacune détectée."]
            snapshot = {
                "claim_id": payload.claim_id,
                "claim_version_ids": selected_version_ids,
                "evidence_ids": selected_evidence_ids,
                "algorithm_version": self.EVIDENCE_GAP_ALGORITHM_VERSION,
                "qualification": qualification,
                "reasons": reasons,
                "versions": measures,
                "transitions": transitions,
                "worst_transition": worst_transition,
                "incompatible_evidence_ids": incompatible_evidence_ids,
                "metrics": {
                    "reference_count": len(selected_version_ids) + len(selected_evidence_ids),
                    "selected_version_count": len(selected_version_ids),
                    "selected_evidence_count": len(selected_evidence_ids),
                    "supported_version_count": sum(bool(item["supported_evidence_ids"]) for item in measures),
                    "contradicted_version_count": sum(bool(item["contradicted_evidence_ids"] or item["explicit_contradictions"]) for item in measures),
                    "unsourced_version_count": sum(item["usable_evidence_count"] == 0 for item in measures),
                    "stale_source_count": len({source_id for item in measures for source_id in item["stale_source_ids"]}),
                    "inaccessible_source_count": 0,
                    "missing_dependency_count": sum(len(item["missing_dependencies"]) for item in measures),
                    "total_gap_count": sum(item["gap_count"] for item in measures),
                    "new_gap_count": sum(len(item["new_gaps"]) for item in transitions),
                    "resolved_gap_count": sum(len(item["resolved_gaps"]) for item in transitions),
                },
                "policy": {
                    "reference_count_minimum": 2,
                    "reference_count_maximum": 100,
                    "stale_after_days": self.EVIDENCE_GAP_STALE_DAYS,
                    "staleness_reference": "claim_version_created_at",
                    "source_accessibility": "UNKNOWN_WHEN_NOT_PERSISTED",
                    "covered_proves_truth": False,
                    "client_supplied_result": False,
                },
                "warning": (
                    "COVERED décrit seulement la présence de preuves sourcées selon ces règles; "
                    "cela ne prouve ni la vérité ni la causalité."
                ),
            }
            dossier_hash = canonical_hash(snapshot)
            existing = connection.execute(
                "SELECT * FROM evidence_gap_dossiers WHERE dossier_hash = ?", (dossier_hash,)
            ).fetchone()
            if existing:
                return self._serialize_evidence_gap_dossier(existing, replay=True)
            dossier_id, created_at = new_id(), utc_now()
            connection.execute(
                """INSERT INTO evidence_gap_dossiers
                   (id, claim_id, claim_version_ids_json, evidence_ids_json,
                    reference_count, algorithm_version, qualification,
                    dossier_hash, snapshot_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    dossier_id, payload.claim_id,
                    json.dumps(selected_version_ids, separators=(",", ":")),
                    json.dumps(selected_evidence_ids, separators=(",", ":")),
                    len(selected_version_ids) + len(selected_evidence_ids),
                    self.EVIDENCE_GAP_ALGORITHM_VERSION, qualification, dossier_hash,
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    created_at,
                ),
            )
            connection.executemany(
                """INSERT INTO evidence_gap_dossier_versions
                   (dossier_id, claim_version_id, ordinal) VALUES (?, ?, ?)""",
                [(dossier_id, version_id, ordinal) for ordinal, version_id in enumerate(selected_version_ids)],
            )
            connection.execute(
                """INSERT INTO audit_events
                   (id, event_type, subject_type, subject_id, payload_hash, created_at)
                   VALUES (?, 'EVIDENCE_GAP_DOSSIER_CREATED',
                           'evidence_gap_dossier', ?, ?, ?)""",
                (new_id(), dossier_id, dossier_hash, created_at),
            )
            stored = connection.execute(
                "SELECT * FROM evidence_gap_dossiers WHERE id = ?", (dossier_id,)
            ).fetchone()
        return self._serialize_evidence_gap_dossier(stored)

    def get_evidence_gap_dossier(self, dossier_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM evidence_gap_dossiers WHERE id = ?", (dossier_id,)
            ).fetchone()
        if not row:
            raise NotFoundError("Dossier de lacunes de preuves introuvable")
        return self._serialize_evidence_gap_dossier(row)

    def list_evidence_gap_dossiers(self, limit: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM evidence_gap_dossiers").fetchone()[0]
            rows = connection.execute(
                """SELECT * FROM evidence_gap_dossiers
                   ORDER BY created_at DESC, id DESC LIMIT ?""", (limit,)
            ).fetchall()
        items = []
        for row in rows:
            snapshot = json.loads(row["snapshot_json"])
            items.append({
                "id": row["id"], "claim_id": snapshot["claim_id"],
                "claim_version_ids": snapshot["claim_version_ids"],
                "evidence_ids": snapshot["evidence_ids"],
                "qualification": snapshot["qualification"],
                "worst_transition": snapshot["worst_transition"],
                "metrics": snapshot["metrics"], "dossier_hash": row["dossier_hash"],
                "created_at": row["created_at"],
            })
        return {"items": items, "count": len(items), "total": total, "limit": limit}
