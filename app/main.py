from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status

from . import __version__
from .db import Database
from .models import (
    ClaimCreate,
    ClaimRelationCreate,
    ClaimVersionCreate,
    ClaimEvolutionDossierCreate,
    ContradictionDossierCreate,
    CoverageReportCreate,
    EvidenceCreate,
    EvidenceGapDossierCreate,
    ImpactReportCreate,
    ProvenanceDossierCreate,
    SourceCreate,
    TrustAlignmentDossierCreate,
)
from .service import ConflictError, KnowledgeService, NotFoundError


def create_app(database_path: str | Path | None = None) -> FastAPI:
    resolved_path = Path(database_path or os.getenv("KNOWLEDGEFORGE_DB_PATH", "data/knowledgeforge.db"))
    database = Database(resolved_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        yield

    application = FastAPI(
        title="KnowledgeForge API",
        version=__version__,
        summary="Registre traçable d'affirmations et d'éléments probants",
        description=(
            "KnowledgeForge calcule des verdicts documentaires reproductibles à partir de "
            "liens explicites. Un verdict n'est jamais présenté comme une vérité factuelle."
        ),
        lifespan=lifespan,
    )
    application.state.database = database

    def service(request: Request) -> KnowledgeService:
        return KnowledgeService(request.app.state.database)

    @application.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError):
        return _error_response(status.HTTP_404_NOT_FOUND, str(exc))

    @application.exception_handler(ConflictError)
    async def conflict_handler(_: Request, exc: ConflictError):
        return _error_response(status.HTTP_409_CONFLICT, str(exc))

    @application.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.get("/info", tags=["system"])
    def info() -> dict[str, object]:
        return {
            "name": "KnowledgeForge",
            "version": __version__,
            "release": "V1.07",
            "capabilities": [
                "versioned-claims",
                "documentary-coverage",
                "bounded-impact-analysis",
                "immutable-contradiction-dossiers",
                "source-independence-provenance-dossiers",
                "trust-alignment-dossiers",
                "chronological-claim-evolution-dossiers",
                "evidence-gap-dossiers",
            ],
        }

    @application.post("/sources", status_code=status.HTTP_201_CREATED, tags=["sources"])
    def create_source(payload: SourceCreate, svc: KnowledgeService = Depends(service)):
        return svc.create_source(payload)

    @application.get("/sources/{source_id}", tags=["sources"])
    def get_source(source_id: str, svc: KnowledgeService = Depends(service)):
        return svc.get_source(source_id)

    @application.post("/claims", status_code=status.HTTP_201_CREATED, tags=["claims"])
    def create_claim(payload: ClaimCreate, svc: KnowledgeService = Depends(service)):
        return svc.create_claim(payload)

    @application.get("/claims/{claim_id}", tags=["claims"])
    def get_claim(claim_id: str, svc: KnowledgeService = Depends(service)):
        return svc.get_claim(claim_id)

    @application.post("/claims/{claim_id}/versions", status_code=status.HTTP_201_CREATED, tags=["claims"])
    def create_version(
        claim_id: str,
        payload: ClaimVersionCreate,
        svc: KnowledgeService = Depends(service),
    ):
        return svc.create_claim_version(claim_id, payload)

    @application.post("/claims/{claim_id}/evidence", status_code=status.HTTP_201_CREATED, tags=["evidence"])
    def add_evidence(
        claim_id: str,
        payload: EvidenceCreate,
        svc: KnowledgeService = Depends(service),
    ):
        return svc.add_evidence(claim_id, payload)

    @application.get("/evidence/{evidence_id}/verify", tags=["evidence"])
    def verify_evidence(evidence_id: str, svc: KnowledgeService = Depends(service)):
        return svc.get_evidence_verification(evidence_id)

    @application.post("/claims/{claim_id}/relations", status_code=status.HTTP_201_CREATED, tags=["relations"])
    def add_claim_relation(
        claim_id: str,
        payload: ClaimRelationCreate,
        svc: KnowledgeService = Depends(service),
    ):
        return svc.create_claim_relation(claim_id, payload)

    @application.get("/claims/{claim_id}/assessment", tags=["assessment"])
    def assess_claim(
        claim_id: str,
        version: int | None = Query(default=None, ge=1),
        svc: KnowledgeService = Depends(service),
    ):
        return svc.assess(claim_id, version)

    @application.get("/claims/{claim_id}/contradictions", tags=["assessment"])
    def detect_contradictions(
        claim_id: str,
        version: int | None = Query(default=None, ge=1),
        svc: KnowledgeService = Depends(service),
    ):
        return svc.contradictions(claim_id, version)

    @application.post(
        "/claims/{claim_id}/coverage-reports",
        status_code=status.HTTP_201_CREATED,
        tags=["coverage"],
    )
    def create_coverage_report(
        claim_id: str,
        payload: CoverageReportCreate,
        svc: KnowledgeService = Depends(service),
    ):
        return svc.create_coverage_report(claim_id, payload)

    @application.get("/coverage-reports/{report_id}", tags=["coverage"])
    def get_coverage_report(report_id: str, svc: KnowledgeService = Depends(service)):
        return svc.get_coverage_report(report_id)

    @application.post(
        "/claims/{claim_id}/impact-reports",
        status_code=status.HTTP_201_CREATED,
        tags=["impact"],
    )
    def create_impact_report(
        claim_id: str,
        payload: ImpactReportCreate,
        svc: KnowledgeService = Depends(service),
    ):
        return svc.create_impact_report(claim_id, payload)

    @application.get("/impact-reports/{report_id}", tags=["impact"])
    def get_impact_report(report_id: str, svc: KnowledgeService = Depends(service)):
        return svc.get_impact_report(report_id)

    @application.post(
        "/contradiction-dossiers",
        status_code=status.HTTP_201_CREATED,
        tags=["contradictions"],
    )
    def create_contradiction_dossier(
        payload: ContradictionDossierCreate,
        svc: KnowledgeService = Depends(service),
    ):
        return svc.create_contradiction_dossier(payload)

    @application.get(
        "/contradiction-dossiers/{dossier_id}", tags=["contradictions"]
    )
    def get_contradiction_dossier(
        dossier_id: str, svc: KnowledgeService = Depends(service)
    ):
        return svc.get_contradiction_dossier(dossier_id)

    @application.post(
        "/provenance-dossiers",
        status_code=status.HTTP_201_CREATED,
        tags=["provenance"],
    )
    def create_provenance_dossier(
        payload: ProvenanceDossierCreate,
        svc: KnowledgeService = Depends(service),
    ):
        return svc.create_provenance_dossier(payload)

    @application.get("/provenance-dossiers", tags=["provenance"])
    def list_provenance_dossiers(
        limit: int = Query(default=50, ge=1, le=100),
        svc: KnowledgeService = Depends(service),
    ):
        return svc.list_provenance_dossiers(limit)

    @application.get("/provenance-dossiers/{dossier_id}", tags=["provenance"])
    def get_provenance_dossier(
        dossier_id: str, svc: KnowledgeService = Depends(service)
    ):
        return svc.get_provenance_dossier(dossier_id)

    @application.post(
        "/trust-alignment-dossiers",
        status_code=status.HTTP_201_CREATED,
        tags=["trust-alignment"],
    )
    def create_trust_alignment_dossier(
        payload: TrustAlignmentDossierCreate,
        svc: KnowledgeService = Depends(service),
    ):
        return svc.create_trust_alignment_dossier(payload)

    @application.get("/trust-alignment-dossiers", tags=["trust-alignment"])
    def list_trust_alignment_dossiers(
        limit: int = Query(default=50, ge=1, le=100),
        svc: KnowledgeService = Depends(service),
    ):
        return svc.list_trust_alignment_dossiers(limit)

    @application.get(
        "/trust-alignment-dossiers/{dossier_id}", tags=["trust-alignment"]
    )
    def get_trust_alignment_dossier(
        dossier_id: str, svc: KnowledgeService = Depends(service)
    ):
        return svc.get_trust_alignment_dossier(dossier_id)

    @application.post(
        "/claim-evolution-dossiers",
        status_code=status.HTTP_201_CREATED,
        tags=["claim-evolution"],
    )
    def create_claim_evolution_dossier(
        payload: ClaimEvolutionDossierCreate,
        svc: KnowledgeService = Depends(service),
    ):
        return svc.create_claim_evolution_dossier(payload)

    @application.get("/claim-evolution-dossiers", tags=["claim-evolution"])
    def list_claim_evolution_dossiers(
        limit: int = Query(default=50, ge=1, le=100),
        svc: KnowledgeService = Depends(service),
    ):
        return svc.list_claim_evolution_dossiers(limit)

    @application.get(
        "/claim-evolution-dossiers/{dossier_id}", tags=["claim-evolution"]
    )
    def get_claim_evolution_dossier(
        dossier_id: str, svc: KnowledgeService = Depends(service)
    ):
        return svc.get_claim_evolution_dossier(dossier_id)

    @application.post(
        "/v1/evidence-gap-dossiers",
        status_code=status.HTTP_201_CREATED,
        tags=["evidence-gaps"],
    )
    def create_evidence_gap_dossier(
        payload: EvidenceGapDossierCreate,
        svc: KnowledgeService = Depends(service),
    ):
        return svc.create_evidence_gap_dossier(payload)

    @application.get("/v1/evidence-gap-dossiers", tags=["evidence-gaps"])
    def list_evidence_gap_dossiers(
        limit: int = Query(default=50, ge=1, le=100),
        svc: KnowledgeService = Depends(service),
    ):
        return svc.list_evidence_gap_dossiers(limit)

    @application.get(
        "/v1/evidence-gap-dossiers/{dossier_id}", tags=["evidence-gaps"]
    )
    def get_evidence_gap_dossier(
        dossier_id: str, svc: KnowledgeService = Depends(service)
    ):
        return svc.get_evidence_gap_dossier(dossier_id)

    return application


def _error_response(status_code: int, detail: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"detail": detail})


app = create_app()
