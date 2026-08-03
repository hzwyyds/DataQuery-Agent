from __future__ import annotations

import re
import shutil
from pathlib import Path
from uuid import UUID

from app.core.config import Settings, settings

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def validate_id(value: str) -> str:
    return str(UUID(value))


def safe_filename(value: str) -> str:
    original = Path(value).name
    suffix = Path(original).suffix.lower()
    stem = _SAFE_NAME.sub("_", Path(original).stem).strip("._") or "dataset"
    max_stem_length = max(1, 160 - len(suffix))
    return f"{stem[:max_stem_length]}{suffix}"


class WorkspaceStorage:
    def __init__(self, config: Settings = settings):
        self.config = config
        self.root = config.data_dir.resolve()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "workspaces").mkdir(exist_ok=True)

    def workspace_dir(self, workspace_id: str) -> Path:
        path = (self.root / "workspaces" / validate_id(workspace_id)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("workspace path escaped the data directory")
        return path

    def source_dir(self, workspace_id: str, source_id: str) -> Path:
        path = self.workspace_dir(workspace_id) / "sources" / validate_id(source_id)
        return path.resolve()

    def warehouse_path(self, workspace_id: str) -> Path:
        return self.workspace_dir(workspace_id) / "warehouse.duckdb"

    def workspace_bytes(self, workspace_id: str) -> int:
        root = self.workspace_dir(workspace_id)
        if not root.exists():
            return 0
        return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())

    def remove_workspace(self, workspace_id: str) -> None:
        path = self.workspace_dir(workspace_id)
        if path.exists():
            shutil.rmtree(path)

    def remove_source(self, workspace_id: str, source_id: str) -> None:
        path = self.source_dir(workspace_id, source_id)
        if path.exists():
            shutil.rmtree(path)
