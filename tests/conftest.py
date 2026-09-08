import os
import sys
from pathlib import Path

TEST_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://postgres@localhost:5433/refund_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import threading
import time

import httpx
import pytest
import uvicorn

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

TEST_SERVER_URL = "http://127.0.0.1:8810"


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="session", autouse=True)
def _live_app_server():
    # Real HTTP server (not ASGITransport) so tests exercise the exact same
    # code path the concurrency test relies on, and so a real TCP request/
    # response round-trip is proven (Anti-Puppet: no in-process shortcuts).
    config = uvicorn.Config(app, host="127.0.0.1", port=8810, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 5
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn server did not start in time"

    yield

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _truncate_tables():
    yield
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "TRUNCATE TABLE ledger_entries, refunds, orders RESTART IDENTITY CASCADE"
        )


@pytest.fixture
def client():
    c = httpx.Client(base_url=TEST_SERVER_URL, timeout=10)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
