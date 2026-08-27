from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Relation = Literal["SUPPORTS", "CONTRADICTS"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceCreate(StrictModel):
    url: str = Field(min_length=3, max_length=2048)
    title: str = Field(min_length=1, max_length=500)
    publisher: str | None = Field(default=None, max_length=300)
    published_at: datetime | None = None
    reliability: float = Field(default=0.75, ge=0.0, le=1.0)


class ClaimCreate(StrictModel):
    statement: str = Field(min_length=3, max_length=20_000)
    context: str | None = Field(default=None, max_length=20_000)
    declared_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ClaimVersionCreate(ClaimCreate):
    change_note: str | None = Field(default=None, max_length=2_000)
    expected_current_version: int | None = Field(default=None, ge=1)


class EvidenceCreate(StrictModel):
    source_id: str = Field(min_length=1, max_length=64)
    excerpt: str = Field(min_length=1, max_length=50_000)
    locator: str | None = Field(default=None, max_length=2_000)
    strength: float = Field(default=0.75, ge=0.0, le=1.0)
    relation: Relation
    claim_version_number: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=2_000)

    @field_validator("relation", mode="before")
    @classmethod
    def normalize_relation(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


class ClaimRelationCreate(StrictModel):
    related_claim_id: str = Field(min_length=1, max_length=64)
    relation: Relation
    from_version_number: int | None = Field(default=None, ge=1)
    related_version_number: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=2_000)

    @field_validator("relation", mode="before")
    @classmethod
    def normalize_relation(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


class CoverageReportCreate(StrictModel):
    """Selects the immutable claim version and reference date to snapshot.

    All coverage, freshness, integrity and qualification fields are deliberately
    absent: they are derived by the server.
    """

    claim_version_number: int | None = Field(default=None, ge=1)
    as_of: date | None = None


class ImpactReportCreate(StrictModel):
    """Selects the root version and the bounded deterministic traversal.

    A qualification, affected claims and paths are intentionally absent: the
    server derives them exclusively from the stored relation graph.
    """

    claim_version_number: int | None = Field(default=None, ge=1)
    max_depth: int = Field(default=3, ge=1, le=8)


class ContradictionDossierCreate(StrictModel):
    """Selects only the immutable claim versions to compare.

    Depth, qualifications, evidence thresholds and every conclusion are fixed
    server policy and therefore cannot be supplied by a client.
    """

    claim_version_ids: list[str] = Field(min_length=2, max_length=20)

    @field_validator("claim_version_ids")
    @classmethod
    def validate_unique_version_ids(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 64 for value in values):
            raise ValueError("Chaque identifiant de version doit contenir entre 1 et 64 caractères")
        if len(set(values)) != len(values):
            raise ValueError("Les identifiants de version doivent être uniques")
        return values


class ProvenanceDossierCreate(StrictModel):
    """Selects only immutable claim versions for source-independence analysis."""

    claim_version_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("claim_version_ids")
    @classmethod
    def validate_unique_version_ids(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 64 for value in values):
            raise ValueError("Chaque identifiant de version doit contenir entre 1 et 64 caractères")
        if len(set(values)) != len(values):
            raise ValueError("Les identifiants de version doivent être uniques")
        return values


class TrustAlignmentDossierCreate(StrictModel):
    """Selects persisted claim versions; all alignment results are server-derived."""

    claim_version_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("claim_version_ids")
    @classmethod
    def validate_unique_version_ids(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 64 for value in values):
            raise ValueError("Chaque identifiant de version doit contenir entre 1 et 64 caractères")
        if len(set(values)) != len(values):
            raise ValueError("Les identifiants de version doivent être uniques")
        return values


class ClaimEvolutionDossierCreate(StrictModel):
    """Selects only persisted versions; all chronological measures are derived."""

    claim_version_ids: list[str] = Field(min_length=2, max_length=50)

    @field_validator("claim_version_ids")
    @classmethod
    def validate_unique_version_ids(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 64 for value in values):
            raise ValueError("Chaque identifiant de version doit contenir entre 1 et 64 caractères")
        if len(set(values)) != len(values):
            raise ValueError("Les identifiants de version doivent être uniques")
        return values


class EvidenceGapDossierCreate(StrictModel):
    """Selects one claim and 2..100 persisted version/evidence references."""

    claim_id: str = Field(min_length=1, max_length=64)
    claim_version_ids: list[str] = Field(min_length=1, max_length=100)
    evidence_ids: list[str] = Field(default_factory=list, max_length=99)

    @field_validator("claim_version_ids", "evidence_ids")
    @classmethod
    def validate_unique_ids(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 64 for value in values):
            raise ValueError("Chaque identifiant doit contenir entre 1 et 64 caractères")
        if len(set(values)) != len(values):
            raise ValueError("Les identifiants doivent être uniques")
        return values

    @model_validator(mode="after")
    def validate_reference_count(self):
        count = len(self.claim_version_ids) + len(self.evidence_ids)
        if count < 2 or count > 100:
            raise ValueError("Le dossier exige entre 2 et 100 références")
        return self
