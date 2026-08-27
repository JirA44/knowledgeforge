from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path):
    application = create_app(tmp_path / "knowledgeforge-test.db")
    with TestClient(application) as test_client:
        yield test_client


def create_source(
    client: TestClient,
    suffix: str,
    reliability: float = 0.9,
    published_at: str | None = None,
    publisher: str = "Test Publisher",
) -> dict:
    response = client.post(
        "/sources",
        json={
            "url": f"https://example.test/{suffix}",
            "title": f"Source {suffix}",
            "publisher": publisher,
            "published_at": published_at,
            "reliability": reliability,
        },
    )
    assert response.status_code == 201
    return response.json()


def create_claim(client: TestClient, statement: str = "Le résultat mesuré est reproductible.") -> dict:
    response = client.post("/claims", json={"statement": statement, "context": "Protocole A"})
    assert response.status_code == 201
    return response.json()


def link_evidence(
    client: TestClient,
    claim_id: str,
    source_id: str,
    relation: str,
    excerpt: str,
    strength: float = 0.9,
) -> dict:
    response = client.post(
        f"/claims/{claim_id}/evidence",
        json={
            "source_id": source_id,
            "relation": relation,
            "excerpt": excerpt,
            "locator": "p. 1",
            "strength": strength,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_health_and_version(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "1.0.7"}
    info = client.get("/info")
    assert info.status_code == 200
    assert info.json()["version"] == "1.0.7"
    assert info.json()["release"] == "V1.07"


def test_claim_is_versioned_and_old_version_is_preserved(client):
    claim = create_claim(client, "Version initiale de l'affirmation.")
    response = client.post(
        f"/claims/{claim['id']}/versions",
        json={
            "statement": "Version corrigée de l'affirmation.",
            "context": "Contexte B",
            "change_note": "Correction méthodologique",
            "expected_current_version": 1,
        },
    )
    assert response.status_code == 201
    assert response.json()["version_number"] == 2

    stored = client.get(f"/claims/{claim['id']}").json()
    assert [item["version_number"] for item in stored["versions"]] == [1, 2]
    assert stored["versions"][0]["statement"] == "Version initiale de l'affirmation."
    assert stored["current_version"]["statement"] == "Version corrigée de l'affirmation."


def test_optimistic_version_conflict(client):
    claim = create_claim(client)
    response = client.post(
        f"/claims/{claim['id']}/versions",
        json={"statement": "Nouvelle formulation.", "expected_current_version": 4},
    )
    assert response.status_code == 409
    assert "Version courante" in response.json()["detail"]


def test_evidence_hash_is_normalized_and_verifiable(client):
    source = create_source(client, "hash")
    claim_a = create_claim(client, "Affirmation A")
    claim_b = create_claim(client, "Affirmation B")
    first = link_evidence(client, claim_a["id"], source["id"], "SUPPORTS", "Texte   avec espaces")
    second = link_evidence(client, claim_b["id"], source["id"], "supports", "Texte avec espaces")

    assert first["evidence"]["content_hash"] == second["evidence"]["content_hash"]
    assert second["deduplicated"] is True
    verification = client.get(f"/evidence/{first['evidence']['id']}/verify")
    assert verification.status_code == 200
    assert verification.json()["valid"] is True
    assert len(verification.json()["stored_hash"]) == 64


def test_hash_verification_detects_database_tampering(client):
    source = create_source(client, "tamper")
    claim = create_claim(client, "Affirmation à protéger")
    linked = link_evidence(client, claim["id"], source["id"], "SUPPORTS", "Extrait original")
    evidence_id = linked["evidence"]["id"]
    database = client.app.state.database
    with database.connect() as connection:
        connection.execute("UPDATE evidence SET strength = 0.1 WHERE id = ?", (evidence_id,))
    assert client.get(f"/evidence/{evidence_id}/verify").json()["valid"] is False


def test_empty_assessment_is_insufficient(client):
    claim = create_claim(client)
    assessment = client.get(f"/claims/{claim['id']}/assessment").json()
    assert assessment["verdict"] == "INSUFFICIENT"
    assert assessment["confidence_score"] == 0
    assert "vérité factuelle" in assessment["disclaimer"]


def test_two_independent_sources_can_support(client):
    claim = create_claim(client)
    first = create_source(client, "support-1", reliability=0.9)
    second = create_source(client, "support-2", reliability=0.85)
    link_evidence(client, claim["id"], first["id"], "SUPPORTS", "Mesure indépendante numéro un.")
    link_evidence(client, claim["id"], second["id"], "SUPPORTS", "Mesure indépendante numéro deux.")

    assessment = client.get(f"/claims/{claim['id']}/assessment").json()
    assert assessment["verdict"] == "SUPPORTED"
    assert assessment["support_source_count"] == 2
    assert assessment["support_score"] > 0.9
    assert 0 < assessment["confidence_score"] <= 1


def test_single_source_does_not_manufacture_supported_verdict(client):
    claim = create_claim(client)
    source = create_source(client, "single", reliability=1.0)
    link_evidence(client, claim["id"], source["id"], "SUPPORTS", "Une seule publication.", 1.0)
    assessment = client.get(f"/claims/{claim['id']}/assessment").json()
    assert assessment["verdict"] == "INSUFFICIENT"
    assert assessment["support_score"] == 1.0


def test_contradictory_evidence_makes_claim_contested(client):
    claim = create_claim(client)
    support_a = create_source(client, "mixed-support-1", 0.9)
    support_b = create_source(client, "mixed-support-2", 0.9)
    opposing = create_source(client, "mixed-oppose", 0.9)
    link_evidence(client, claim["id"], support_a["id"], "SUPPORTS", "Résultat favorable A.")
    link_evidence(client, claim["id"], support_b["id"], "SUPPORTS", "Résultat favorable B.")
    link_evidence(client, claim["id"], opposing["id"], "CONTRADICTS", "Résultat opposé.")

    assessment = client.get(f"/claims/{claim['id']}/assessment").json()
    detection = client.get(f"/claims/{claim['id']}/contradictions").json()
    assert assessment["verdict"] == "CONTESTED"
    assert detection["detected"] is True
    assert detection["mixed_evidence"] is True
    assert len(detection["contradictory_evidence"]) == 1


def test_explicit_claim_contradiction_is_detected_but_not_scored_as_evidence(client):
    first = create_claim(client, "Le paramètre est supérieur à dix.")
    second = create_claim(client, "Le paramètre est inférieur ou égal à dix.")
    response = client.post(
        f"/claims/{first['id']}/relations",
        json={"related_claim_id": second["id"], "relation": "contradicts", "note": "Bornes incompatibles"},
    )
    assert response.status_code == 201

    detection = client.get(f"/claims/{second['id']}/contradictions").json()
    assessment = client.get(f"/claims/{second['id']}/assessment").json()
    assert detection["detected"] is True
    assert len(detection["explicit_claim_conflicts"]) == 1
    assert assessment["verdict"] == "INSUFFICIENT"


def test_evidence_attaches_to_selected_historical_version(client):
    claim = create_claim(client, "Formulation V1")
    client.post(f"/claims/{claim['id']}/versions", json={"statement": "Formulation V2"})
    source = create_source(client, "historical")
    response = client.post(
        f"/claims/{claim['id']}/evidence",
        json={
            "source_id": source["id"],
            "relation": "SUPPORTS",
            "excerpt": "Extrait relatif à V1.",
            "claim_version_number": 1,
        },
    )
    assert response.status_code == 201
    assert response.json()["link"]["claim_version_number"] == 1
    assert client.get(f"/claims/{claim['id']}/assessment?version=1").json()["evidence_count"] == 1
    assert client.get(f"/claims/{claim['id']}/assessment?version=2").json()["evidence_count"] == 0


def test_missing_resources_and_invalid_payloads_are_rejected(client):
    assert client.get("/claims/missing").status_code == 404
    claim = create_claim(client)
    response = client.post(
        f"/claims/{claim['id']}/evidence",
        json={"source_id": "missing", "relation": "SUPPORTS", "excerpt": "Preuve"},
    )
    assert response.status_code == 404
    invalid_source = client.post(
        "/sources",
        json={"url": "x", "title": "", "reliability": 2},
    )
    assert invalid_source.status_code == 422


def test_sqlite_foreign_keys_are_enabled(tmp_path):
    app = create_app(tmp_path / "fk.db")
    with TestClient(app):
        database = app.state.database
        with pytest.raises(sqlite3.IntegrityError):
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO evidence
                       (id, source_id, excerpt, locator, strength, content_hash, created_at)
                       VALUES ('e', 'absent', 'x', NULL, 0.5, ?, 'now')""",
                    ("0" * 64,),
                )


def create_coverage_report(client: TestClient, claim_id: str, as_of: str = "2026-08-22") -> dict:
    response = client.post(
        f"/claims/{claim_id}/coverage-reports",
        json={"as_of": as_of},
    )
    assert response.status_code == 201
    return response.json()


def test_coverage_report_is_current_with_two_fresh_independent_sources(client):
    claim = create_claim(client)
    first = create_source(
        client, "fresh-1", published_at="2026-08-01T00:00:00Z", publisher="Institut A"
    )
    second = create_source(
        client, "fresh-2", published_at="2026-05-01T00:00:00Z", publisher="Institut B"
    )
    link_evidence(client, claim["id"], first["id"], "SUPPORTS", "Résultat récent A.")
    link_evidence(client, claim["id"], second["id"], "SUPPORTS", "Résultat récent B.")

    report = create_coverage_report(client, claim["id"])
    assert report["qualification"] == "CURRENT"
    assert report["coverage"]["independent_support_source_count"] == 2
    assert report["coverage"]["fresh_independent_support_source_count"] == 2
    assert report["freshness"]["fresh_count"] == 2
    assert report["integrity"]["valid"] is True


def test_coverage_report_is_stale_when_support_is_old(client):
    claim = create_claim(client)
    first = create_source(
        client, "old-1", published_at="2020-01-01T00:00:00Z", publisher="Archives A"
    )
    second = create_source(
        client, "old-2", published_at="2021-01-01T00:00:00Z", publisher="Archives B"
    )
    link_evidence(client, claim["id"], first["id"], "SUPPORTS", "Ancien résultat A.")
    link_evidence(client, claim["id"], second["id"], "SUPPORTS", "Ancien résultat B.")

    report = create_coverage_report(client, claim["id"])
    assert report["qualification"] == "STALE"
    assert report["freshness"]["stale_count"] == 2
    assert report["freshness"]["dated_fresh_ratio"] == 0


def test_coverage_report_is_insufficient_for_one_source_or_invalid_hash(client):
    claim = create_claim(client)
    source = create_source(
        client, "one", published_at="2026-08-01T00:00:00Z", publisher="Institut Unique"
    )
    linked = link_evidence(client, claim["id"], source["id"], "SUPPORTS", "Résultat unique.")
    first = create_coverage_report(client, claim["id"])
    assert first["qualification"] == "INSUFFICIENT"

    with client.app.state.database.connect() as connection:
        connection.execute(
            "UPDATE evidence SET excerpt = 'Altération directe' WHERE id = ?",
            (linked["evidence"]["id"],),
        )
    altered = create_coverage_report(client, claim["id"])
    assert altered["qualification"] == "INSUFFICIENT"
    assert altered["integrity"]["valid"] is False
    assert altered["integrity"]["invalid_hash_evidence_ids"] == [linked["evidence"]["id"]]


def test_coverage_report_is_contested_when_usable_contradiction_exists(client):
    claim = create_claim(client)
    support_a = create_source(
        client, "coverage-sa", published_at="2026-07-01T00:00:00Z", publisher="Lab A"
    )
    support_b = create_source(
        client, "coverage-sb", published_at="2026-07-02T00:00:00Z", publisher="Lab B"
    )
    opposing = create_source(
        client, "coverage-c", published_at="2026-07-03T00:00:00Z", publisher="Lab C"
    )
    link_evidence(client, claim["id"], support_a["id"], "SUPPORTS", "Soutien A.")
    link_evidence(client, claim["id"], support_b["id"], "SUPPORTS", "Soutien B.")
    link_evidence(client, claim["id"], opposing["id"], "CONTRADICTS", "Résultat contraire.")

    report = create_coverage_report(client, claim["id"])
    assert report["qualification"] == "CONTESTED"
    assert report["coverage"]["contradicts_present"] is True
    assert report["coverage"]["independent_contradict_source_count"] == 1


def test_coverage_report_is_immutable_hashed_and_idempotent(client):
    claim = create_claim(client)
    first = create_coverage_report(client, claim["id"])
    replay = create_coverage_report(client, claim["id"])
    fetched = client.get(f"/coverage-reports/{first['id']}")

    assert first["id"] == replay["id"]
    assert first["report_hash"] == replay["report_hash"]
    assert len(first["report_hash"]) == 64
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert fetched.status_code == 200
    assert fetched.json()["report_hash"] == first["report_hash"]

    database = client.app.state.database
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute(
                "UPDATE coverage_reports SET qualification = 'CURRENT' WHERE id = ?",
                (first["id"],),
            )
    with database.connect() as connection:
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE subject_id = ?", (first["id"],)
        ).fetchone()[0]
    assert audit_count == 1


def test_coverage_report_rejects_client_verdict_and_extra_fields(client):
    claim = create_claim(client)
    response = client.post(
        f"/claims/{claim['id']}/coverage-reports",
        json={"as_of": "2026-08-22", "qualification": "CURRENT"},
    )
    assert response.status_code == 422

    schema = client.app.openapi()
    assert schema["info"]["version"] == "1.0.7"
    request_schema = schema["components"]["schemas"]["CoverageReportCreate"]
    assert request_schema["additionalProperties"] is False


def test_coverage_report_flags_evidence_whose_source_is_missing(client):
    claim = create_claim(client)
    source = create_source(
        client, "orphan", published_at="2026-08-01T00:00:00Z", publisher="Institut Orphelin"
    )
    linked = link_evidence(client, claim["id"], source["id"], "SUPPORTS", "Extrait orphelin.")

    # Simule une corruption externe qui contourne volontairement les contraintes.
    raw = sqlite3.connect(client.app.state.database.path)
    try:
        raw.execute("PRAGMA foreign_keys = OFF")
        raw.execute("DELETE FROM sources WHERE id = ?", (source["id"],))
        raw.commit()
    finally:
        raw.close()

    report = create_coverage_report(client, claim["id"])
    assert report["qualification"] == "INSUFFICIENT"
    assert report["integrity"]["missing_source_evidence_ids"] == [linked["evidence"]["id"]]
    assert report["evidence"][0]["source_exists"] is False


def link_claims(
    client: TestClient,
    origin_id: str,
    target_id: str,
    relation: str = "SUPPORTS",
    *,
    origin_version: int | None = None,
    target_version: int | None = None,
) -> dict:
    payload: dict = {"related_claim_id": target_id, "relation": relation}
    if origin_version is not None:
        payload["from_version_number"] = origin_version
    if target_version is not None:
        payload["related_version_number"] = target_version
    response = client.post(f"/claims/{origin_id}/relations", json=payload)
    assert response.status_code == 201
    return response.json()


def create_impact_report(client: TestClient, claim_id: str, max_depth: int = 3) -> dict:
    response = client.post(
        f"/claims/{claim_id}/impact-reports",
        json={"max_depth": max_depth},
    )
    assert response.status_code == 201
    return response.json()


def test_impact_report_traverses_chain_and_exposes_current_versions_and_coverage(client):
    root = create_claim(client, "Racine")
    middle = create_claim(client, "Milieu V1")
    leaf = create_claim(client, "Feuille")
    link_claims(client, root["id"], middle["id"], "SUPPORTS")
    link_claims(client, middle["id"], leaf["id"], "CONTRADICTS")
    coverage = create_coverage_report(client, middle["id"])
    client.post(
        f"/claims/{middle['id']}/versions",
        json={"statement": "Milieu V2", "expected_current_version": 1},
    )

    report = create_impact_report(client, root["id"])

    assert report["qualification"] == "LOCALIZED"
    assert report["metrics"]["affected_claim_count"] == 2
    assert sorted(item["depth"] for item in report["affected_claims"]) == [1, 2]
    middle_item = next(item for item in report["affected_claims"] if item["claim_id"] == middle["id"])
    leaf_item = next(item for item in report["affected_claims"] if item["claim_id"] == leaf["id"])
    assert middle_item["traversed_version_number"] == 1
    assert middle_item["current_version_number"] == 2
    assert middle_item["is_current_version"] is False
    assert middle_item["coverage_report"]["id"] == coverage["id"]
    assert leaf_item["relationship_types"] == ["CONTRADICTS", "SUPPORTS"]
    assert [edge["relation"] for edge in leaf_item["paths"][0]] == [
        "SUPPORTS",
        "CONTRADICTS",
    ]


def test_impact_report_classifies_four_branches_as_widespread(client):
    root = create_claim(client, "Racine ramifiée")
    branches = [create_claim(client, f"Branche {number}") for number in range(4)]
    for branch in branches:
        link_claims(client, root["id"], branch["id"])

    report = create_impact_report(client, root["id"])

    assert report["qualification"] == "WIDESPREAD"
    assert report["metrics"]["affected_claim_count"] == 4
    assert report["metrics"]["branching_node_count"] == 1


def test_impact_report_detects_cycle_without_expanding_it(client):
    root = create_claim(client, "Cycle A")
    second = create_claim(client, "Cycle B")
    link_claims(client, root["id"], second["id"])
    link_claims(client, second["id"], root["id"], "CONTRADICTS")

    report = create_impact_report(client, root["id"], max_depth=8)

    assert report["qualification"] == "CYCLE_DETECTED"
    assert report["metrics"]["cycle_count"] == 1
    assert report["metrics"]["affected_claim_count"] == 1
    assert [edge["relation"] for edge in report["cycles"][0]] == [
        "SUPPORTS",
        "CONTRADICTS",
    ]


def test_impact_report_respects_depth_limit(client):
    root = create_claim(client, "Profondeur A")
    middle = create_claim(client, "Profondeur B")
    leaf = create_claim(client, "Profondeur C")
    link_claims(client, root["id"], middle["id"])
    link_claims(client, middle["id"], leaf["id"])

    report = create_impact_report(client, root["id"], max_depth=1)

    assert [item["claim_id"] for item in report["affected_claims"]] == [middle["id"]]
    assert report["metrics"]["maximum_reached_depth"] == 1
    assert report["metrics"]["depth_limit_reached"] is True
    assert report["metrics"]["depth_limited_relation_count"] == 1


def test_impact_report_is_insufficient_without_outgoing_relation(client):
    root = create_claim(client, "Affirmation isolée")
    report = create_impact_report(client, root["id"])
    assert report["qualification"] == "INSUFFICIENT"
    assert report["affected_claims"] == []
    assert report["metrics"]["traversed_relation_count"] == 0


def test_impact_report_is_hashed_idempotent_immutable_and_audited(client):
    root = create_claim(client, "Audit racine")
    target = create_claim(client, "Audit cible")
    link_claims(client, root["id"], target["id"])

    first = create_impact_report(client, root["id"])
    replay = create_impact_report(client, root["id"])
    fetched = client.get(f"/impact-reports/{first['id']}")

    assert first["id"] == replay["id"]
    assert first["report_hash"] == replay["report_hash"]
    assert len(first["report_hash"]) == 64
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert fetched.status_code == 200
    assert fetched.json()["report_hash"] == first["report_hash"]

    database = client.app.state.database
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute(
                "UPDATE impact_reports SET qualification = 'WIDESPREAD' WHERE id = ?",
                (first["id"],),
            )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute("DELETE FROM impact_reports WHERE id = ?", (first["id"],))
    with database.connect() as connection:
        audit_id = connection.execute(
            "SELECT id FROM audit_events WHERE subject_id = ?", (first["id"],)
        ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with database.connect() as connection:
            connection.execute("DELETE FROM audit_events WHERE id = ?", (audit_id,))


def test_impact_report_rejects_client_qualification_and_extra_fields(client):
    root = create_claim(client, "Payload strict")
    response = client.post(
        f"/claims/{root['id']}/impact-reports",
        json={"max_depth": 2, "qualification": "LOCALIZED"},
    )
    assert response.status_code == 422
    assert client.post(
        f"/claims/{root['id']}/impact-reports", json={"max_depth": 9}
    ).status_code == 422

    schema = client.app.openapi()
    request_schema = schema["components"]["schemas"]["ImpactReportCreate"]
    assert request_schema["additionalProperties"] is False
    assert schema["info"]["version"] == "1.0.7"


def create_contradiction_dossier(
    client: TestClient, claim_version_ids: list[str]
) -> dict:
    response = client.post(
        "/contradiction-dossiers", json={"claim_version_ids": claim_version_ids}
    )
    assert response.status_code == 201
    return response.json()


def add_support_sources(
    client: TestClient,
    claim_id: str,
    prefix: str,
    count: int,
) -> None:
    for number in range(count):
        source = create_source(
            client,
            f"{prefix}-{number}",
            reliability=0.9,
            published_at="2026-08-01T00:00:00Z",
            publisher=f"{prefix} Publisher {number}",
        )
        link_evidence(
            client,
            claim_id,
            source["id"],
            "SUPPORTS",
            f"Soutien documentaire {prefix} {number}.",
            0.9,
        )


def test_contradiction_dossier_is_unresolved_when_both_sides_are_documented(client):
    left = create_claim(client, "Position documentée A")
    right = create_claim(client, "Position documentée B")
    link_claims(client, left["id"], right["id"], "CONTRADICTS")
    add_support_sources(client, left["id"], "left-balanced", 2)
    add_support_sources(client, right["id"], "right-balanced", 2)
    left_coverage = create_coverage_report(client, left["id"])
    right_coverage = create_coverage_report(client, right["id"])

    dossier = create_contradiction_dossier(
        client, [left["current_version"]["id"], right["current_version"]["id"]]
    )

    assert dossier["qualification"] == "UNRESOLVED"
    assert dossier["metrics"]["contradictory_pair_count"] == 1
    assert dossier["evidence_asymmetries"] == []
    coverage_ids = {
        item["coverage_report"]["id"] for item in dossier["selected_versions"]
    }
    assert coverage_ids == {left_coverage["id"], right_coverage["id"]}
    assert all(
        item["evidence_summary"]["support_source_count"] == 2
        for item in dossier["selected_versions"]
    )
    assert "ne prouve pas la vérité" in dossier["warning"]


def test_contradiction_dossier_detects_material_evidence_asymmetry(client):
    documented = create_claim(client, "Côté fortement documenté")
    sparse = create_claim(client, "Côté sans soutien enregistré")
    link_claims(client, documented["id"], sparse["id"], "CONTRADICTS")
    add_support_sources(client, documented["id"], "asymmetric", 2)

    dossier = create_contradiction_dossier(
        client,
        [documented["current_version"]["id"], sparse["current_version"]["id"]],
    )

    assert dossier["qualification"] == "EVIDENCE_ASYMMETRY"
    assert dossier["metrics"]["evidence_asymmetry_count"] == 1
    asymmetry = dossier["evidence_asymmetries"][0]
    assert asymmetry["stronger_documented_side_id"] == documented["current_version"]["id"]
    assert asymmetry["support_source_gap"] == 2
    assert "ne prouve pas" in asymmetry["warning"]


def test_contradiction_dossier_is_insufficient_when_both_sides_have_one_source(client):
    left = create_claim(client, "Côté incomplet A")
    right = create_claim(client, "Côté incomplet B")
    link_claims(client, left["id"], right["id"], "CONTRADICTS")
    add_support_sources(client, left["id"], "insufficient-left", 1)
    add_support_sources(client, right["id"], "insufficient-right", 1)

    dossier = create_contradiction_dossier(
        client, [left["current_version"]["id"], right["current_version"]["id"]]
    )

    assert dossier["qualification"] == "INSUFFICIENT"
    assert dossier["contradictory_pairs"][0]["both_sides_sufficiently_supported"] is False
    assert dossier["contradictory_pairs"][0]["material_evidence_asymmetry"] is False


def test_contradiction_dossier_reports_no_direct_contradiction(client):
    left = create_claim(client, "Affirmation indépendante A")
    right = create_claim(client, "Affirmation indépendante B")

    dossier = create_contradiction_dossier(
        client, [left["current_version"]["id"], right["current_version"]["id"]]
    )

    assert dossier["qualification"] == "NO_CONTRADICTION"
    assert dossier["contradictory_pairs"] == []
    assert dossier["metrics"]["contradictory_pair_count"] == 0


def test_contradiction_dossier_keeps_indirect_bounded_paths_contextual(client):
    left = create_claim(client, "Chemin racine")
    middle = create_claim(client, "Chemin intermédiaire")
    right = create_claim(client, "Chemin cible")
    link_claims(client, left["id"], middle["id"], "SUPPORTS")
    link_claims(client, middle["id"], right["id"], "CONTRADICTS")

    dossier = create_contradiction_dossier(
        client, [left["current_version"]["id"], right["current_version"]["id"]]
    )

    assert dossier["qualification"] == "NO_CONTRADICTION"
    assert dossier["contradictory_pairs"] == []
    assert len(dossier["relation_contexts"]) == 1
    path = dossier["relation_contexts"][0]["bounded_paths"][0]
    assert [edge["relation"] for edge in path] == ["SUPPORTS", "CONTRADICTS"]
    assert len(path) <= dossier["policy"]["path_maximum_depth"] == 3
    assert dossier["policy"]["indirect_paths_establish_contradiction"] is False


def test_contradiction_dossier_excludes_context_paths_beyond_fixed_depth(client):
    nodes = [create_claim(client, f"Long chemin {number}") for number in range(5)]
    link_claims(client, nodes[0]["id"], nodes[1]["id"], "SUPPORTS")
    link_claims(client, nodes[1]["id"], nodes[2]["id"], "SUPPORTS")
    link_claims(client, nodes[2]["id"], nodes[3]["id"], "SUPPORTS")
    link_claims(client, nodes[3]["id"], nodes[4]["id"], "CONTRADICTS")

    dossier = create_contradiction_dossier(
        client,
        [nodes[0]["current_version"]["id"], nodes[4]["current_version"]["id"]],
    )

    assert dossier["qualification"] == "NO_CONTRADICTION"
    assert dossier["relation_contexts"] == []
    assert dossier["metrics"]["context_path_count"] == 0
    assert dossier["policy"]["path_maximum_depth"] == 3


def test_contradiction_dossier_marks_historical_selection_as_frozen(client):
    historical = create_claim(client, "Version sélectionnée V1")
    other = create_claim(client, "Autre version")
    historical_id = historical["current_version"]["id"]
    response = client.post(
        f"/claims/{historical['id']}/versions",
        json={"statement": "Version courante V2", "expected_current_version": 1},
    )
    assert response.status_code == 201

    dossier = create_contradiction_dossier(
        client, [historical_id, other["current_version"]["id"]]
    )
    selected = next(
        item for item in dossier["selected_versions"]
        if item["claim_version_id"] == historical_id
    )
    assert selected["selection_state"] == "FROZEN"
    assert selected["version_number"] == 1
    assert selected["current_version_number"] == 2
    assert dossier["metrics"]["frozen_version_count"] == 1


def test_contradiction_dossier_is_order_independent_hashed_immutable_and_audited(client):
    left = create_claim(client, "Dossier immuable A")
    right = create_claim(client, "Dossier immuable B")
    left_id = left["current_version"]["id"]
    right_id = right["current_version"]["id"]
    first = create_contradiction_dossier(client, [left_id, right_id])
    replay = create_contradiction_dossier(client, [right_id, left_id])
    fetched = client.get(f"/contradiction-dossiers/{first['id']}")

    assert first["id"] == replay["id"]
    assert first["dossier_hash"] == replay["dossier_hash"]
    assert len(first["dossier_hash"]) == 64
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert fetched.status_code == 200
    assert fetched.json()["dossier_hash"] == first["dossier_hash"]

    database = client.app.state.database
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute(
                "UPDATE contradiction_dossiers SET qualification = 'UNRESOLVED' WHERE id = ?",
                (first["id"],),
            )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute(
                "DELETE FROM contradiction_dossier_versions WHERE dossier_id = ?",
                (first["id"],),
            )
    with database.connect() as connection:
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE subject_id = ?", (first["id"],)
        ).fetchone()[0]
    assert audit_count == 1


def test_contradiction_dossier_payload_is_strict_and_ids_are_validated(client):
    first = create_claim(client, "Payload A")["current_version"]["id"]
    second = create_claim(client, "Payload B")["current_version"]["id"]
    assert client.post(
        "/contradiction-dossiers", json={"claim_version_ids": [first]}
    ).status_code == 422
    assert client.post(
        "/contradiction-dossiers", json={"claim_version_ids": [first, first]}
    ).status_code == 422
    assert client.post(
        "/contradiction-dossiers",
        json={"claim_version_ids": [first, second], "verdict": "UNRESOLVED"},
    ).status_code == 422
    assert client.post(
        "/contradiction-dossiers",
        json={"claim_version_ids": [f"id-{number}" for number in range(21)]},
    ).status_code == 422
    assert client.post(
        "/contradiction-dossiers", json={"claim_version_ids": [first, "missing"]}
    ).status_code == 404

    schema = client.app.openapi()
    request_schema = schema["components"]["schemas"]["ContradictionDossierCreate"]
    assert request_schema["additionalProperties"] is False
    assert list(request_schema["properties"]) == ["claim_version_ids"]
    assert schema["info"]["version"] == "1.0.7"


def create_provenance_dossier(client: TestClient, claim_version_ids: list[str]) -> dict:
    response = client.post(
        "/provenance-dossiers", json={"claim_version_ids": claim_version_ids}
    )
    assert response.status_code == 201
    return response.json()


def test_provenance_dossier_is_diverse_with_three_balanced_sources(client):
    claim = create_claim(client, "Diversité documentaire")
    add_support_sources(client, claim["id"], "diverse", 3)

    dossier = create_provenance_dossier(client, [claim["current_version"]["id"]])

    assert dossier["qualification"] == "DIVERSE"
    assert dossier["metrics"]["independent_source_count"] == 3
    assert dossier["metrics"]["dominant_source_share"] == pytest.approx(1 / 3, abs=0.0001)
    assert dossier["claim_coverage"][0]["independent_source_count"] == 3
    assert dossier["dominant_source"]["unique_content_count"] == 1
    assert "ne prouvent pas la vérité" in dossier["warning"]


def test_provenance_dossier_detects_concentration_above_half(client):
    claim = create_claim(client, "Concentration documentaire")
    dominant = create_source(client, "dominant", publisher="Agence Dominante")
    for number in range(4):
        link_evidence(
            client,
            claim["id"],
            dominant["id"],
            "SUPPORTS",
            f"Contenu dominant distinct {number}.",
        )
    for number, publisher in enumerate(("Agence B", "Agence C")):
        source = create_source(client, f"minor-{number}", publisher=publisher)
        link_evidence(
            client, claim["id"], source["id"], "SUPPORTS", f"Contenu minoritaire {number}."
        )

    dossier = create_provenance_dossier(client, [claim["current_version"]["id"]])

    assert dossier["qualification"] == "CONCENTRATED"
    assert dossier["metrics"]["independent_source_count"] == 3
    assert dossier["dominant_source"]["stable_identifier"] == "agence dominante"
    assert dossier["metrics"]["dominant_source_share"] == pytest.approx(4 / 6, abs=0.0001)


def test_provenance_dossier_groups_same_publisher_as_one_source(client):
    claim = create_claim(client, "Sources du même éditeur")
    for number in range(3):
        source = create_source(
            client,
            f"same-publisher-{number}",
            publisher="Éditeur Commun",
        )
        link_evidence(
            client,
            claim["id"],
            source["id"],
            "SUPPORTS",
            f"Publication distincte {number}.",
        )

    dossier = create_provenance_dossier(client, [claim["current_version"]["id"]])

    assert dossier["qualification"] == "INSUFFICIENT"
    assert dossier["metrics"]["independent_source_count"] == 1
    assert dossier["source_groups"][0]["provenance_kind"] == "PUBLISHER"
    assert len(dossier["source_groups"][0]["source_ids"]) == 3


def test_provenance_dossier_marks_unresolvable_external_provenance_unknown(client):
    claim = create_claim(client, "Provenance externe absente")
    response = client.post(
        "/sources",
        json={
            "url": "urn:dataset:alpha",
            "title": "Jeu sans éditeur ni domaine",
            "reliability": 0.8,
        },
    )
    assert response.status_code == 201
    source = response.json()
    link_evidence(client, claim["id"], source["id"], "SUPPORTS", "Contenu sans provenance externe.")

    dossier = create_provenance_dossier(client, [claim["current_version"]["id"]])

    assert dossier["qualification"] == "UNKNOWN_PROVENANCE"
    assert dossier["metrics"]["unknown_provenance_group_count"] == 1
    assert dossier["source_groups"][0]["provenance_kind"] == "SOURCE_ID"
    assert dossier["source_groups"][0]["stable_identifier"] == source["id"]


def test_provenance_dossier_detects_duplicate_content_across_sources(client):
    claim = create_claim(client, "Contenu recopié")
    for number in range(3):
        source = create_source(
            client, f"duplicate-{number}", publisher=f"Éditeur Duplicate {number}"
        )
        link_evidence(
            client,
            claim["id"],
            source["id"],
            "SUPPORTS",
            "Le même extrait normalisé est reproduit.",
        )

    dossier = create_provenance_dossier(client, [claim["current_version"]["id"]])

    assert dossier["qualification"] == "CONCENTRATED"
    assert dossier["metrics"]["duplicate_content_group_count"] == 1
    assert dossier["metrics"]["duplicate_link_count"] == 2
    duplicate = dossier["duplicate_content_groups"][0]
    assert duplicate["link_count"] == 3
    assert len(duplicate["stored_content_hashes"]) == 3
    assert dossier["metrics"]["unique_usable_content_count"] == 1
    assert dossier["metrics"]["dominant_source_share"] == 1.0


def test_provenance_dossier_rejects_tampered_evidence_from_diversity(client):
    claim = create_claim(client, "Intégrité de provenance")
    add_support_sources(client, claim["id"], "integrity", 3)
    database = client.app.state.database
    with database.connect() as connection:
        evidence_id = connection.execute("SELECT id FROM evidence ORDER BY id LIMIT 1").fetchone()[0]
        connection.execute(
            "UPDATE evidence SET excerpt = 'Altération hors API' WHERE id = ?", (evidence_id,)
        )

    dossier = create_provenance_dossier(client, [claim["current_version"]["id"]])

    assert dossier["qualification"] == "INSUFFICIENT"
    assert dossier["integrity"]["valid"] is False
    assert dossier["integrity"]["invalid_hash_evidence_ids"] == [evidence_id]
    invalid = next(item for item in dossier["evidence_links"] if item["evidence_id"] == evidence_id)
    assert invalid["usable"] is False


def test_provenance_dossier_exposes_frozen_version_and_existing_coverage(client):
    claim = create_claim(client, "Provenance V1")
    add_support_sources(client, claim["id"], "frozen", 3)
    coverage = create_coverage_report(client, claim["id"])
    selected_id = claim["current_version"]["id"]
    response = client.post(
        f"/claims/{claim['id']}/versions",
        json={"statement": "Provenance V2", "expected_current_version": 1},
    )
    assert response.status_code == 201

    dossier = create_provenance_dossier(client, [selected_id])

    assert dossier["selected_versions"][0]["selection_state"] == "FROZEN"
    assert dossier["selected_versions"][0]["current_version_number"] == 2
    assert dossier["claim_coverage"][0]["coverage_report"]["id"] == coverage["id"]


def test_provenance_dossier_is_order_independent_immutable_audited_and_listed(client):
    first_claim = create_claim(client, "Liste provenance A")
    second_claim = create_claim(client, "Liste provenance B")
    first_id = first_claim["current_version"]["id"]
    second_id = second_claim["current_version"]["id"]
    first = create_provenance_dossier(client, [first_id, second_id])
    replay = create_provenance_dossier(client, [second_id, first_id])
    fetched = client.get(f"/provenance-dossiers/{first['id']}")
    listed = client.get("/provenance-dossiers?limit=1")

    assert first["id"] == replay["id"]
    assert first["dossier_hash"] == replay["dossier_hash"]
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert fetched.status_code == 200
    assert fetched.json()["dossier_hash"] == first["dossier_hash"]
    assert listed.status_code == 200
    assert listed.json()["count"] == listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == first["id"]

    database = client.app.state.database
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute(
                "UPDATE provenance_dossiers SET qualification = 'DIVERSE' WHERE id = ?",
                (first["id"],),
            )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute(
                "DELETE FROM provenance_dossier_versions WHERE dossier_id = ?",
                (first["id"],),
            )
    with database.connect() as connection:
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE subject_id = ?", (first["id"],)
        ).fetchone()[0]
    assert audit_count == 1


def test_provenance_dossier_payload_is_strict_and_ids_are_validated(client):
    first = create_claim(client, "Provenance payload")["current_version"]["id"]
    assert client.post(
        "/provenance-dossiers", json={"claim_version_ids": []}
    ).status_code == 422
    assert client.post(
        "/provenance-dossiers", json={"claim_version_ids": [first, first]}
    ).status_code == 422
    assert client.post(
        "/provenance-dossiers",
        json={"claim_version_ids": [first], "score": 1, "provenance": "invented"},
    ).status_code == 422
    assert client.post(
        "/provenance-dossiers",
        json={"claim_version_ids": [f"missing-{number}" for number in range(21)]},
    ).status_code == 422
    assert client.post(
        "/provenance-dossiers", json={"claim_version_ids": ["missing"]}
    ).status_code == 404
    assert client.get("/provenance-dossiers/missing").status_code == 404
    assert client.get("/provenance-dossiers?limit=101").status_code == 422

    schema = client.app.openapi()
    request_schema = schema["components"]["schemas"]["ProvenanceDossierCreate"]
    assert request_schema["additionalProperties"] is False
    assert list(request_schema["properties"]) == ["claim_version_ids"]
    assert schema["info"]["version"] == "1.0.7"


def create_claim_with_confidence(
    client: TestClient, statement: str, declared_confidence: float | None
) -> dict:
    response = client.post(
        "/claims",
        json={
            "statement": statement,
            "context": "Contexte de confiance",
            "declared_confidence": declared_confidence,
        },
    )
    assert response.status_code == 201
    return response.json()


def create_trust_alignment_dossier(
    client: TestClient, claim_version_ids: list[str]
) -> dict:
    response = client.post(
        "/trust-alignment-dossiers",
        json={"claim_version_ids": claim_version_ids},
    )
    assert response.status_code == 201
    return response.json()


def add_weak_support_sources(client: TestClient, claim_id: str, prefix: str) -> None:
    for number in range(2):
        source = create_source(
            client,
            f"{prefix}-{number}",
            reliability=0.5,
            publisher=f"{prefix} Publisher {number}",
        )
        link_evidence(
            client,
            claim_id,
            source["id"],
            "SUPPORTS",
            f"Soutien faible {prefix} {number}.",
            0.5,
        )


def test_trust_alignment_is_insufficient_without_declared_confidence(client):
    claim = create_claim(client, "Confiance historique absente")
    add_support_sources(client, claim["id"], "trust-missing", 2)

    dossier = create_trust_alignment_dossier(
        client, [claim["current_version"]["id"]]
    )

    measure = dossier["claim_measures"][0]
    assert dossier["qualification"] == "INSUFFICIENT"
    assert measure["declared_confidence"] is None
    assert measure["evidentiary_confidence"] > 0
    assert measure["confidence_gap"] is None
    assert measure["eligible_for_alignment"] is False
    assert "aucune confiance déclarée" in measure["insufficiency_reasons"][0]


def test_trust_alignment_is_aligned_inside_fixed_tolerance(client):
    claim = create_claim_with_confidence(client, "Confiance alignée", 0.64)
    add_support_sources(client, claim["id"], "trust-aligned", 2)

    dossier = create_trust_alignment_dossier(
        client, [claim["current_version"]["id"]]
    )

    measure = dossier["claim_measures"][0]
    assert dossier["qualification"] == "ALIGNED"
    assert measure["evidentiary_confidence"] == pytest.approx(0.6426, abs=0.0001)
    assert abs(measure["confidence_gap"]) <= 0.10
    assert dossier["policy"]["alignment_absolute_tolerance"] == 0.10
    assert "ne prouve pas la vérité" in dossier["warning"]


def test_trust_alignment_uses_worst_claim_for_overconfidence(client):
    aligned = create_claim_with_confidence(client, "Côté aligné", 0.64)
    over = create_claim_with_confidence(client, "Côté surconfiant", 0.95)
    add_support_sources(client, aligned["id"], "trust-worst-aligned", 2)
    add_weak_support_sources(client, over["id"], "trust-worst-over")

    dossier = create_trust_alignment_dossier(
        client,
        [aligned["current_version"]["id"], over["current_version"]["id"]],
    )

    assert dossier["qualification"] == "OVERCONFIDENT"
    assert dossier["worst_claim"]["claim_version_id"] == over["current_version"]["id"]
    assert dossier["worst_claim"]["confidence_gap"] > 0.10
    assert dossier["metrics"]["maximum_absolute_gap"] == dossier["worst_claim"]["absolute_gap"]


def test_trust_alignment_detects_underconfidence(client):
    claim = create_claim_with_confidence(client, "Confiance trop basse", 0.10)
    add_support_sources(client, claim["id"], "trust-under", 2)

    dossier = create_trust_alignment_dossier(
        client, [claim["current_version"]["id"]]
    )

    assert dossier["qualification"] == "UNDERCONFIDENT"
    assert dossier["worst_claim"]["confidence_gap"] < -0.10


def test_trust_alignment_is_incompatible_for_selected_direct_contradiction(client):
    left = create_claim_with_confidence(client, "Confiance incompatible A", 0.64)
    right = create_claim_with_confidence(client, "Confiance incompatible B", 0.64)
    add_support_sources(client, left["id"], "trust-incompatible-left", 2)
    add_support_sources(client, right["id"], "trust-incompatible-right", 2)
    relation = link_claims(client, left["id"], right["id"], "CONTRADICTS")

    dossier = create_trust_alignment_dossier(
        client,
        [left["current_version"]["id"], right["current_version"]["id"]],
    )

    assert dossier["qualification"] == "INCOMPATIBLE"
    assert dossier["metrics"]["incompatible_pair_count"] == 1
    assert dossier["incompatible_pairs"][0]["relation_id"] == relation["id"]


def test_trust_alignment_is_insufficient_when_evidence_hash_is_invalid(client):
    claim = create_claim_with_confidence(client, "Confiance altérée", 0.64)
    add_support_sources(client, claim["id"], "trust-integrity", 2)
    database = client.app.state.database
    with database.connect() as connection:
        evidence_id = connection.execute("SELECT id FROM evidence ORDER BY id LIMIT 1").fetchone()[0]
        connection.execute(
            "UPDATE evidence SET strength = 0.1 WHERE id = ?", (evidence_id,)
        )

    dossier = create_trust_alignment_dossier(
        client, [claim["current_version"]["id"]]
    )

    assert dossier["qualification"] == "INSUFFICIENT"
    measure = dossier["claim_measures"][0]
    assert measure["evidence_integrity"]["valid"] is False
    assert measure["eligible_for_alignment"] is False


def test_trust_alignment_reloads_frozen_coverage_and_provenance(client):
    claim = create_claim_with_confidence(client, "Confiance V1", 0.95)
    add_support_sources(client, claim["id"], "trust-frozen", 3)
    selected_id = claim["current_version"]["id"]
    coverage = create_coverage_report(client, claim["id"])
    provenance = create_provenance_dossier(client, [selected_id])
    response = client.post(
        f"/claims/{claim['id']}/versions",
        json={
            "statement": "Confiance V2",
            "declared_confidence": 0.50,
            "expected_current_version": 1,
        },
    )
    assert response.status_code == 201

    dossier = create_trust_alignment_dossier(client, [selected_id])
    measure = dossier["claim_measures"][0]

    assert measure["selection_state"] == "FROZEN"
    assert measure["declared_confidence"] == 0.95
    assert measure["current_version_number"] == 2
    assert measure["coverage_report"]["id"] == coverage["id"]
    assert measure["provenance_dossier"]["id"] == provenance["id"]
    assert dossier["metrics"]["coverage_report_count"] == 1
    assert dossier["metrics"]["provenance_dossier_count"] == 1


def test_trust_alignment_is_order_independent_immutable_audited_and_listed(client):
    first_claim = create_claim(client, "Confiance dossier A")
    second_claim = create_claim(client, "Confiance dossier B")
    first_id = first_claim["current_version"]["id"]
    second_id = second_claim["current_version"]["id"]
    first = create_trust_alignment_dossier(client, [first_id, second_id])
    replay = create_trust_alignment_dossier(client, [second_id, first_id])
    fetched = client.get(f"/trust-alignment-dossiers/{first['id']}")
    listed = client.get("/trust-alignment-dossiers?limit=1")

    assert first["id"] == replay["id"]
    assert first["dossier_hash"] == replay["dossier_hash"]
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert fetched.status_code == 200
    assert fetched.json()["dossier_hash"] == first["dossier_hash"]
    assert listed.status_code == 200
    assert listed.json()["count"] == listed.json()["total"] == 1

    database = client.app.state.database
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute(
                "UPDATE trust_alignment_dossiers SET qualification = 'ALIGNED' WHERE id = ?",
                (first["id"],),
            )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute(
                "DELETE FROM trust_alignment_dossier_versions WHERE dossier_id = ?",
                (first["id"],),
            )
    with database.connect() as connection:
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE subject_id = ?", (first["id"],)
        ).fetchone()[0]
    assert audit_count == 1


def test_trust_alignment_payload_and_declared_confidence_are_strict(client):
    claim = create_claim_with_confidence(client, "Confiance payload", 0.5)
    version_id = claim["current_version"]["id"]
    assert claim["current_version"]["declared_confidence"] == 0.5
    assert client.post(
        "/claims", json={"statement": "Confiance invalide", "declared_confidence": 1.1}
    ).status_code == 422
    assert client.post(
        "/trust-alignment-dossiers", json={"claim_version_ids": []}
    ).status_code == 422
    assert client.post(
        "/trust-alignment-dossiers", json={"claim_version_ids": [version_id, version_id]}
    ).status_code == 422
    assert client.post(
        "/trust-alignment-dossiers",
        json={"claim_version_ids": [version_id], "result": "ALIGNED", "score": 1},
    ).status_code == 422
    assert client.post(
        "/trust-alignment-dossiers",
        json={"claim_version_ids": [f"missing-{number}" for number in range(21)]},
    ).status_code == 422
    assert client.post(
        "/trust-alignment-dossiers", json={"claim_version_ids": ["missing"]}
    ).status_code == 404
    assert client.get("/trust-alignment-dossiers/missing").status_code == 404
    assert client.get("/trust-alignment-dossiers?limit=101").status_code == 422

    schema = client.app.openapi()
    request_schema = schema["components"]["schemas"]["TrustAlignmentDossierCreate"]
    assert request_schema["additionalProperties"] is False
    assert list(request_schema["properties"]) == ["claim_version_ids"]
    assert schema["info"]["version"] == "1.0.7"


def create_evolution_claim(client: TestClient, confidences=(0.6, 0.6)) -> tuple[dict, list[dict]]:
    claim = create_claim_with_confidence(client, "Évolution chronologique V1", confidences[0])
    versions = [claim["current_version"]]
    for index, confidence in enumerate(confidences[1:], start=2):
        response = client.post(
            f"/claims/{claim['id']}/versions",
            json={
                "statement": f"Évolution chronologique V{index}",
                "context": "Contexte chronologique",
                "declared_confidence": confidence,
                "change_note": f"Transition {index - 1} vers {index}",
                "expected_current_version": index - 1,
            },
        )
        assert response.status_code == 201
        versions.append(response.json())
    return claim, versions


def add_version_evidence(
    client: TestClient,
    claim_id: str,
    version_number: int,
    relation: str,
    prefix: str,
    count: int = 2,
) -> list[dict]:
    linked = []
    for number in range(count):
        source = create_source(
            client,
            f"{prefix}-{version_number}-{number}",
            reliability=0.9,
            publisher=f"{prefix} Publisher {version_number}-{number}",
        )
        response = client.post(
            f"/claims/{claim_id}/evidence",
            json={
                "source_id": source["id"],
                "relation": relation,
                "excerpt": f"{prefix} version {version_number} preuve {number}",
                "locator": "section évolution",
                "strength": 0.9,
                "claim_version_number": version_number,
            },
        )
        assert response.status_code == 201
        linked.append(response.json())
    return linked


def create_evolution_dossier(client: TestClient, version_ids: list[str]) -> dict:
    response = client.post(
        "/claim-evolution-dossiers", json={"claim_version_ids": version_ids}
    )
    assert response.status_code == 201
    return response.json()


def test_claim_evolution_stable_and_chronologically_ordered(client):
    claim, versions = create_evolution_claim(client, (0.65, 0.65, 0.65))
    for number in (1, 2, 3):
        add_version_evidence(client, claim["id"], number, "SUPPORTS", "stable")
    dossier = create_evolution_dossier(
        client, [versions[2]["id"], versions[0]["id"], versions[1]["id"]]
    )
    assert dossier["qualification"] == "STABLE"
    assert [item["version_number"] for item in dossier["versions"]] == [1, 2, 3]
    assert all(not item["direction_changed"] for item in dossier["transitions"])
    assert dossier["policy"]["material_absolute_delta"] == 0.10


def test_claim_evolution_escalates_on_contradiction_and_direction_change(client):
    claim, versions = create_evolution_claim(client)
    add_version_evidence(client, claim["id"], 1, "SUPPORTS", "escalate-support")
    add_version_evidence(client, claim["id"], 2, "CONTRADICTS", "escalate-contradict")
    dossier = create_evolution_dossier(client, [item["id"] for item in versions])
    transition = dossier["transitions"][0]
    assert dossier["qualification"] == "ESCALATING"
    assert transition["contradiction_delta"] >= 0.10
    assert transition["direction_changed"] is True
    assert transition["escalating"] is True
    assert dossier["worst_transition"] == transition


def test_claim_evolution_resolving_when_contradictions_recede(client):
    claim, versions = create_evolution_claim(client)
    add_version_evidence(client, claim["id"], 1, "CONTRADICTS", "resolve-contradict")
    add_version_evidence(client, claim["id"], 2, "SUPPORTS", "resolve-support")
    dossier = create_evolution_dossier(client, [item["id"] for item in versions])
    assert dossier["qualification"] == "RESOLVING"
    assert dossier["transitions"][0]["resolving"] is True
    assert dossier["transitions"][0]["contradiction_delta"] <= -0.10


def test_claim_evolution_is_insufficient_without_usable_evidence(client):
    claim, versions = create_evolution_claim(client)
    add_version_evidence(client, claim["id"], 1, "SUPPORTS", "partial")
    dossier = create_evolution_dossier(client, [item["id"] for item in versions])
    assert dossier["qualification"] == "INSUFFICIENT"
    assert dossier["versions"][1]["usable_evidence_count"] == 0


def test_claim_evolution_cross_claim_selection_is_incompatible(client):
    first, first_versions = create_evolution_claim(client)
    second, second_versions = create_evolution_claim(client, (0.7, 0.7))
    add_version_evidence(client, first["id"], 1, "SUPPORTS", "compatible-a")
    add_version_evidence(client, second["id"], 1, "SUPPORTS", "compatible-b")
    dossier = create_evolution_dossier(
        client, [first_versions[0]["id"], second_versions[0]["id"]]
    )
    assert dossier["qualification"] == "INCOMPATIBLE"
    assert dossier["claim_id"] is None


def test_claim_evolution_tracks_declared_derived_alignment_and_coverage(client):
    claim, versions = create_evolution_claim(client, (0.95, 0.20))
    for number in (1, 2):
        add_version_evidence(client, claim["id"], number, "SUPPORTS", "measures")
    create_coverage_report(client, claim["id"])
    dossier = create_evolution_dossier(client, [item["id"] for item in versions])
    assert dossier["versions"][0]["declared_confidence"] == 0.95
    assert dossier["versions"][0]["evidentiary_confidence"] > 0
    assert dossier["versions"][0]["alignment"] == "OVERCONFIDENT"
    assert dossier["versions"][1]["coverage_report"] is not None
    assert dossier["metrics"]["coverage_report_count"] == 1
    assert dossier["transitions"][0]["declared_confidence_delta"] == pytest.approx(-0.75)


def test_claim_evolution_explicit_contradiction_increase_escalates(client):
    claim, versions = create_evolution_claim(client)
    for number in (1, 2):
        add_version_evidence(client, claim["id"], number, "SUPPORTS", "explicit")
    other = create_claim(client, "Affirmation contradictoire externe")
    response = client.post(
        f"/claims/{claim['id']}/relations",
        json={
            "related_claim_id": other["id"],
            "relation": "CONTRADICTS",
            "from_version_number": 2,
        },
    )
    assert response.status_code == 201
    dossier = create_evolution_dossier(client, [item["id"] for item in versions])
    assert dossier["qualification"] == "ESCALATING"
    assert dossier["transitions"][0]["explicit_contradiction_count_delta"] == 1


def test_claim_evolution_detects_tampered_evidence_hash(client):
    claim, versions = create_evolution_claim(client)
    for number in (1, 2):
        add_version_evidence(client, claim["id"], number, "SUPPORTS", "tampered")
    database = client.app.state.database
    with database.connect() as connection:
        evidence_id = connection.execute(
            "SELECT id FROM evidence ORDER BY created_at, id LIMIT 1"
        ).fetchone()[0]
        connection.execute("UPDATE evidence SET strength = 0.1 WHERE id = ?", (evidence_id,))
    dossier = create_evolution_dossier(client, [item["id"] for item in versions])
    assert dossier["qualification"] == "INSUFFICIENT"
    assert dossier["versions"][0]["evidence_integrity"]["valid"] is False


def test_claim_evolution_is_order_independent_immutable_audited_and_listed(client):
    claim, versions = create_evolution_claim(client)
    for number in (1, 2):
        add_version_evidence(client, claim["id"], number, "SUPPORTS", "immutable")
    first = create_evolution_dossier(client, [item["id"] for item in versions])
    replay = create_evolution_dossier(client, [item["id"] for item in reversed(versions)])
    assert replay["id"] == first["id"] and replay["idempotent_replay"] is True
    assert client.get(f"/claim-evolution-dossiers/{first['id']}").json()["dossier_hash"] == first["dossier_hash"]
    assert client.get("/claim-evolution-dossiers?limit=1").json()["total"] == 1
    database = client.app.state.database
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute(
                "UPDATE claim_evolution_dossiers SET qualification = 'STABLE' WHERE id = ?",
                (first["id"],),
            )
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE subject_id = ?", (first["id"],)
        ).fetchone()[0] == 1


def test_claim_evolution_payload_is_strict_unique_bounded_and_missing_safe(client):
    claim, versions = create_evolution_claim(client)
    ids = [item["id"] for item in versions]
    assert client.post("/claim-evolution-dossiers", json={"claim_version_ids": [ids[0]]}).status_code == 422
    assert client.post("/claim-evolution-dossiers", json={"claim_version_ids": [ids[0], ids[0]]}).status_code == 422
    assert client.post(
        "/claim-evolution-dossiers",
        json={"claim_version_ids": ids, "qualification": "STABLE", "score": 1},
    ).status_code == 422
    assert client.post(
        "/claim-evolution-dossiers",
        json={"claim_version_ids": [f"missing-{number}" for number in range(51)]},
    ).status_code == 422
    assert client.post(
        "/claim-evolution-dossiers", json={"claim_version_ids": [ids[0], "missing"]}
    ).status_code == 404
    assert client.get("/claim-evolution-dossiers/missing").status_code == 404
    assert client.get("/claim-evolution-dossiers?limit=101").status_code == 422
    schema = client.app.openapi()["components"]["schemas"]["ClaimEvolutionDossierCreate"]
    assert schema["additionalProperties"] is False
    assert list(schema["properties"]) == ["claim_version_ids"]


def create_gap_claim(client: TestClient, version_count: int = 2) -> tuple[dict, list[dict]]:
    claim = create_claim(client, "Couverture probante V1")
    versions = [claim["current_version"]]
    for number in range(2, version_count + 1):
        response = client.post(
            f"/claims/{claim['id']}/versions",
            json={
                "statement": f"Couverture probante V{number}",
                "change_note": f"Version {number}",
                "expected_current_version": number - 1,
            },
        )
        assert response.status_code == 201
        versions.append(response.json())
    return claim, versions


def add_gap_evidence(
    client: TestClient,
    claim_id: str,
    version_number: int,
    relation: str = "SUPPORTS",
    *,
    prefix: str = "gap",
    published_at: str | None = None,
) -> dict:
    source = create_source(
        client,
        f"{prefix}-{version_number}-{relation.lower()}",
        published_at=published_at,
        publisher=f"{prefix} Publisher {version_number}",
    )
    response = client.post(
        f"/claims/{claim_id}/evidence",
        json={
            "source_id": source["id"],
            "excerpt": f"Preuve {relation} pour version {version_number}",
            "locator": "section lacunes",
            "strength": 0.9,
            "relation": relation,
            "claim_version_number": version_number,
        },
    )
    assert response.status_code == 201
    return response.json()


def create_gap_dossier(
    client: TestClient,
    claim_id: str,
    version_ids: list[str],
    evidence_ids: list[str] | None = None,
) -> dict:
    response = client.post(
        "/v1/evidence-gap-dossiers",
        json={
            "claim_id": claim_id,
            "claim_version_ids": version_ids,
            "evidence_ids": evidence_ids or [],
        },
    )
    assert response.status_code == 201
    return response.json()


def test_evidence_gap_dossier_is_covered_and_server_ordered(client):
    claim, versions = create_gap_claim(client, 3)
    for number in (1, 2, 3):
        add_gap_evidence(client, claim["id"], number, prefix="covered")
    dossier = create_gap_dossier(
        client, claim["id"], [versions[2]["id"], versions[0]["id"], versions[1]["id"]]
    )
    assert dossier["qualification"] == "COVERED"
    assert [item["version_number"] for item in dossier["versions"]] == [1, 2, 3]
    assert dossier["metrics"]["supported_version_count"] == 3
    assert dossier["metrics"]["total_gap_count"] == 0
    assert dossier["policy"]["covered_proves_truth"] is False
    assert "ne prouve" in dossier["warning"]


def test_evidence_gap_dossier_tracks_new_and_resolved_gaps(client):
    claim, versions = create_gap_claim(client, 3)
    add_gap_evidence(client, claim["id"], 1, "CONTRADICTS", prefix="transition")
    add_gap_evidence(client, claim["id"], 2, "SUPPORTS", prefix="transition")
    add_gap_evidence(client, claim["id"], 3, "CONTRADICTS", prefix="transition-new")
    dossier = create_gap_dossier(client, claim["id"], [item["id"] for item in versions])
    assert dossier["qualification"] == "GAPPED"
    assert "CONTRADICTORY_EVIDENCE" in dossier["transitions"][0]["resolved_gaps"]
    assert "CONTRADICTORY_EVIDENCE" in dossier["transitions"][1]["new_gaps"]
    assert dossier["metrics"]["new_gap_count"] > 0
    assert dossier["metrics"]["resolved_gap_count"] > 0
    assert dossier["worst_transition"]["to_gap_count"] >= 1


def test_evidence_gap_dossier_is_insufficient_when_proof_is_missing(client):
    claim, versions = create_gap_claim(client)
    add_gap_evidence(client, claim["id"], 1, prefix="missing-proof")
    dossier = create_gap_dossier(client, claim["id"], [item["id"] for item in versions])
    assert dossier["qualification"] == "INSUFFICIENT"
    assert dossier["metrics"]["unsourced_version_count"] == 1
    assert dossier["versions"][1]["gap_codes"] == ["MISSING_EVIDENCE"]


def test_evidence_gap_dossier_detects_stale_sources_without_inventing_access(client):
    claim, versions = create_gap_claim(client)
    for number in (1, 2):
        add_gap_evidence(
            client, claim["id"], number, prefix="stale",
            published_at="2020-01-01T00:00:00Z",
        )
    dossier = create_gap_dossier(client, claim["id"], [item["id"] for item in versions])
    assert dossier["qualification"] == "GAPPED"
    assert dossier["metrics"]["stale_source_count"] == 2
    assert dossier["metrics"]["inaccessible_source_count"] == 0
    assert all(
        evidence["source_accessibility_state"] == "UNKNOWN_NOT_PERSISTED"
        for version in dossier["versions"] for evidence in version["evidence"]
    )


def test_evidence_gap_dossier_exposes_missing_dependencies_and_witnesses(client):
    claim, versions = create_gap_claim(client)
    other = create_claim(client, "Dépendance externe")
    for number in (1, 2):
        add_gap_evidence(client, claim["id"], number, prefix="dependency")
    response = client.post(
        f"/claims/{claim['id']}/relations",
        json={
            "related_claim_id": other["id"],
            "relation": "SUPPORTS",
            "from_version_number": 1,
        },
    )
    assert response.status_code == 201
    dossier = create_gap_dossier(client, claim["id"], [item["id"] for item in versions])
    assert dossier["qualification"] == "GAPPED"
    assert dossier["metrics"]["missing_dependency_count"] == 1
    assert "MISSING_DEPENDENCY" in dossier["versions"][0]["gap_codes"]
    witness = next(item for item in dossier["versions"][0]["witnesses"] if item["code"] == "MISSING_DEPENDENCY")
    assert witness["missing_claim_version_ids"] == [other["current_version"]["id"]]


def test_evidence_gap_dossier_cross_claim_versions_are_incompatible(client):
    first, first_versions = create_gap_claim(client)
    second, second_versions = create_gap_claim(client)
    dossier = create_gap_dossier(
        client, first["id"], [first_versions[0]["id"], second_versions[0]["id"]]
    )
    assert dossier["qualification"] == "INCOMPATIBLE"


def test_evidence_gap_dossier_strict_evidence_selection_and_incompatibility(client):
    claim, versions = create_gap_claim(client)
    first = add_gap_evidence(client, claim["id"], 1, prefix="selected")
    add_gap_evidence(client, claim["id"], 2, prefix="selected")
    other = create_claim(client, "Preuve hors sélection")
    outside = add_gap_evidence(client, other["id"], 1, prefix="outside")
    dossier = create_gap_dossier(
        client,
        claim["id"],
        [item["id"] for item in versions],
        [first["evidence"]["id"], outside["evidence"]["id"]],
    )
    assert dossier["qualification"] == "INCOMPATIBLE"
    assert dossier["incompatible_evidence_ids"] == [outside["evidence"]["id"]]


def test_evidence_gap_dossier_invalid_hash_is_insufficient(client):
    claim, versions = create_gap_claim(client)
    for number in (1, 2):
        add_gap_evidence(client, claim["id"], number, prefix="integrity-gap")
    database = client.app.state.database
    with database.connect() as connection:
        evidence_id = connection.execute(
            "SELECT id FROM evidence ORDER BY created_at, id LIMIT 1"
        ).fetchone()[0]
        connection.execute("UPDATE evidence SET strength = 0.1 WHERE id = ?", (evidence_id,))
    dossier = create_gap_dossier(client, claim["id"], [item["id"] for item in versions])
    assert dossier["qualification"] == "INSUFFICIENT"
    assert any(item["invalid_hash_evidence_ids"] for item in dossier["versions"])


def test_evidence_gap_dossier_is_order_independent_immutable_audited_and_listed(client):
    claim, versions = create_gap_claim(client)
    linked = [add_gap_evidence(client, claim["id"], number, prefix="immutable-gap") for number in (1, 2)]
    evidence_ids = [item["evidence"]["id"] for item in linked]
    first = create_gap_dossier(client, claim["id"], [item["id"] for item in versions], evidence_ids)
    replay = create_gap_dossier(client, claim["id"], [item["id"] for item in reversed(versions)], list(reversed(evidence_ids)))
    assert replay["id"] == first["id"] and replay["idempotent_replay"] is True
    assert client.get(f"/v1/evidence-gap-dossiers/{first['id']}").json()["dossier_hash"] == first["dossier_hash"]
    assert client.get("/v1/evidence-gap-dossiers?limit=1").json()["total"] == 1
    database = client.app.state.database
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute(
                "UPDATE evidence_gap_dossiers SET qualification = 'GAPPED' WHERE id = ?",
                (first["id"],),
            )
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE subject_id = ?", (first["id"],)
        ).fetchone()[0] == 1


def test_evidence_gap_payload_is_strict_bounded_and_unknown_references_refused(client):
    claim, versions = create_gap_claim(client)
    base = {"claim_id": claim["id"], "claim_version_ids": [item["id"] for item in versions], "evidence_ids": []}
    assert client.post(
        "/v1/evidence-gap-dossiers", json={**base, "qualification": "COVERED", "result": {}}
    ).status_code == 422
    assert client.post(
        "/v1/evidence-gap-dossiers",
        json={"claim_id": claim["id"], "claim_version_ids": [versions[0]["id"]], "evidence_ids": []},
    ).status_code == 422
    assert client.post(
        "/v1/evidence-gap-dossiers",
        json={"claim_id": claim["id"], "claim_version_ids": [str(index) for index in range(100)], "evidence_ids": ["extra"]},
    ).status_code == 422
    assert client.post(
        "/v1/evidence-gap-dossiers",
        json={**base, "evidence_ids": ["missing-evidence"]},
    ).status_code == 404
    assert client.post(
        "/v1/evidence-gap-dossiers", json={**base, "claim_id": "missing-claim"}
    ).status_code == 404
    assert client.get("/v1/evidence-gap-dossiers/missing").status_code == 404
    assert client.get("/v1/evidence-gap-dossiers?limit=101").status_code == 422
    schema = client.app.openapi()["components"]["schemas"]["EvidenceGapDossierCreate"]
    assert schema["additionalProperties"] is False
    assert list(schema["properties"]) == ["claim_id", "claim_version_ids", "evidence_ids"]
