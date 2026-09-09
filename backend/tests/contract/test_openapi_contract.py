from src.main import app

EXPECTED_ROUTES: dict[str, set[str]] = {
    # Meetings
    "/api/v1/meetings": {"get", "post"},
    "/api/v1/meetings/upload": {"post"},
    "/api/v1/meetings/search/content": {"get"},
    # Sessions
    "/api/v1/sessions": {"get"},
    "/api/v1/sessions/batch-delete": {"post"},
    "/api/v1/sessions/summaries": {"get"},
    "/api/v1/sessions/search": {"post"},
    # Chat
    "/api/v1/chat": {"post"},
    "/api/v1/chat/stream": {"post"},
    "/api/v1/chat/search": {"post"},
    # Memory
    "/api/v1/memory": {"get", "post", "put", "delete"},
    "/api/v1/memory/batch": {"post"},
    "/api/v1/memory/batch-delete": {"post"},
    "/api/v1/memory/export": {"get"},
    "/api/v1/memory/search": {"post"},
    "/api/v1/memory/decay": {"post"},
    "/api/v1/memory/entities": {"get"},
    "/api/v1/memory/entities/batch-delete": {"post"},
    "/api/v1/memory/entities/merge": {"post"},
    # Settings
    "/api/v1/settings": {"get", "put"},
    "/api/v1/settings/bindings": {"get"},
    "/api/v1/settings/rebuild-vectors": {"post"},
    "/api/v1/settings/rebuild-multimodal": {"post"},
    "/api/v1/settings/reload-config": {"post"},
    # Health
    "/api/v1/health": {"get"},
    "/api/v1/health/live": {"get"},
    "/api/v1/health/ready": {"get"},
    # Skills
    "/api/v1/skills": {"get", "post"},
    "/api/v1/skills/invoke": {"post"},
    "/api/v1/skills/match": {"post"},
    # File download
    "/api/v1/meetings/file-token": {"post"},
    "/api/v1/meetings/assets": {"get"},
}


def test_openapi_schema_has_core_routes():
    schema = app.openapi()
    paths = schema["paths"]
    for path in ["/api/v1/chat", "/api/v1/meetings/upload", "/api/v1/settings"]:
        assert path in paths


def test_expected_routes_exist_with_correct_methods():
    schema = app.openapi()
    paths = schema["paths"]
    missing: list[str] = []
    method_mismatches: list[str] = []

    for path, expected_methods in EXPECTED_ROUTES.items():
        if path not in paths:
            missing.append(path)
            continue
        actual_methods = {m for m in paths[path] if m in ("get", "post", "put", "delete", "patch")}
        if not expected_methods.issubset(actual_methods):
            method_mismatches.append(f"{path}: expected {expected_methods}, got {actual_methods}")

    assert not missing, f"Missing routes: {missing}"
    assert not method_mismatches, f"Method mismatches: {method_mismatches}"


def test_key_endpoints_have_response_schemas():
    schema = app.openapi()
    paths = schema["paths"]
    endpoints_with_expected_schema = [
        ("/api/v1/chat", "post"),
        ("/api/v1/sessions", "get"),
        ("/api/v1/memory", "get"),
        ("/api/v1/settings", "get"),
        ("/api/v1/skills", "get"),
    ]
    missing_schemas: list[str] = []

    for path, method in endpoints_with_expected_schema:
        op = paths.get(path, {}).get(method, {})
        responses = op.get("responses", {})
        ok_response = responses.get("200", responses.get("201", {}))
        content = ok_response.get("content", {})
        if not content:
            missing_schemas.append(f"{path} {method.upper()}")

    assert not missing_schemas, f"Endpoints missing response schemas: {missing_schemas}"


def test_parameterized_routes_exist():
    schema = app.openapi()
    paths = schema["paths"]
    param_routes = [
        "/api/v1/meetings/{meeting_id}",
        "/api/v1/sessions/{session_id}",
        "/api/v1/memory/entities/{name}",
    ]
    missing = [r for r in param_routes if r not in paths]
    assert not missing, f"Missing parameterized routes: {missing}"


async def test_validation_responses_match_the_published_contract(auth_headers):
    from httpx import ASGITransport, AsyncClient
    from jsonschema import Draft202012Validator

    schema = app.openapi()
    cases = [
        ("get", "/api/v1/memory", "/api/v1/memory?cursor=null", None),
        ("get", "/api/v1/memory/export", "/api/v1/memory/export?cursor=null", None),
        (
            "patch",
            "/api/v1/meetings/{meeting_id}/files/{file_id}/semantics",
            "/api/v1/meetings/1/files/1/semantics",
            {},
        ),
        ("get", "/api/v1/memory", "/api/v1/memory?limit=invalid", None),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for method, path, url, body in cases:
            response = await client.request(method, url, json=body, headers=auth_headers)
            assert response.status_code == 422
            response_schema = schema["paths"][path][method]["responses"]["422"]["content"][
                "application/json"
            ]["schema"]
            Draft202012Validator({"components": schema["components"], **response_schema}).validate(
                response.json()
            )
