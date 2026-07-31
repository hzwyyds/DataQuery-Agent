from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.routes as routes
import app.main as main_module
from app.core.config import Settings
from app.data.ingestion import IngestionService
from app.data.repository import Repository
from app.data.storage import WorkspaceStorage
from app.rag.service import RAGService


def test_workspace_upload_catalog_and_delete(tmp_path: Path, monkeypatch) -> None:
    config = Settings(data_dir=tmp_path)
    repository = Repository(tmp_path / "metadata.sqlite3")
    storage = WorkspaceStorage(config)
    ingestion = IngestionService(repository, storage)
    monkeypatch.setattr(routes, "repository", repository)
    monkeypatch.setattr(routes, "storage", storage)
    monkeypatch.setattr(routes, "ingestion", ingestion)
    monkeypatch.setattr(routes, "rag", RAGService(repository, config))
    monkeypatch.setattr(main_module, "repository", repository)
    monkeypatch.setattr(main_module, "WorkspaceStorage", lambda: storage)

    def unconfigured_runtime():
        raise RuntimeError("LLM_API_KEY is required")

    monkeypatch.setattr(routes, "agent_runtime", unconfigured_runtime)

    with TestClient(main_module.app) as client:
        created = client.post(
            "/api/v1/workspaces",
            json={"name": "Retail workspace", "description": "API test"},
        )
        assert created.status_code == 201
        workspace_id = created.json()["id"]

        run = client.post(
            f"/api/v1/workspaces/{workspace_id}/runs",
            json={"question": "Show sales by region"},
        )
        assert run.status_code == 202
        assert run.json()["status"] == "FAILED"
        run_id = run.json()["id"]
        assert client.get(f"/api/v1/workspaces/{workspace_id}/runs/{run_id}").status_code == 200
        history = client.get(f"/api/v1/workspaces/{workspace_id}/runs").json()["runs"]
        assert history[0]["error_code"] == "CONFIGURATION_ERROR"
        events = client.get(
            f"/api/v1/workspaces/{workspace_id}/runs/{run_id}/events",
            headers={"Last-Event-ID": "0"},
        )
        assert events.status_code == 200
        assert "event: failed" in events.text
        assert "id: 1" in events.text

        uploaded = client.post(
            f"/api/v1/workspaces/{workspace_id}/files",
            files={
                "file": (
                    "orders.csv",
                    b"region,sales_amount\nEast,120.5\nWest,80\n",
                    "text/csv",
                )
            },
        )
        assert uploaded.status_code == 201, uploaded.text
        source_id = uploaded.json()["id"]

        catalog = client.get(f"/api/v1/workspaces/{workspace_id}/catalog")
        assert catalog.status_code == 200
        assert catalog.json()["tables"][0]["row_count"] == 2
        column_id = catalog.json()["tables"][0]["columns"][0]["id"]
        annotation = client.patch(
            f"/api/v1/workspaces/{workspace_id}/catalog/columns/{column_id}",
            json={"description": "Sales territory", "aliases": ["market", "market"]},
        )
        assert annotation.status_code == 200
        assert annotation.json()["aliases"] == ["market"]

        monkeypatch.setattr(routes, "settings", replace(routes.settings, max_file_bytes=8))
        too_large = client.post(
            f"/api/v1/workspaces/{workspace_id}/files",
            files={"file": ("too-large.csv", b"region\nEast\n", "text/csv")},
        )
        assert too_large.status_code == 413
        assert too_large.json()["detail"] == "单个文件不能超过 50 MB"

        deleted = client.delete(f"/api/v1/workspaces/{workspace_id}/sources/{source_id}")
        assert deleted.status_code == 204
        assert client.get(f"/api/v1/workspaces/{workspace_id}/catalog").json() == {"tables": []}
