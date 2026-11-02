from pathlib import Path

import pytest

from invoice_intake.pipeline import Pipeline, Settings

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(vendors_path=ROOT / "config" / "vendors.csv", data_dir=tmp_path, llm_base_url=None)


@pytest.fixture
def pipeline(settings: Settings) -> Pipeline:
    return Pipeline(settings)


def sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()
