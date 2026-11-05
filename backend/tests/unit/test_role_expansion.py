from __future__ import annotations

from dcr.models import LevelBand
from dcr.roles.expansion import expand_roles, role_matches


def test_user_example_expansion() -> None:
    specs, unrec = expand_roles(
        ["SDE I", "SDE II", "SDE III", "Cloud Engineer", "AI Engineer", "Forward Deployed Engineer", "Data Engineer"],
        company="Amazon",
        include_similar=False,
    )
    by = {s.canonical_role: s for s in specs}
    assert not unrec
    assert set(by) == {
        "software_engineer",
        "cloud_engineer",
        "ai_engineer",
        "forward_deployed_engineer",
        "data_engineer",
    }
    assert by["software_engineer"].levels == [LevelBand.ENTRY, LevelBand.MID, LevelBand.SENIOR]
    assert by["software_engineer"].requested_as == ["SDE I", "SDE II", "SDE III"]
    assert by["cloud_engineer"].levels == []  # any level
    assert "software engineer" in by["software_engineer"].titles


def test_company_ladder_changes_level_mapping() -> None:
    google, _ = expand_roles(["Software Engineer III"], company="Google", include_similar=False)
    amazon, _ = expand_roles(["Software Engineer III"], company="Amazon", include_similar=False)
    assert google[0].levels == [LevelBand.MID]
    assert amazon[0].levels == [LevelBand.SENIOR]


def test_similar_roles_and_unrecognized() -> None:
    specs, unrec = expand_roles(["Data Engineer", "Wizard of Light Bulb Moments"], include_similar=True)
    roles = {s.canonical_role: s.origin for s in specs}
    assert roles["data_engineer"] == "user"
    assert roles.get("data_scientist") == "similar"
    assert unrec == ["Wizard of Light Bulb Moments"]


def test_any_level_wins_over_specific() -> None:
    specs, _ = expand_roles(["SDE II", "Software Engineer"], include_similar=False)
    assert specs[0].levels == []


def test_role_matches() -> None:
    specs, _ = expand_roles(["SDE II"], company="Amazon", include_similar=False)
    assert role_matches(specs, "software_engineer", LevelBand.MID)
    assert not role_matches(specs, "software_engineer", LevelBand.SENIOR)
    assert not role_matches(specs, "data_engineer", LevelBand.MID)


def test_llm_suggester_adds_titles_best_effort() -> None:
    def suggest(roles: list[str], co: str) -> dict[str, list[str]]:
        return {"forward_deployed_engineer": ["Solutions Engineer – Deployed"]}

    specs, _ = expand_roles(["Forward Deployed Engineer"], include_similar=False, suggester=suggest)
    assert "Solutions Engineer – Deployed" in specs[0].titles

    def boom(roles, co):  # type: ignore[no-untyped-def]
        raise RuntimeError("llm down")

    specs2, _ = expand_roles(["Data Engineer"], include_similar=False, suggester=boom)
    assert specs2[0].canonical_role == "data_engineer"
