from __future__ import annotations

import json

from fastapi.testclient import TestClient

from src.api.app import APP_VERSION, GRAPH_VERSION, create_app
from src.api.dependencies import WorkflowConfigurationError, get_workflow
from src.recovery.contracts import MAX_RETRY
from src.recovery.evidence_fusion import EVIDENCE_BUDGET, FUSION_POLICY
from src.retrieval.rrf import DEFAULT_RRF_K


def test_health_and_version_do_not_initialize_external_runtime() -> None:
    application = create_app()

    def must_not_initialize():
        raise AssertionError("system endpoints must not initialize the workflow")

    application.dependency_overrides[get_workflow] = must_not_initialize
    with TestClient(application) as client:
        health = client.get("/health")
        version = client.get("/version")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert version.status_code == 200
    assert version.json() == {
        "app_version": APP_VERSION,
        "graph_version": GRAPH_VERSION,
        "recovery_max_retry": MAX_RETRY,
        "literature_top_k": EVIDENCE_BUDGET,
        "evidence_fusion_policy": FUSION_POLICY,
        "fusion_rrf_k": DEFAULT_RRF_K,
    }


def test_swagger_redoc_and_openapi_schema_are_available_and_secret_free(
    monkeypatch,
) -> None:
    postgres_secret = "postgres-password-must-never-appear"
    openai_secret = "sk-api-secret-must-never-appear"
    monkeypatch.setenv("PGPASSWORD", postgres_secret)
    monkeypatch.setenv("OPENAI_API_KEY", openai_secret)
    application = create_app()

    with TestClient(application) as client:
        swagger = client.get("/docs")
        redoc = client.get("/redoc")
        schema_response = client.get("/openapi.json")

    assert swagger.status_code == 200
    assert redoc.status_code == 200
    assert schema_response.status_code == 200
    schema = schema_response.json()
    assert set(("/health", "/version", "/query")) <= set(schema["paths"])
    assert schema["paths"]["/query"]["post"]["summary"]
    assert schema["components"]["schemas"]["QueryRequest"]
    assert schema["components"]["schemas"]["QueryResponse"]
    serialized = json.dumps(schema, ensure_ascii=False)
    assert postgres_secret not in serialized
    assert openai_secret not in serialized
    assert "PGPASSWORD" not in serialized
    assert "OPENAI_API_KEY" not in serialized


def test_missing_runtime_configuration_is_sanitized() -> None:
    application = create_app()

    def unavailable():
        raise WorkflowConfigurationError(
            "OPENAI_API_KEY and an imaginary secret value"
        )

    application.dependency_overrides[get_workflow] = unavailable
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post("/query", json={"question": "문헌을 검색해줘"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Agentic RAG runtime is not available."
    }
    assert "imaginary secret" not in response.text
