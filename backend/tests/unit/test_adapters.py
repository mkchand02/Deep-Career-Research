"""Source adapters against recorded/mock HTTP responses (respx). No live network."""

from __future__ import annotations

import json
from datetime import date

import httpx
import respx

from dcr.adapters.base import AdapterContext
from dcr.adapters.registry import build_adapter
from dcr.adapters.webutil import RobotsGate, blocked, json_ld_people, parse_joined_post, published_date
from dcr.models import SourceSpec, StudyQuery
from dcr.pipeline.discover import discover

ROBOTS = "User-agent: *\nDisallow: /private/\n"
PERSON_PAGE = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Person","name":"Avery Synthetic","jobTitle":"Software Engineer II",
 "worksFor":{"@type":"Organization","name":"Google"},"alumniOf":[{"@type":"CollegeOrUniversity","name":"MIT"}],
 "address":{"@type":"PostalAddress","addressLocality":"Mountain View, CA"},
 "sameAs":["https://github.com/averysynth"]}</script></head><body><p>Hi!</p></body></html>"""
JOINED_POST = """<html><head><meta property="article:published_time" content="2026-10-12T09:00:00Z"></head>
<body><nav>menu</nav><article><h1>New chapter</h1><p>I just joined Google as a Forward Deployed Engineer! Excited.</p>
</article></body></html>"""


def q(ref: date) -> StudyQuery:
    return StudyQuery(company="Google", reference_date=ref, sample_cap=50)


def test_blocklist_and_parsers() -> None:
    assert blocked("https://www.linkedin.com/in/someone") and blocked("https://lnkd.in/x")
    assert not blocked("https://janedoe.dev/about")
    j = parse_joined_post("Big news: I just joined Google as a Software Engineer II.")
    assert j == {"company": "Google", "title": "Software Engineer II", "kind": "joined"}
    assert parse_joined_post("Senior Data Engineer at Stripe, previously Amazon.")["company"] == "Stripe"  # type: ignore[index]
    assert parse_joined_post("I like hiking and coffee.") is None
    assert json_ld_people(PERSON_PAGE)[0]["jobTitle"] == "Software Engineer II"
    assert published_date(JOINED_POST) == date(2026, 10, 12)


@respx.mock
async def test_robots_gate(app_config, secrets) -> None:  # type: ignore[no-untyped-def]
    respx.get("https://a.example/robots.txt").respond(200, text=ROBOTS)
    respx.get("https://b.example/robots.txt").respond(404)
    respx.get("https://c.example/robots.txt").respond(503)
    gate = RobotsGate(AdapterContext("web_scraper", app_config, secrets), min_delay_s=0)
    assert (await gate.can_fetch("https://a.example/about"))[0]
    assert not (await gate.can_fetch("https://a.example/private/x"))[0]
    assert (await gate.can_fetch("https://b.example/anything"))[0]
    ok, reason = await gate.can_fetch("https://c.example/x")
    assert not ok and "unavailable" in reason
    ok, reason = await gate.can_fetch("https://www.linkedin.com/in/x")
    assert not ok and "never-fetch" in reason


@respx.mock
async def test_web_scraper_respects_robots_and_blocklist(app_config, secrets, ref_date) -> None:  # type: ignore[no-untyped-def]
    respx.get("https://avery.example/robots.txt").respond(200, text=ROBOTS)
    respx.get("https://avery.example/").respond(200, text=PERSON_PAGE, headers={"content-type": "text/html"})
    respx.get("https://blog.example/robots.txt").respond(200, text="User-agent: *\nAllow: /\n")
    respx.get("https://blog.example/posts/new-job").respond(
        200, text=JOINED_POST, headers={"content-type": "text/html"}
    )
    private = respx.get("https://avery.example/private/cv").respond(200, text=PERSON_PAGE)
    linkedin = respx.get(url__regex=r"https://(www\.)?linkedin\.com/.*").respond(200, text="nope")
    query = q(ref_date).model_copy(
        update={
            "sources": [
                SourceSpec(
                    name="web_scraper",
                    params={
                        "seeds": [
                            "https://avery.example/",
                            "https://blog.example/posts/new-job",
                            "https://avery.example/private/cv",
                            "https://www.linkedin.com/in/avery",
                        ],
                        "min_delay_s": 0,
                    },
                )
            ]
        }
    )
    res = await discover(query, app_config, secrets)
    assert not linkedin.called and not private.called
    by_ref = {p.source_ref: p for p in res.profiles}
    person = by_ref["https://avery.example/"]
    assert person.positions[0].company == "Google" and person.github_username == "averysynth"
    assert person.education[0].institution == "MIT"
    post = by_ref["https://blog.example/posts/new-job"]
    assert post.positions[0].title == "Forward Deployed Engineer"
    assert post.positions[0].start_date == date(2026, 10, 12)
    reasons = {s["reason"] for s in res.robots_skips}
    assert "disallowed by robots.txt" in reasons and any("never-fetch" in r for r in reasons)


GH_USER = {
    "login": "averysynth",
    "type": "User",
    "name": "Avery Synthetic",
    "company": "@google",
    "location": "Mountain View, CA",
    "bio": "Software Engineer @ Google | ML infra",
    "blog": "avery.example",
}
GH_REPOS = [
    {
        "name": "tiny-llm",
        "description": "Fine-tuning small language models",
        "language": "Python",
        "topics": ["llm", "pytorch"],
        "stargazers_count": 42,
        "fork": False,
        "html_url": "https://github.com/x/y",
    },
    {"name": "forked", "fork": True, "language": "Go", "stargazers_count": 1000},
]


@respx.mock
async def test_github_org_members_and_linked_enrichment(app_config, secrets, ref_date) -> None:  # type: ignore[no-untyped-def]
    respx.get("https://api.github.com/orgs/google/public_members").mock(
        side_effect=[httpx.Response(200, json=[{"login": "averysynth"}]), httpx.Response(200, json=[])]
    )
    respx.get("https://api.github.com/users/averysynth").respond(200, json=GH_USER)
    respx.get("https://api.github.com/users/averysynth/repos").respond(200, json=GH_REPOS)
    rec = {
        "full_name": "Avery Synthetic",
        "github_username": "averysynth",
        "positions": [{"title": "SWE II", "company": "Google", "start_date": "2026-10-01"}],
    }
    query = q(ref_date).model_copy(
        update={
            "sources": [
                SourceSpec(name="github", params={"orgs": "google"}),
                SourceSpec(name="manual", params={"records": [rec]}),
            ]
        }
    )
    res = await discover(query, app_config, secrets)
    gh = [p for p in res.profiles if p.source == "github"]
    assert len(gh) == 2 and res.linked_enrichments == 1
    main = next(p for p in gh if p.source_ref == "averysynth")
    assert main.positions[0].company == "google" and main.positions[0].title == "Software Engineer"
    assert [p.name for p in main.projects] == ["tiny-llm"] and main.projects[0].stars == 42
    enrich = next(p for p in gh if p.source_ref.startswith("enrich:"))
    assert enrich.positions == []


@respx.mock
async def test_github_rate_limit_is_partial_failure(app_config, secrets, ref_date) -> None:  # type: ignore[no-untyped-def]
    respx.get("https://api.github.com/users/someone").respond(429, headers={"Retry-After": "0"})
    query = q(ref_date).model_copy(
        update={
            "sources": [
                SourceSpec(name="github", params={"users": "someone"}),
                SourceSpec(name="synthetic", params={"n": 20}),
            ]
        }
    )
    res = await discover(query, app_config, secrets)
    assert any(e["source"] == "github" for e in res.errors)
    assert any(p.source.startswith("synthetic") for p in res.profiles)


@respx.mock
async def test_job_boards(app_config, secrets, ref_date) -> None:  # type: ignore[no-untyped-def]
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").respond(
        200,
        json={
            "jobs": [
                {
                    "title": "Software Engineer, Infrastructure",
                    "location": {"name": "New York, NY"},
                    "content": "&lt;p&gt;Kubernetes, Go and Terraform&lt;/p&gt;",
                    "updated_at": "2026-10-01T00:00:00Z",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                }
            ]
        },
    )
    respx.get("https://api.lever.co/v0/postings/acme").respond(
        200,
        json=[
            {
                "text": "Machine Learning Engineer",
                "categories": {"location": "Remote"},
                "createdAt": 1790000000000,
                "descriptionPlain": "PyTorch and LLMs",
                "lists": [{"content": "<li>Python</li>"}],
                "hostedUrl": "u",
            }
        ],
    )
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").respond(
        200,
        json={
            "jobs": [
                {
                    "title": "Data Engineer",
                    "location": "Seattle, WA",
                    "descriptionPlain": "Spark, Airflow, SQL",
                    "publishedAt": "2026-10-02T00:00:00Z",
                    "jobUrl": "u",
                }
            ]
        },
    )
    query = q(ref_date).model_copy(
        update={
            "company": "Acme",
            "sources": [
                SourceSpec(name="greenhouse", params={"board": "acme"}),
                SourceSpec(name="lever", params={"board": "acme"}),
                SourceSpec(name="ashby", params={"board": "acme"}),
            ],
        }
    )
    res = await discover(query, app_config, secrets)
    assert not res.errors, res.errors
    skills = {p.title: set(p.skills) for p in res.postings}
    assert {"Kubernetes", "Terraform"} <= skills["Software Engineer, Infrastructure"]
    assert {"PyTorch", "LLMs", "Python"} <= skills["Machine Learning Engineer"]
    assert {"Apache Spark", "Apache Airflow", "SQL"} <= skills["Data Engineer"]


async def test_keyed_adapters_unavailable_without_keys(app_config, secrets) -> None:  # type: ignore[no-untyped-def]
    for name in ("pdl", "brave", "x"):
        ok, reason = build_adapter(name, app_config, secrets).available()
        assert not ok and "requires" in reason


@respx.mock
async def test_pdl_brave_x_with_mock_keys(app_config, secrets, ref_date) -> None:  # type: ignore[no-untyped-def]
    keyed = secrets.model_copy(update={"pdl_api_key": "k", "brave_api_key": "k", "x_bearer_token": "k"})
    respx.post("https://api.peopledatalabs.com/v5/person/search").respond(
        200,
        json={
            "data": [
                {
                    "id": "pdl1",
                    "full_name": "Sam Synthetic",
                    "job_title": "software engineer",
                    "location_name": "seattle, washington",
                    "experience": [
                        {
                            "company": {"name": "google"},
                            "title": {"name": "software engineer"},
                            "start_date": "2026-09",
                            "is_primary": True,
                            "location_names": ["seattle, washington, united states"],
                        }
                    ],
                    "education": [
                        {
                            "school": {"name": "university of washington"},
                            "degrees": ["bachelors"],
                            "majors": ["computer science"],
                            "end_date": "2024",
                        }
                    ],
                    "skills": ["python"],
                }
            ]
        },
    )
    respx.get("https://api.search.brave.com/res/v1/web/search").respond(
        200,
        json={
            "web": {
                "results": [{"url": "https://blog.example/posts/new-job"}, {"url": "https://www.linkedin.com/in/x"}]
            }
        },
    )
    respx.get("https://blog.example/robots.txt").respond(404)
    respx.get("https://blog.example/posts/new-job").respond(
        200, text=JOINED_POST, headers={"content-type": "text/html"}
    )
    respx.get("https://api.x.com/2/tweets/search/recent").respond(
        200,
        json={
            "data": [
                {
                    "id": "1",
                    "text": "I just joined Google as a Data Engineer!",
                    "author_id": "u1",
                    "created_at": "2026-10-20T10:00:00Z",
                },
                {"id": "2", "text": "I just joined Stripe!", "author_id": "u1", "created_at": "2026-10-20T10:00:00Z"},
            ],
            "includes": {"users": [{"id": "u1", "name": "Pat Synthetic", "username": "patsyn", "location": "NYC"}]},
        },
    )
    query = q(ref_date).model_copy(
        update={
            "sources": [
                SourceSpec(name="pdl"),
                SourceSpec(name="brave", params={"min_delay_s": 0}),
                SourceSpec(name="x"),
            ]
        }
    )
    res = await discover(query, app_config, keyed)
    assert not res.errors, res.errors
    srcs = {p.source for p in res.profiles}
    assert srcs == {"pdl", "brave", "x"}
    pdl = next(p for p in res.profiles if p.source == "pdl")
    assert pdl.positions[0].start_date == date(2026, 9, 1) and pdl.positions[0].end_date is None
    xp = next(p for p in res.profiles if p.source == "x")
    assert xp.positions[0].title == "Data Engineer" and xp.positions[0].start_date == date(2026, 10, 20)
    assert len([p for p in res.profiles if p.source == "x"]) == 1  # Stripe post filtered out
    json.dumps([p.model_dump(mode="json") for p in res.profiles])
