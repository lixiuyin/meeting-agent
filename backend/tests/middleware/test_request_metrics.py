"""Request metric labels must remain bounded to route templates."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import middleware


@pytest.mark.unit
def test_http_metric_uses_route_template(monkeypatch):
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def item(item_id: str):
        return {"id": item_id}

    app.add_middleware(middleware.RequestIdMiddleware)
    monkeypatch.setattr(middleware, "_METRICS_ENABLED", True)
    with patch("src.core.metrics.HTTP_REQUEST_DURATION") as metric:
        response = TestClient(app).get("/items/unbounded-user-value")

    assert response.status_code == 200
    metric.labels.assert_called_once_with(method="GET", path="/items/{item_id}", status="200")


@pytest.mark.unit
def test_unmatched_paths_share_one_metric_label(monkeypatch):
    app = FastAPI()
    app.add_middleware(middleware.RequestIdMiddleware)
    monkeypatch.setattr(middleware, "_METRICS_ENABLED", True)
    with patch("src.core.metrics.HTTP_REQUEST_DURATION") as metric:
        response = TestClient(app).get("/attacker-controlled-404")

    assert response.status_code == 404
    metric.labels.assert_called_once_with(method="GET", path="unmatched", status="404")


@pytest.mark.unit
def test_urlencoded_forms_are_rejected_before_parsing():
    app = FastAPI()

    @app.post("/form")
    async def form_endpoint():
        return {"unexpected": True}

    app.add_middleware(middleware.SupportedContentTypeMiddleware)
    response = TestClient(app).post(
        "/form",
        content="a=1&b=2",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 415
