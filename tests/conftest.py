from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import sessionmaker

from stockresearch.config import Settings
from stockresearch.container import Container
from stockresearch.db.models import Base
from stockresearch.db.session import make_engine
from stockresearch.services import repo
from tests import fakes


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite:///{tmp_path / 'test.db'}", data_dir=tmp_path, app_user="pavan",
        app_password=SecretStr("correct-horse-battery"), session_secret=SecretStr("x" * 40), cookie_secure=False,
        gemini_min_interval_seconds=0, min_turnover_cr=5, shortlist_per_group=10, app_url="http://testserver",
    )


@pytest.fixture()
def container(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[Container]:
    monkeypatch.setattr(repo, "today_ist", lambda: fakes.TODAY)
    monkeypatch.setattr(repo, "now_ist", lambda: fakes.NOW)
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    c = Container(settings=settings, sessions=sessionmaker(bind=engine, expire_on_commit=False),
                  exchange=fakes.FakeExchange(), market=fakes.FakeMarket(), news=fakes.FakeNews(),
                  researcher=fakes.FakeResearcher(), notifiers=[fakes.FakeNotifier()])
    yield c
    engine.dispose()
