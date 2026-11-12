"""Load and validate content files from ``content/``.

Each file validates against its own model. ``load_bundle`` then checks the files against each
other, so a scenario that names a plan the catalog lacks, or a frame whose disclaimer numbers
disagree with the catalog, stops the server at startup.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from app.content.catalog import Catalog, DocumentKind
from app.content.common import OutcomeKind
from app.content.rubric import Rubric
from app.content.rules import RuleSet
from app.content.scenario import CallFrame, Scenario

CONTENT_DIR_ENV = "SIM_CONTENT_DIR"
MVP_RULESET = Path("rules/mvp-2027.json")
MVP_FRAME = Path("scenarios/frames/aep-2027.json")
MVP_CATALOG = Path("catalog/2027-v1.json")
MVP_RUBRIC = Path("rubric/mvp-1.json")
SCENARIO_DIR = Path("scenarios")

CUSTOMER_JARGON = (
    "formulary",
    "tier",
    "moop",
    "ssbci",
    "pecl",
    "tpmo",
    "scope of appointment",
    "prior authorization",
    "step therapy",
    "election period",
    "creditable",
    "trn-",
)
"""Words a beneficiary wouldn't use. Their presence in customer knowledge means an author let
agent vocabulary into the actor's mouth."""


class ContentError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("content problems:\n- " + "\n- ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class ContentBundle:
    ruleset: RuleSet
    frame: CallFrame
    catalog: Catalog
    rubric: Rubric
    scenarios: Mapping[str, Scenario]

    def scenario(self, scenario_id: str) -> Scenario:
        return self.scenarios[scenario_id]


def content_root() -> Path:
    override = os.environ.get(CONTENT_DIR_ENV)
    return Path(override) if override else Path(__file__).resolve().parents[3] / "content"


def load_model[T: BaseModel](path: Path, model: type[T]) -> T:
    return model.model_validate_json(path.read_bytes())


def load_ruleset(root: Path | None = None) -> RuleSet:
    return load_model((root or content_root()) / MVP_RULESET, RuleSet)


def load_call_frame(root: Path | None = None) -> CallFrame:
    return load_model((root or content_root()) / MVP_FRAME, CallFrame)


def load_catalog(root: Path | None = None) -> Catalog:
    return load_model((root or content_root()) / MVP_CATALOG, Catalog)


def load_rubric(root: Path | None = None) -> Rubric:
    return load_model((root or content_root()) / MVP_RUBRIC, Rubric)


def load_scenarios(root: Path | None = None) -> dict[str, Scenario]:
    paths = sorted(((root or content_root()) / SCENARIO_DIR).glob("*.json"))
    scenarios = [load_model(p, Scenario) for p in paths]
    ids = [s.scenario_id for s in scenarios]
    if len(set(ids)) != len(ids):
        raise ContentError([f"duplicate scenario ids: {ids}"])
    return {s.scenario_id: s for s in scenarios}


def load_bundle(root: Path | None = None) -> ContentBundle:
    bundle = ContentBundle(
        ruleset=load_ruleset(root),
        frame=load_call_frame(root),
        catalog=load_catalog(root),
        rubric=load_rubric(root),
        scenarios=load_scenarios(root),
    )
    problems = validate_bundle(bundle)
    if problems:
        raise ContentError(problems)
    return bundle


def validate_bundle(b: ContentBundle) -> list[str]:
    problems: list[str] = []
    problems += _frame_vs_catalog(b)
    problems += _catalog_completeness(b.catalog)
    problems += _rubric_vs_rules(b)
    for scenario in b.scenarios.values():
        problems += [f"{scenario.scenario_id}: {p}" for p in _scenario_problems(scenario, b)]
    return problems


def _frame_vs_catalog(b: ContentBundle) -> list[str]:
    f, c = b.frame, b.catalog
    out: list[str] = []
    if f.county_id != c.county.county_id:
        out.append(f"frame county {f.county_id} is not the catalog county {c.county.county_id}")
    if f.plan_year != c.plan_year:
        out.append(f"frame plan year {f.plan_year} is not the catalog plan year {c.plan_year}")
    if f.carriers_represented != len(c.carriers):
        out.append(
            f"frame represents {f.carriers_represented} organizations; "
            f"the catalog has {len(c.carriers)} carriers"
        )
    if f.products_offered != len(c.plans):
        out.append(f"frame offers {f.products_offered} products; the catalog has {len(c.plans)}")
    return out


def _catalog_completeness(c: Catalog) -> list[str]:
    out: list[str] = []
    for plan in c.plans:
        for kind in (DocumentKind.SUMMARY_OF_BENEFITS, DocumentKind.PRE_ENROLLMENT_CHECKLIST):
            count = sum(
                1
                for d in c.documents
                if d.plan_id == plan.plan_id and d.kind is kind and d.plan_year == c.plan_year
            )
            if count != 1:
                out.append(f"{plan.plan_id} needs one {c.plan_year} {kind.value}, found {count}")
    rows = sorted(c.participation, key=lambda r: (r.practice_id, r.network_id, r.effective_from))
    for prev, row in zip(rows, rows[1:], strict=False):
        same = (prev.practice_id, prev.network_id) == (row.practice_id, row.network_id)
        if same and (prev.effective_to is None or prev.effective_to >= row.effective_from):
            out.append(f"overlapping participation for {row.practice_id} in {row.network_id}")
    return out


def _rubric_vs_rules(b: ContentBundle) -> list[str]:
    out: list[str] = []
    rule_ids = {r.rule_id for r in b.ruleset.rules}
    for criterion in b.rubric.criteria:
        unknown = sorted(set(criterion.rule_ids) - rule_ids)
        if unknown:
            out.append(f"rubric {criterion.criterion_id} cites unknown rules {unknown}")
    rubric_checks = {c.check_id for c in b.rubric.criteria if c.check_id is not None}
    for rule in b.ruleset.rules:
        ungraded = sorted(str(c) for c in set(rule.check_ids) - rubric_checks)
        if ungraded:
            out.append(f"rule {rule.rule_id} checks {ungraded} have no rubric criterion")
    return out


def _scenario_problems(s: Scenario, b: ContentBundle) -> list[str]:
    c = b.catalog
    out: list[str] = []
    if s.frame_id != b.frame.frame_id:
        out.append(f"frame {s.frame_id} is not loaded")
    if s.catalog_version != c.catalog_version:
        out.append(f"catalog {s.catalog_version} is not loaded")

    plans = {p.plan_id for p in c.plans}
    practices = {p.practice_id for p in c.practices}
    drugs = {d.drug_id for d in c.drugs}
    pharmacies = {p.pharmacy_id for p in c.pharmacies}
    truth = s.truth

    if truth.current_plan_id is not None and truth.current_plan_id not in plans:
        out.append(f"current plan {truth.current_plan_id} is not in the catalog")
    for practice_id in (*truth.must_keep_practices, truth.pcp_practice_id):
        if practice_id is not None and practice_id not in practices:
            out.append(f"practice {practice_id} is not in the catalog")
    truth_drugs = [d.drug_id for d in truth.drugs]
    if len(set(truth_drugs)) != len(truth_drugs):
        out.append("truth lists a drug twice")
    out += [f"drug {d} is not in the catalog" for d in truth_drugs if d not in drugs]
    if truth.pharmacy_id not in pharmacies:
        out.append(f"pharmacy {truth.pharmacy_id} is not in the catalog")
    for zip_code in (s.crm_seed.zip, truth.residence_zip):
        if zip_code not in c.county.zips:
            out.append(f"ZIP {zip_code} is outside {c.county.name}")

    enroll_plans: list[str] = []
    for outcome in s.outcomes:
        out += [
            f"outcome plan {p} is not in the catalog" for p in outcome.plan_ids if p not in plans
        ]
        if outcome.kind is OutcomeKind.ENROLL:
            enroll_plans += outcome.plan_ids
    if len(set(enroll_plans)) != len(enroll_plans):
        out.append("a plan appears in more than one enroll outcome")
    unclassified = sorted(plans - set(enroll_plans) - {truth.current_plan_id or ""})
    if unclassified:
        out.append(f"plans with no enroll verdict: {unclassified}")

    rule_ids = {r.rule_id for r in b.ruleset.rules}
    out += [f"rule {r} is not in the ruleset" for r in s.rule_ids if r not in rule_ids]
    criteria = {cr.criterion_id for cr in b.rubric.criteria}
    out += [
        f"N/A criterion {k} is not in the rubric" for k in s.not_applicable if k not in criteria
    ]

    out += _knowledge_leaks(s, c)
    return out


def internal_ids(c: Catalog) -> set[str]:
    """Catalog ids the customer could never know. Every one contains a hyphen, so a plain-word
    match in prose is never a false hit."""
    return {
        *(p.plan_id for p in c.plans),
        *(p.practice_id for p in c.practices),
        *(d.drug_id for d in c.drugs),
        *(p.pharmacy_id for p in c.pharmacies),
        *(loc.location_id for loc in c.locations),
        *(p.network_id for p in c.plans),
        *(p.formulary_id for p in c.plans),
    }


def jargon_in(text: str) -> list[str]:
    folded = text.casefold()
    return [w for w in CUSTOMER_JARGON if re.search(rf"(?<![a-z]){re.escape(w)}", folded)]


def _knowledge_leaks(s: Scenario, c: Catalog) -> list[str]:
    """The build-time leak guard: customer knowledge stays in lay words."""
    ids = internal_ids(c)
    out: list[str] = []
    lay = [(f"knowledge {k.item_id}", k.lay_text) for k in s.knowledge] + [
        (f"objection {o.objection_id}", f"{o.lay_text} {o.settles_when or ''}")
        for o in s.objections
    ]
    for where, raw in lay:
        text = raw.casefold()
        for word in jargon_in(text):
            out.append(f"{where} uses agent jargon {word!r}")
        for internal in ids:
            if internal.casefold() in text:
                out.append(f"{where} contains internal id {internal!r}")
    return out


def aep_end(frame: CallFrame) -> date:
    return date(frame.plan_year - 1, 12, 7)
