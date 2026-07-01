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

    with TestClient(main_module.app) as client:
        created = client.post(
            "/api/v1/workspaces",
            json={"name": "Retail workspace", "description": "API test"},
        )
        assert created.status_code == 201
        workspace_id = created.json()["id"]

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

        deleted = client.delete(f"/api/v1/workspaces/{workspace_id}/sources/{source_id}")
        assert deleted.status_code == 204
        assert client.get(f"/api/v1/workspaces/{workspace_id}/catalog").json() == {"tables": []}
