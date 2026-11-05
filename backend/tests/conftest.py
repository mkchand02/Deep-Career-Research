"""Shared fixtures. Tests only ever use synthetic data."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import sessionmaker

from dcr.config import AppConfig, Secrets
from dcr.db.models import Base
from dcr.db.session import make_engine
from dcr.synth.generator import SyntheticDataset, SyntheticGenerator

REF = date(2026, 11, 5)


@pytest.fixture
def ref_date() -> date:
    return REF


@pytest.fixture
def app_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "database_url": "sqlite:///:memory:",
            "enrichment": {
                "enabled": True,
                "pricing": {"claude-opus-5-5": {"input_per_mtok": 5, "output_per_mtok": 25}},
            },
            "sources": {
                "github": {"rate_limit_per_minute": 6000, "max_retries": 2},
                "web_scraper": {"rate_limit_per_minute": 6000, "max_retries": 2},
                "greenhouse": {"rate_limit_per_minute": 6000},
                "lever": {"rate_limit_per_minute": 6000},
                "ashby": {"rate_limit_per_minute": 6000},
                "pdl": {"enabled": True, "rate_limit_per_minute": 6000},
                "brave": {"enabled": True, "rate_limit_per_minute": 6000},
                "x": {"enabled": True, "rate_limit_per_minute": 6000},
            },
        }
    )


@pytest.fixture
def secrets() -> Secrets:
    return Secrets.model_construct(
        database_url="",
        anthropic_api_key=None,
        github_token=None,
        pdl_api_key=None,
        brave_api_key=None,
        x_bearer_token=None,
        dcr_config=None,
    )


@pytest.fixture
def session_factory(tmp_path):  # type: ignore[no-untyped-def]
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture(scope="session")
def synth_ds() -> SyntheticDataset:
    return SyntheticGenerator(seed=7, reference_date=REF).generate(800)
