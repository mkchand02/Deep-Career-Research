from __future__ import annotations

from datetime import date

import pytest

from dcr.models import DegreeLevel, Education, LevelBand, Position
from dcr.pipeline.normalize.companies import TargetCompany, default_company_normalizer
from dcr.pipeline.normalize.education import canonical_field, default_school_normalizer, degree_level
from dcr.pipeline.normalize.experience import experience_years, merged_years
from dcr.pipeline.normalize.geo import infer_country, region_matches
from dcr.pipeline.normalize.skills import default_skill_normalizer
from dcr.roles.titles import default_catalog


@pytest.mark.parametrize(
    ("raw", "canon", "ctype"),
    [
        ("Google LLC", "Google", "big_tech"),
        ("Google DeepMind", "Google", "big_tech"),
        ("YouTube", "Google", "big_tech"),
        ("Amazon Web Services (AWS)", "Amazon", "big_tech"),
        ("facebook", "Meta", "big_tech"),
        ("J.P. Morgan", "JPMorgan Chase", "finance"),
        ("Tata Consultancy Services Ltd.", "Tata Consultancy Services", "consulting"),
        ("Microsfot", "Microsoft", "big_tech"),  # typo, fuzzy
    ],
)
def test_company_canonicalization(raw: str, canon: str, ctype: str) -> None:
    m = default_company_normalizer().match(raw)
    assert (m.canonical, m.type) == (canon, ctype)


def test_unknown_company_passthrough_and_target_aliases() -> None:
    cn = default_company_normalizer()
    m = cn.match("Nimbus Labs")
    assert m.canonical == "Nimbus Labs" and m.type == "other" and not m.known
    t = TargetCompany("Acme Robotics", ["Acme Robotics Inc", "AcmeAI"], cn)
    assert t.matches("AcmeAI")[0] and t.matches("Acme Robotics, Inc.")[0]
    assert not t.matches("Google")[0]


@pytest.mark.parametrize(
    ("raw", "canon"),
    [
        ("k8s", "Kubernetes"),
        ("golang", "Go"),
        ("GCP", "Google Cloud"),
        ("pyspark", "Apache Spark"),
        ("sklearn", "scikit-learn"),
        ("Generative AI", "LLMs"),
        ("React.js", "React"),
        ("c plus plus", "C++"),
        ("PostgresQL", "PostgreSQL"),
        ("Kubernets", "Kubernetes"),
    ],
)
def test_skill_canonicalization(raw: str, canon: str) -> None:
    assert default_skill_normalizer().canonical(raw)[0] == canon


def test_skill_dedupe_and_text_detection() -> None:
    sn = default_skill_normalizer()
    assert sn.normalize(["k8s", "Kubernetes", "kube", "Python", "python3"]) == ["Kubernetes", "Python"]
    found = sn.find_in_text("Built RAG pipelines on GKE with PyTorch; I like to go hiking.")
    assert {"RAG", "Kubernetes", "PyTorch"} <= set(found)
    assert "Go" not in found


@pytest.mark.parametrize(
    ("raw", "canon", "country"),
    [
        ("MIT", "Massachusetts Institute of Technology", "US"),
        ("UC Berkeley", "University of California, Berkeley", "US"),
        ("IIT Bombay", "Indian Institute of Technology Bombay", "IN"),
        ("Carnegie Mellon", "Carnegie Mellon University", "US"),
        ("University of Waterloo", "University of Waterloo", "CA"),
    ],
)
def test_school_canonicalization(raw: str, canon: str, country: str) -> None:
    c, k, known = default_school_normalizer().match(raw)
    assert (c, k, known) == (canon, country, True)


@pytest.mark.parametrize(
    ("raw", "lvl"),
    [
        ("B.S.", DegreeLevel.BACHELOR),
        ("Bachelor of Technology", DegreeLevel.BACHELOR),
        ("B.Tech", DegreeLevel.BACHELOR),
        ("M.S.", DegreeLevel.MASTER),
        ("MEng", DegreeLevel.MASTER),
        ("Master's degree", DegreeLevel.MASTER),
        ("Ph.D.", DegreeLevel.PHD),
        ("MBA", DegreeLevel.MBA),
        ("Certificate", DegreeLevel.OTHER),
    ],
)
def test_degree_levels(raw: str, lvl: DegreeLevel) -> None:
    assert degree_level(raw) == lvl


def test_field_canonical() -> None:
    assert canonical_field("CS") == "Computer Science"
    assert canonical_field("computer science") == "Computer Science"


@pytest.mark.parametrize(
    ("loc", "iso"),
    [
        ("Mountain View, CA", "US"),
        ("New York, NY, United States", "US"),
        ("Bengaluru, India", "IN"),
        ("London, United Kingdom", "GB"),
        ("Toronto, Ontario, Canada", "CA"),
        ("Seattle", "US"),
        ("Somewhere", None),
        ("Zurich, Switzerland", "CH"),
    ],
)
def test_infer_country(loc: str, iso: str | None) -> None:
    assert infer_country(loc) == iso


def test_region_matching() -> None:
    assert region_matches("Mountain View, CA, United States", ["CA"])
    assert region_matches("Mountain View, California", ["CA"])
    assert region_matches("Sunnyvale, CA", ["California"])
    assert not region_matches("Austin, TX", ["CA", "WA"])
    assert region_matches(None, [])


@pytest.mark.parametrize(
    ("title", "company", "role", "level", "intern"),
    [
        ("Software Engineer II", "Google", "software_engineer", LevelBand.ENTRY, False),  # Google SWE II = L3
        ("Software Engineer III", "Google", "software_engineer", LevelBand.MID, False),
        ("Software Engineer, L4", "Google", "software_engineer", LevelBand.MID, False),
        ("SDE II", "Amazon", "software_engineer", LevelBand.MID, False),
        ("Software Development Engineer I", "Amazon", "software_engineer", LevelBand.ENTRY, False),
        ("Senior Software Engineer", "Google", "software_engineer", LevelBand.SENIOR, False),
        ("Software Engineer, Machine Learning", "Google", "ai_engineer", LevelBand.UNKNOWN, False),
        ("Machine Learning Engineer", None, "ai_engineer", LevelBand.UNKNOWN, False),
        ("Forward Deployed Software Engineer", None, "forward_deployed_engineer", LevelBand.UNKNOWN, False),
        ("Cloud Infrastructure Engineer", None, "cloud_engineer", LevelBand.UNKNOWN, False),
        ("Data Engineer", None, "data_engineer", LevelBand.UNKNOWN, False),
        ("Software Engineering Intern", None, "software_engineer", LevelBand.UNKNOWN, True),
        ("Staff Software Engineer", None, "software_engineer", LevelBand.STAFF, False),
        ("Software Engineer, E4", "Meta", "software_engineer", LevelBand.MID, False),
        ("Associate Product Manager", None, "product_manager", LevelBand.ENTRY, False),
        ("Site Reliability Engineer", None, "sre", LevelBand.UNKNOWN, False),
        ("Chief Happiness Officer", None, None, LevelBand.UNKNOWN, False),
    ],
)
def test_title_parsing(title: str, company: str | None, role: str | None, level: LevelBand, intern: bool) -> None:
    p = default_catalog().parse(title, company)
    assert (p.canonical_role, p.level, p.is_internship) == (role, level, intern)


def test_experience_merges_overlaps_and_excludes_internships() -> None:
    ref = date(2026, 1, 1)
    pos = [
        Position(title="SWE", company="A", start_date=date(2022, 1, 1), end_date=date(2024, 1, 1)),
        Position(title="SWE", company="B", start_date=date(2023, 1, 1), end_date=date(2025, 1, 1)),  # overlap
        Position(
            title="Intern", company="C", start_date=date(2021, 6, 1), end_date=date(2021, 9, 1), is_internship=True
        ),
    ]
    assert experience_years(pos, ref) == pytest.approx(3.0, abs=0.01)
    assert experience_years(pos, ref, exclude_internships=False) == pytest.approx(3.25, abs=0.01)
    assert experience_years([Position(title="x", company="y")], ref) is None
    assert merged_years([]) == 0.0


def test_school_normalizer_education_list() -> None:
    edu = default_school_normalizer().normalize([Education(institution="IIT Delhi", degree="B.Tech", field="CSE")])
    assert edu[0].institution == "Indian Institute of Technology Delhi"
    assert edu[0].country == "IN" and edu[0].degree_level == DegreeLevel.BACHELOR
    assert edu[0].field == "Computer Science"
