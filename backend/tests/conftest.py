import json
import os
from pathlib import Path

os.environ["FPPCC_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["FPPCC_RUN_COLLECTOR"] = "false"

import pytest  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def load(name):
    p = FIX / name
    return json.loads(p.read_text()) if p.suffix == ".json" else p.read_text()


@pytest.fixture
def fpp_status():
    return load("fpp_status_remote.json")


@pytest.fixture
def fpp_info():
    return load("fpp_info_remote.json")
