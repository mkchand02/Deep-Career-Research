"""Synthetic fixture generator.

Produces realistic *fake* professional profiles (Faker names, plausible careers, education,
skills with messy aliases, projects) plus cross-source duplicates with noise, and fake job
postings. Development and tests never need real personal data.

Every record is flagged ``synthetic: true``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from faker import Faker

TARGETS = ["Google"]

# (canonical role, weight, title variants without level, skill pool)
ROLE_TEMPLATES: list[tuple[str, float, list[str], list[str]]] = [
    (
        "software_engineer",
        0.40,
        [
            "Software Engineer",
            "Software Development Engineer",
            "SWE",
            "Backend Engineer",
            "Software Developer",
            "Full Stack Engineer",
        ],
        [
            "Python",
            "Java",
            "golang",
            "C++",
            "k8s",
            "Docker",
            "Distributed Systems",
            "Microservices",
            "SQL",
            "gRPC",
            "REST APIs",
            "Spanner",
            "System Design",
            "DSA",
            "Git",
            "Linux",
            "TypeScript",
            "React",
        ],
    ),
    (
        "cloud_engineer",
        0.12,
        ["Cloud Engineer", "Cloud Infrastructure Engineer", "Platform Engineer", "DevOps Engineer"],
        [
            "Kubernetes",
            "Terraform",
            "GCP",
            "AWS",
            "Docker",
            "Linux",
            "Bash",
            "CI/CD",
            "Prometheus",
            "Go",
            "Python",
            "Networking",
            "Helm",
            "Istio",
            "Ansible",
            "Observability",
        ],
    ),
    (
        "ai_engineer",
        0.16,
        [
            "Machine Learning Engineer",
            "AI Engineer",
            "ML Engineer",
            "Software Engineer, Machine Learning",
            "Generative AI Engineer",
        ],
        [
            "Python",
            "PyTorch",
            "TensorFlow",
            "JAX",
            "LLMs",
            "RAG",
            "Hugging Face",
            "MLOps",
            "Deep Learning",
            "NLP",
            "Computer Vision",
            "Vector Databases",
            "LangChain",
            "Prompt Engineering",
            "sklearn",
            "SQL",
            "Kubernetes",
            "Reinforcement Learning",
        ],
    ),
    (
        "forward_deployed_engineer",
        0.06,
        ["Forward Deployed Engineer", "Forward Deployed Software Engineer", "Deployment Strategist"],
        [
            "Python",
            "TypeScript",
            "SQL",
            "Customer-facing Engineering",
            "Google Cloud",
            "LLMs",
            "REST APIs",
            "Data Pipelines",
            "React",
            "Technical Communication",
            "Docker",
            "Kubernetes",
        ],
    ),
    (
        "data_engineer",
        0.10,
        ["Data Engineer", "Big Data Engineer", "Analytics Engineer", "Data Platform Engineer"],
        [
            "Python",
            "SQL",
            "Spark",
            "Airflow",
            "Kafka",
            "BigQuery",
            "dbt",
            "Apache Beam",
            "ETL",
            "Scala",
            "Data Warehousing",
            "Hadoop",
            "Pandas",
            "Data Visualization",
        ],
    ),
    (
        "sre",
        0.05,
        ["Site Reliability Engineer", "SRE", "Production Engineer"],
        [
            "Linux",
            "Go",
            "Python",
            "Kubernetes",
            "Prometheus",
            "Observability",
            "Networking",
            "Bash",
            "Distributed Systems",
            "Terraform",
        ],
    ),
    (
        "cloud_solutions_engineer",
        0.05,
        ["Customer Engineer", "Cloud Technical Solutions Engineer", "Solutions Architect"],
        [
            "Google Cloud",
            "AWS",
            "Kubernetes",
            "Customer-facing Engineering",
            "Networking",
            "Python",
            "SQL",
            "Technical Communication",
            "Security",
        ],
    ),
    (
        "data_scientist",
        0.04,
        ["Data Scientist", "Product Data Scientist"],
        ["Python", "R", "SQL", "Statistics", "scikit-learn", "Pandas", "Machine Learning", "Tableau", "A/B Testing"],
    ),
    (
        "product_manager",
        0.02,
        ["Product Manager", "Technical Program Manager", "Associate Product Manager"],
        ["Agile", "SQL", "Technical Communication", "Data Visualization"],
    ),
]

# Level tokens per (company, band) used to build realistic titles.
LEVEL_TITLES: dict[str, dict[str, list[str]]] = {
    "Google": {
        "entry": ["{t} II", "{t} II", "{t}, L3", "{t}"],
        "mid": ["{t} III", "{t}, L4"],
        "senior": ["Senior {t}", "{t}, L5"],
    },
    "Amazon": {"entry": ["{t} I", "{t}"], "mid": ["{t} II"], "senior": ["{t} III", "Senior {t}"]},
    "Microsoft": {"entry": ["{t}", "{t} 59"], "mid": ["{t} II"], "senior": ["Senior {t}"]},
    "Meta": {"entry": ["{t}, E3", "{t}"], "mid": ["{t}, E4"], "senior": ["{t}, E5"]},
    "*": {
        "entry": ["{t}", "Junior {t}", "{t} I", "Associate {t}"],
        "mid": ["{t} II", "{t}"],
        "senior": ["Senior {t}", "{t} III"],
    },
}

PRIOR_COMPANIES: list[tuple[str, str]] = [
    ("Amazon", "big_tech"),
    ("Microsoft", "big_tech"),
    ("Meta", "big_tech"),
    ("Apple", "big_tech"),
    ("Salesforce", "enterprise"),
    ("Oracle", "enterprise"),
    ("IBM", "enterprise"),
    ("Adobe", "enterprise"),
    ("Cisco", "enterprise"),
    ("Intel", "enterprise"),
    ("Stripe", "startup"),
    ("Databricks", "startup"),
    ("Snowflake", "enterprise"),
    ("Uber", "big_tech"),
    ("Airbnb", "big_tech"),
    ("Figma", "startup"),
    ("Rippling", "startup"),
    ("Plaid", "startup"),
    ("Goldman Sachs", "finance"),
    ("JPMorgan Chase", "finance"),
    ("Capital One", "finance"),
    ("Bloomberg", "finance"),
    ("Accenture", "consulting"),
    ("Deloitte", "consulting"),
    ("Infosys", "consulting"),
    ("Tata Consultancy Services", "consulting"),
    ("Wipro", "consulting"),
    ("Flipkart", "big_tech"),
    ("Samsung", "enterprise"),
    ("Nimbus Labs", "startup"),
    ("Quantiva AI", "startup"),
    ("Brightpath Health", "startup"),
    ("Orbitly", "startup"),
    ("Carnegie Mellon University", "academia"),
]

# Company name as it might appear on a profile (noise for normalization).
COMPANY_VARIANTS = {
    "Google": ["Google", "Google", "Google LLC", "Google Cloud", "Google DeepMind", "YouTube", "Alphabet"],
    "Amazon": ["Amazon", "Amazon Web Services (AWS)", "AWS", "Amazon.com"],
    "Microsoft": ["Microsoft", "Microsoft Corporation", "Microsoft Azure"],
    "Meta": ["Meta", "Facebook", "Meta Platforms"],
}

US_SCHOOLS = [
    "Stanford University",
    "MIT",
    "Carnegie Mellon University",
    "UC Berkeley",
    "Georgia Tech",
    "UIUC",
    "University of Michigan",
    "University of Washington",
    "UT Austin",
    "Cornell University",
    "Purdue University",
    "UCLA",
    "UCSD",
    "University of Southern California",
    "Columbia University",
    "NYU",
    "Northeastern University",
    "Arizona State University",
    "San Jose State University",
    "Texas A&M",
    "University of Maryland",
    "Virginia Tech",
    "Rutgers University",
    "University of Wisconsin-Madison",
    "Penn State",
    "UMass Amherst",
    "Northwestern University",
    "Princeton University",
    "Harvard University",
    "Duke University",
    "Stony Brook University",
]
FOREIGN_SCHOOLS = [
    ("IIT Bombay", "IN"),
    ("IIT Delhi", "IN"),
    ("IIT Madras", "IN"),
    ("BITS Pilani", "IN"),
    ("VIT", "IN"),
    ("NIT Trichy", "IN"),
    ("IIIT Hyderabad", "IN"),
    ("Anna University", "IN"),
    ("Delhi Technological University", "IN"),
    ("Tsinghua University", "CN"),
    ("Peking University", "CN"),
    ("Zhejiang University", "CN"),
    ("SJTU", "CN"),
    ("University of Toronto", "CA"),
    ("University of Waterloo", "CA"),
    ("UBC", "CA"),
    ("University of Oxford", "GB"),
    ("Imperial College London", "GB"),
    ("ETH Zurich", "CH"),
    ("TUM", "DE"),
    ("NUS", "SG"),
    ("KAIST", "KR"),
    ("Technion", "IL"),
    ("University of Sao Paulo", "BR"),
    ("University of Lagos", "NG"),
    ("LUMS", "PK"),
    ("BUET", "BD"),
    ("National Taiwan University", "TW"),
]
FIELDS = [
    "Computer Science",
    "Computer Science",
    "Computer Science",
    "Computer Engineering",
    "Electrical Engineering",
    "Data Science",
    "Mathematics",
    "Statistics",
    "Information Systems",
    "Software Engineering",
    "Artificial Intelligence",
    "Physics",
    "Mechanical Engineering",
]
US_CITIES = [
    ("Mountain View, CA", 0.22),
    ("Sunnyvale, CA", 0.10),
    ("San Francisco, CA", 0.10),
    ("New York, NY", 0.16),
    ("Seattle, WA", 0.08),
    ("Kirkland, WA", 0.07),
    ("Austin, TX", 0.06),
    ("Chicago, IL", 0.04),
    ("Boston, MA", 0.05),
    ("Cambridge, MA", 0.03),
    ("Atlanta, GA", 0.03),
    ("Pittsburgh, PA", 0.02),
    ("Los Angeles, CA", 0.02),
    ("Boulder, CO", 0.02),
]
OTHER_LOCATIONS = [
    ("Bengaluru, India", "IN"),
    ("Hyderabad, India", "IN"),
    ("London, United Kingdom", "GB"),
    ("Toronto, Canada", "CA"),
    ("Zurich, Switzerland", "CH"),
    ("Dublin, Ireland", "IE"),
    ("Warsaw, Poland", "PL"),
]
PROJECT_TEMPLATES = [
    ("Distributed key-value store", ["infra", "distributed-systems"], ["Go", "gRPC", "Raft"]),
    ("Kubernetes autoscaler experiments", ["infra", "cloud"], ["Go", "Kubernetes", "Prometheus"]),
    ("LLM-powered document Q&A", ["ml", "llm"], ["Python", "LangChain", "Vector Databases", "FastAPI"]),
    ("Fine-tuning small language models", ["ml", "llm"], ["Python", "PyTorch", "Hugging Face"]),
    ("Image segmentation for medical scans", ["ml", "computer-vision"], ["Python", "PyTorch", "OpenCV"]),
    ("Real-time chat app", ["web"], ["TypeScript", "React", "Node.js", "Redis"]),
    ("Personal portfolio site", ["web"], ["Next.js", "Tailwind"]),
    ("Streaming ETL pipeline", ["data"], ["Python", "Kafka", "Spark", "Airflow"]),
    ("Data warehouse cost dashboard", ["data", "analytics"], ["SQL", "dbt", "BigQuery", "Looker"]),
    ("Compiler for a toy language", ["systems", "compilers"], ["Rust", "LLVM"]),
    ("Mobile habit tracker", ["mobile"], ["Kotlin", "Android"]),
    ("Terraform module library", ["infra", "cloud"], ["Terraform", "GCP", "AWS"]),
    ("Reinforcement learning game agent", ["ml", "rl"], ["Python", "JAX"]),
    ("Open-source contribution to PyTorch", ["ml", "open-source"], ["Python", "C++", "PyTorch"]),
    ("Customer data migration toolkit", ["data", "customer"], ["Python", "SQL", "Google Cloud"]),
    ("Fraud detection model", ["ml", "fintech"], ["Python", "scikit-learn", "XGBoost"]),
    ("Raytracer", ["graphics", "systems"], ["C++"]),
    ("Campus course-planner", ["web"], ["Python", "Django", "PostgreSQL"]),
]
PRONOUNS = {"woman": ["she/her", "she/they"], "man": ["he/him", "he/they"], "nonbinary": ["they/them"]}


@dataclass
class SyntheticDataset:
    """Generated records plus ground truth (truth is for tests only, never exported)."""

    records: list[dict[str, Any]]
    truth_person_of_record: list[int]
    truth: list[dict[str, Any]] = field(default_factory=list)
    job_postings: list[dict[str, Any]] = field(default_factory=list)


def _weighted(rng: random.Random, items: list[tuple[Any, float]]) -> Any:
    vals, weights = zip(*items, strict=True)
    return rng.choices(vals, weights=weights, k=1)[0]


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


class SyntheticGenerator:
    """Seeded generator of fake people and job postings."""

    def __init__(
        self,
        seed: int = 42,
        reference_date: date | None = None,
        target_company: str = "Google",
        target_share: float = 0.55,
        us_share: float = 0.85,
        duplicate_rate: float = 0.25,
    ) -> None:
        self.rng = random.Random(seed)
        self.fakers = {loc: Faker(loc) for loc in ("en_US", "en_IN", "en_GB", "es_MX", "de_DE", "fr_FR", "pt_BR")}
        for i, f in enumerate(self.fakers.values()):
            f.seed_instance(seed + i)
        self.ref = reference_date or date.today()
        self.target = target_company
        self.target_share = target_share
        self.us_share = us_share
        self.duplicate_rate = duplicate_rate

    # ------------------------------------------------------------------ people
    def _person(self, pid: int) -> dict[str, Any]:
        rng = self.rng
        gender = _weighted(rng, [("woman", 0.27), ("man", 0.70), ("nonbinary", 0.03)])
        locale = _weighted(
            rng,
            [
                ("en_US", 0.55),
                ("en_IN", 0.25),
                ("en_GB", 0.05),
                ("es_MX", 0.06),
                ("de_DE", 0.03),
                ("fr_FR", 0.03),
                ("pt_BR", 0.03),
            ],
        )
        fk = self.fakers[locale]
        first = (
            fk.first_name_female()
            if gender == "woman"
            else (fk.first_name_male() if gender == "man" else fk.first_name())
        )
        last = fk.last_name()
        at_target = rng.random() < self.target_share
        company = (
            self.target
            if at_target
            else rng.choice(["Amazon", "Microsoft", "Meta", "Apple", "Stripe", "Databricks", "Salesforce"])
        )
        in_us = rng.random() < self.us_share
        if in_us:
            location = _weighted(rng, US_CITIES) + ", United States"
            country = "US"
        else:
            location, country = rng.choice(OTHER_LOCATIONS)

        role_tpl = _weighted(rng, [(t, t[1]) for t in ROLE_TEMPLATES])
        role, _, title_variants, skill_pool = role_tpl
        exp_years = max(0.0, rng.gammavariate(1.6, 1.8) - 0.3)
        band = "entry" if exp_years < 2 else ("mid" if exp_years < 5 else "senior")
        title_base = rng.choice(title_variants)
        ladder = LEVEL_TITLES.get(company, LEVEL_TITLES["*"])
        title = rng.choice(ladder[band]).format(t=title_base)

        # Recent joiners are concentrated in the last 120 days; others spread over 3 years.
        if rng.random() < 0.6:
            days_ago = rng.randint(0, 120)
        else:
            days_ago = rng.randint(121, 1100)
        start = self.ref - timedelta(days=days_ago)
        current_company_text = rng.choice(COMPANY_VARIANTS.get(company, [company]))

        # Education
        intl = (not in_us) or rng.random() < (0.45 if locale in ("en_IN",) else 0.15)
        grad_year = start.year - int(exp_years) - (0 if rng.random() < 0.7 else 1)
        education: list[dict[str, Any]] = []
        if intl and in_us:
            bs_school, bs_country = rng.choice(FOREIGN_SCHOOLS)
        elif intl:
            bs_school, bs_country = next(
                ((s, c) for s, c in FOREIGN_SCHOOLS if c == country), rng.choice(FOREIGN_SCHOOLS)
            )
        else:
            bs_school, bs_country = rng.choice(US_SCHOOLS), "US"
        has_ms = rng.random() < (0.65 if intl and in_us else 0.30)
        has_phd = role in ("ai_engineer", "applied_scientist") and rng.random() < 0.18
        bs_end = grad_year - (2 if has_ms else 0) - (4 if has_phd else 0)
        field_ = rng.choice(FIELDS)
        education.append(
            {
                "institution": bs_school,
                "degree": rng.choice(["B.S.", "BS", "Bachelor of Technology", "B.Tech", "Bachelor's degree", "BSc"])
                if bs_country != "US"
                else rng.choice(["B.S.", "BS", "Bachelor of Science", "BA"]),
                "field": field_,
                "start_year": bs_end - 4,
                "end_year": bs_end,
            }
        )
        if has_ms:
            ms_school = rng.choice(US_SCHOOLS) if in_us and rng.random() < 0.85 else rng.choice(FOREIGN_SCHOOLS)[0]
            education.append(
                {
                    "institution": ms_school,
                    "degree": rng.choice(["M.S.", "MS", "Master of Science", "MEng", "Master's degree"]),
                    "field": rng.choice(
                        [
                            "Computer Science",
                            "Computer Science",
                            "Data Science",
                            "Electrical and Computer Engineering",
                            "Machine Learning",
                        ]
                    ),
                    "start_year": bs_end + 1 if not has_phd else bs_end,
                    "end_year": bs_end + 2,
                }
            )
        if has_phd:
            education.append(
                {
                    "institution": rng.choice(US_SCHOOLS),
                    "degree": rng.choice(["PhD", "Ph.D."]),
                    "field": rng.choice(["Computer Science", "Machine Learning", "Statistics"]),
                    "start_year": grad_year - 5,
                    "end_year": grad_year,
                }
            )
        rng.shuffle(education)

        # Positions: internships during school, prior FT jobs, current job.
        positions: list[dict[str, Any]] = []
        interned_at_target = False
        for _ in range(rng.choices([0, 1, 2, 3], weights=[0.3, 0.35, 0.25, 0.1])[0]):
            yr = rng.randint(bs_end - 2, grad_year)
            if at_target and not interned_at_target and rng.random() < 0.25:
                ic = self.target
                interned_at_target = True
            else:
                ic = rng.choice(PRIOR_COMPANIES)[0]
            positions.append(
                {
                    "title": rng.choice(
                        [
                            "Software Engineering Intern",
                            "SWE Intern",
                            "Intern",
                            "Data Science Intern",
                            "Research Intern",
                            "Software Engineer Intern",
                        ]
                    ),
                    "company": ic,
                    "start_date": _iso(date(yr, 6, 1)),
                    "end_date": _iso(date(yr, 8, 25)),
                    "location": location if rng.random() < 0.6 else None,
                }
            )
        cursor = start
        remaining = exp_years
        while remaining > 0.4:
            dur = min(remaining, max(0.5, rng.uniform(0.8, 3.5)))
            p_end = cursor - timedelta(days=rng.randint(7, 45))
            p_start = p_end - timedelta(days=int(dur * 365))
            pc, _ = rng.choice(PRIOR_COMPANIES)
            abroad = (not in_us) or (intl and rng.random() < 0.35)
            positions.append(
                {
                    "title": rng.choice(title_variants) if rng.random() < 0.7 else "Software Engineer",
                    "company": pc,
                    "start_date": _iso(p_start),
                    "end_date": _iso(p_end),
                    "location": (
                        rng.choice(OTHER_LOCATIONS)[0] if abroad else _weighted(rng, US_CITIES) + ", United States"
                    ),
                }
            )
            cursor = p_start
            remaining -= dur
        positions.append(
            {
                "title": title,
                "company": current_company_text,
                "start_date": _iso(start),
                "end_date": None,
                "location": location,
                "description": rng.choice(
                    [
                        "",
                        f"Working on {rng.choice(skill_pool)} at scale.",
                        "Building internal developer platforms.",
                        "Helping customers adopt generative AI.",
                    ]
                ),
            }
        )

        skills = rng.sample(skill_pool, k=min(len(skill_pool), rng.randint(5, 11)))
        has_github = rng.random() < (0.55 if role in ("ai_engineer", "software_engineer") else 0.35)
        handle = (first + last).lower().replace(" ", "")[:14] + str(rng.randint(1, 99)) if has_github else None
        projects = []
        for _ in range(rng.choices([0, 1, 2, 3, 4], weights=[0.2, 0.3, 0.25, 0.15, 0.1])[0]):
            name, tags, stack = rng.choice(PROJECT_TEMPLATES)
            projects.append(
                {
                    "name": name,
                    "description": f"{name} built with {', '.join(stack)}.",
                    "tags": tags,
                    "tech_stack": stack,
                    "kind": rng.choice(["personal", "academic", "professional", "personal"]),
                    "stars": int(rng.paretovariate(1.3)) - 1 if has_github else None,
                    "commits": rng.randint(5, 400) if has_github else None,
                    "url": f"https://github.com/{handle}/{name.lower().replace(' ', '-')}" if handle else None,
                }
            )
        links = []
        if handle:
            links.append(f"https://github.com/{handle}")
        if rng.random() < 0.3:
            links.append(f"https://{first.lower()}{last.lower()}.dev".replace(" ", ""))
        pronouns = rng.choice(PRONOUNS[gender]) if rng.random() < 0.18 else None

        return {
            "pid": pid,
            "first": first,
            "last": last,
            "gender": gender,
            "record": {
                "full_name": f"{first} {last}",
                "headline": f"{title} at {current_company_text}",
                "location": location,
                "country": country,
                "pronouns": pronouns,
                "links": links,
                "github_username": handle,
                "positions": positions,
                "education": education,
                "skills": skills,
                "projects": projects,
                "summary": rng.choice(
                    [
                        "",
                        f"Passionate about {skills[0]} and {skills[-1]}.",
                        f"Recently joined {current_company_text}!",
                        "Lifelong learner.",
                    ]
                ),
                "synthetic": True,
            },
            "truth": {
                "company": company,
                "country": country,
                "role": role,
                "band": band,
                "exp_years": round(exp_years, 2),
                "start_date": start.isoformat(),
                "gender": gender,
                "intl_degree": intl,
            },
        }

    def _duplicate(self, base: dict[str, Any]) -> dict[str, Any]:
        """A second, noisier view of the same person from another (simulated) source."""
        rng = self.rng
        rec = {k: (list(v) if isinstance(v, list) else v) for k, v in base["record"].items()}
        first, last = base["first"], base["last"]
        variant = rng.choice(["case", "initial", "nick", "same"])
        if variant == "case":
            rec["full_name"] = f"{first.upper()} {last}"
        elif variant == "initial":
            rec["full_name"] = f"{first} {rng.choice('ABCDEFGHJKLMNPRST')}. {last}"
        elif variant == "nick" and len(first) > 4:
            rec["full_name"] = f"{first[:4]} {last}"
        rec["skills"] = rng.sample(rec["skills"], k=max(1, len(rec["skills"]) - rng.randint(0, 3)))
        rec["projects"] = rec["projects"][: rng.randint(0, len(rec["projects"]))]
        if rng.random() < 0.3:
            rec["education"] = []
        if rng.random() < 0.5:
            rec["links"] = [lnk for lnk in rec["links"] if "github" in lnk]
        rec["summary"] = ""
        return rec

    def generate(self, n: int = 1000) -> SyntheticDataset:
        """Generate ``n`` distinct people (plus duplicates) and job postings."""
        records: list[dict[str, Any]] = []
        owners: list[int] = []
        truth: list[dict[str, Any]] = []
        for pid in range(n):
            p = self._person(pid)
            truth.append(p["truth"] | {"pid": pid})
            primary = dict(p["record"], source_hint="people_api_sim", source_ref=f"syn-{pid}-a")
            records.append(primary)
            owners.append(pid)
            if self.rng.random() < self.duplicate_rate:
                dup = dict(self._duplicate(p), source_hint="web_sim", source_ref=f"syn-{pid}-b")
                records.append(dup)
                owners.append(pid)
        order = list(range(len(records)))
        self.rng.shuffle(order)
        return SyntheticDataset(
            records=[records[i] for i in order],
            truth_person_of_record=[owners[i] for i in order],
            truth=truth,
            job_postings=self.job_postings(max(40, n // 6)),
        )

    # ------------------------------------------------------------ job postings
    def job_postings(self, n: int) -> list[dict[str, Any]]:
        rng = self.rng
        out = []
        for i in range(n):
            _role, _, variants, pool = _weighted(rng, [(t, t[1]) for t in ROLE_TEMPLATES])
            band = _weighted(rng, [("entry", 0.35), ("mid", 0.35), ("senior", 0.3)])
            title = rng.choice(LEVEL_TITLES.get(self.target, LEVEL_TITLES["*"])[band]).format(t=rng.choice(variants))
            loc = _weighted(rng, US_CITIES) + ", USA" if rng.random() < 0.85 else rng.choice(OTHER_LOCATIONS)[0]
            out.append(
                {
                    "source": "synthetic_jobs",
                    "company": self.target,
                    "title": title,
                    "location": loc,
                    "skills": rng.sample(pool, k=min(len(pool), rng.randint(4, 8))),
                    "posted_at": (self.ref - timedelta(days=rng.randint(0, 90))).isoformat(),
                    "url": f"https://careers.example.com/jobs/{i}",
                    "synthetic": True,
                }
            )
        return out
