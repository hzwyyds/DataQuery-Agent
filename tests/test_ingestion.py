import asyncio
from pathlib import Path
from uuid import uuid4

import pandas as pd
import xlwt

from app.core.config import Settings
from app.data.ingestion import SUPPORTED_SUFFIXES, IngestionService
from app.data.repository import Repository
from app.data.storage import WorkspaceStorage


def ingest_fixture(tmp_path: Path, suffix: str) -> list[dict]:
    async def run() -> list[dict]:
        config = Settings(data_dir=tmp_path)
        repository = Repository(tmp_path / "metadata.sqlite3")
        storage = WorkspaceStorage(config)
        storage.ensure()
        await repository.initialize()
        workspace = await repository.create_workspace("Retail analysis")
        source_id = str(uuid4())
        source_dir = storage.source_dir(workspace["id"], source_id)
        source_dir.mkdir(parents=True)
        path = source_dir / f"orders{suffix}"
        frame = pd.DataFrame(
            {
                "region": ["East", "West", "East"],
                "sales_amount": [120.5, 80.0, None],
                "order_date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            }
        )
        if suffix == ".csv":
            frame.to_csv(path, index=False)
        elif suffix == ".tsv":
            frame.to_csv(path, index=False, sep="\t")
        elif suffix == ".parquet":
            frame.to_parquet(path, index=False)
        elif suffix == ".xls":
            workbook = xlwt.Workbook()
            orders = workbook.add_sheet("Orders")
            for column, name in enumerate(frame.columns):
                orders.write(0, column, name)
            for row, values in enumerate(frame.fillna("").itertuples(index=False), 1):
                for column, value in enumerate(values):
                    orders.write(row, column, value)
            regions = workbook.add_sheet("Regions")
            regions.write(0, 0, "region")
            for row, value in enumerate(frame["region"], 1):
                regions.write(row, 0, value)
            workbook.save(str(path))
        else:
            with pd.ExcelWriter(path) as writer:
                frame.to_excel(writer, sheet_name="Orders", index=False)
                frame[["region"]].to_excel(writer, sheet_name="Regions", index=False)
        await repository.add_source(
            workspace["id"], source_id, path.name, path.name, path.stat().st_size
        )
        await IngestionService(repository, storage).ingest(
            workspace["id"], source_id, path, path.name
        )
        return await repository.catalog(workspace["id"])

    return asyncio.run(run())


def test_csv_ingestion_profiles_columns(tmp_path: Path) -> None:
    catalog = ingest_fixture(tmp_path, ".csv")

    assert len(catalog) == 1
    assert catalog[0]["row_count"] == 3
    columns = {column["name"]: column for column in catalog[0]["columns"]}
    assert columns["sales_amount"]["null_count"] == 1
    assert columns["region"]["distinct_count"] == 2
    assert set(columns["region"]["sample_values"]) == {"East", "West"}


def test_parquet_ingestion(tmp_path: Path) -> None:
    catalog = ingest_fixture(tmp_path, ".parquet")

    assert catalog[0]["display_name"] == "orders"
    assert {column["name"] for column in catalog[0]["columns"]} >= {
        "region",
        "sales_amount",
    }


def test_tsv_ingestion(tmp_path: Path) -> None:
    catalog = ingest_fixture(tmp_path, ".tsv")

    assert catalog[0]["row_count"] == 3
    assert {column["name"] for column in catalog[0]["columns"]} >= {"region", "sales_amount"}


def test_excel_extensions_include_xls_and_xlsx() -> None:
    assert {".xls", ".xlsx"} <= SUPPORTED_SUFFIXES


def test_excel_ingestion_creates_one_table_per_nonempty_sheet(tmp_path: Path) -> None:
    catalog = ingest_fixture(tmp_path, ".xlsx")

    assert {table["display_name"] for table in catalog} == {
        "orders / Orders",
        "orders / Regions",
    }


def test_xls_ingestion_creates_one_table_per_nonempty_sheet(tmp_path: Path) -> None:
    catalog = ingest_fixture(tmp_path, ".xls")

    assert {table["display_name"] for table in catalog} == {
        "orders / Orders",
        "orders / Regions",
    }
