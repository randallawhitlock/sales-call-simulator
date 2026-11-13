"""What the learner did, one plain sentence per logged action.

The debrief links each finding to the events behind it, and the judge reads the same lines to
place speech against research. Names come from the catalog, so a sentence says "Dr. Priya Raman
at Alder Heart Associates Northside" where the log says ``raman-northside``.
"""

from collections.abc import Callable

from pydantic import BaseModel, JsonValue

from app.content.catalog import Catalog
from app.engine.events import Event, EventType

SCOPE_PRODUCTS = {
    "ma": "Medicare Advantage",
    "pdp": "drug plans",
    "medigap": "Medigap",
    "dental_vision_hearing": "dental, vision and hearing",
}
SECTION_NAMES = {
    "doctors": "doctors",
    "drugs": "prescriptions",
    "pharmacy": "pharmacy",
    "coverage": "coverage",
    "contact": "contact details",
    "notes": "notes",
    "disposition": "decision",
}


class TimelineEntry(BaseModel):
    seq: int
    t_ms: int
    type: EventType
    utt_id: str | None = None
    """Set on utterance entries, which carry no summary: the transcript has the words."""
    summary: str


def timeline(events: list[Event], catalog: Catalog) -> list[TimelineEntry]:
    """The whole log in order, so a reader can see what was said around each action."""
    return [
        TimelineEntry(
            seq=e.seq,
            t_ms=e.t_ms,
            type=e.type,
            utt_id=e.utt_id,
            summary="" if e.type is EventType.UTTERANCE else describe(e, catalog),
        )
        for e in events
    ]


def describe(e: Event, catalog: Catalog) -> str:
    """One sentence for one event. Ids the catalog doesn't know are shown as they are."""
    return _DESCRIBERS.get(e.type, _plain)(e, _Names(catalog))


def practice_label(catalog: Catalog, practice_id: str) -> str:
    return _Names(catalog).practice(practice_id)


def drug_label(catalog: Catalog, drug_id: str) -> str:
    return _Names(catalog).drug(drug_id)


class _Names:
    def __init__(self, catalog: Catalog) -> None:
        self.c = catalog

    def _try(self, build: Callable[[str], str], value: JsonValue) -> str:
        key = str(value)
        try:
            return build(key)
        except KeyError:
            return key

    def plan(self, plan_id: JsonValue) -> str:
        return self._try(lambda i: self.c.plan(i).name, plan_id)

    def practice(self, practice_id: JsonValue) -> str:
        def build(i: str) -> str:
            practice = self.c.practice(i)
            provider = self.c.provider(practice.provider_id)
            return f"{provider.name} at {self.c.location(practice.location_id).name}"

        return self._try(build, practice_id)

    def drug(self, drug_id: JsonValue) -> str:
        def build(i: str) -> str:
            drug = self.c.drug(i)
            return f"{drug.name} {drug.strength}"

        return self._try(build, drug_id)

    def pharmacy(self, pharmacy_id: JsonValue) -> str:
        return self._try(lambda i: self.c.pharmacy(i).name, pharmacy_id)

    def document(self, document_id: JsonValue) -> str:
        return self._try(lambda i: self.c.document(i).title, document_id)


def _plain(e: Event, n: _Names) -> str:
    return e.type.value.replace("_", " ").capitalize() + "."


def _items(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _search(e: Event, n: _Names) -> str:
    p = e.payload
    found = _count(len(_items(p.get("result_ids"))), "result")
    kind = p.get("kind")
    if kind == "plans":
        return f"Searched plans for ZIP {p.get('zip')}: {found}."
    terms = [f'"{p[k]}"' for k in ("q", "specialty") if p.get(k)]
    if kind == "pharmacies" and p.get("zip"):
        terms.append(f"ZIP {p['zip']}")
    what = {"providers": "doctors", "drugs": "drugs", "pharmacies": "pharmacies"}.get(
        str(kind), str(kind)
    )
    return f"Searched {what}{' for ' + ', '.join(terms) if terms else ''}: {found}."


def _restrictions(row: dict[str, JsonValue]) -> str:
    if row.get("covered") is not True:
        return "not covered"
    parts = ["covered"]
    if row.get("tier") is not None:
        parts.append(f"tier {row['tier']}")
    if row.get("prior_auth"):
        parts.append("prior authorization")
    if row.get("step_therapy"):
        parts.append("step therapy")
    if row.get("quantity_limit"):
        parts.append(f"quantity limit {row['quantity_limit']}")
    return ", ".join(parts)


def _network(in_network: JsonValue, preferred: JsonValue = None) -> str:
    if in_network is not True:
        return "out of network"
    return "in network, preferred" if preferred is True else "in network"


def _lookup(e: Event, n: _Names) -> str:
    p = e.payload
    kind = p.get("kind")
    if kind == "plan_detail":
        return f"Opened plan details for {n.plan(p.get('plan_id'))}."
    if kind == "provider_network":
        return (
            f"Checked {n.practice(p.get('practice_id'))} on {n.plan(p.get('plan_id'))} for "
            f"{p.get('year')}: {_network(p.get('in_network'))}."
        )
    if kind == "formulary":
        return (
            f"Checked {n.drug(p.get('drug_id'))} on {n.plan(p.get('plan_id'))}: {_restrictions(p)}."
        )
    if kind == "pharmacy_network":
        return (
            f"Checked {n.pharmacy(p.get('pharmacy_id'))} on {n.plan(p.get('plan_id'))}: "
            f"{_network(p.get('in_network'), p.get('preferred'))}."
        )
    if kind == "compare":
        plans = ", ".join(n.plan(i) for i in _items(p.get("plan_ids")))
        drugs = _count(len(_items(p.get("drug_ids"))), "drug")
        lines = [
            f"{n.drug(row.get('drug_id'))} on {n.plan(row.get('plan_id'))} {_restrictions(row)}"
            for row in _items(p.get("lines"))
            if isinstance(row, dict)
        ]
        pharmacy = [
            f"{n.plan(row.get('plan_id'))} {_network(row.get('in_network'), row.get('preferred'))}"
            for row in _items(p.get("pharmacy"))
            if isinstance(row, dict)
        ]
        shown = "; ".join([*lines, *(f"pharmacy on {x}" for x in pharmacy)])
        return (
            f"Compared {plans} with {drugs} at {n.pharmacy(p.get('pharmacy_id'))}"
            f"{': ' + shown if shown else ''}."
        )
    return _plain(e, n)


def _evidence(e: Event, n: _Names) -> str:
    note = str(e.payload.get("note") or "")
    return f"Pinned event {e.payload.get('source_seq')} as evidence{': ' + note if note else ''}."


def _scope(e: Event, n: _Names) -> str:
    products = [SCOPE_PRODUCTS.get(str(x), str(x)) for x in _items(e.payload.get("products"))]
    return f"Recorded scope of appointment ({e.payload.get('method')}): {', '.join(products)}."


def _record(e: Event, n: _Names) -> str:
    section = str(e.payload.get("section"))
    return f"Saved the record's {SECTION_NAMES.get(section, section)}."


def _fields(e: Event) -> dict[str, JsonValue]:
    fields = e.payload.get("fields")
    return fields if isinstance(fields, dict) else {}


def _followup(e: Event, n: _Names) -> str:
    p = e.payload
    return f"Scheduled a follow-up by {p.get('method')} for {p.get('due_date')}: {p.get('reason')}."


_DESCRIBERS: dict[EventType, Callable[[Event, _Names], str]] = {
    EventType.CALL_STARTED: lambda e, n: "Call started.",
    EventType.CALL_ENDED: lambda e, n: "Call ended.",
    EventType.HOLD_STARTED: lambda e, n: "Put the customer on hold.",
    EventType.HOLD_ENDED: lambda e, n: "Took the customer off hold.",
    EventType.SCOPE_RECORDED: _scope,
    EventType.SEARCH: _search,
    EventType.LOOKUP: _lookup,
    EventType.DOCUMENT_OPENED: lambda e, n: f"Opened {n.document(e.payload.get('document_id'))}.",
    EventType.EVIDENCE_PINNED: _evidence,
    EventType.RECORD_SAVED: _record,
    EventType.FOLLOWUP_CREATED: _followup,
    EventType.APPLICATION_CREATED: lambda e, n: (
        f"Started application {e.payload.get('application_id')} for "
        f"{n.plan(_fields(e).get('plan_id'))}."
    ),
    EventType.APPLICATION_UPDATED: lambda e, n: (
        f"Changed application {e.payload.get('application_id')}."
    ),
    EventType.PECL_ITEM_MARKED: lambda e, n: (
        f"Marked checklist item {int(str(e.payload.get('item_index', 0))) + 1} reviewed on "
        f"{e.payload.get('application_id')}."
    ),
    EventType.CONSENT_MARKED: lambda e, n: (
        f"Marked {e.payload.get('utt_id')} as the customer's consent on "
        f"{e.payload.get('application_id')}."
    ),
    EventType.APPLICATION_SUBMITTED: lambda e, n: (
        f"Submitted application {e.payload.get('application_id')} for "
        f"{n.plan(_fields(e).get('plan_id'))}."
    ),
    EventType.APPLICATION_WITHDRAWN: lambda e, n: (
        f"Withdrew application {e.payload.get('application_id')}."
    ),
}
