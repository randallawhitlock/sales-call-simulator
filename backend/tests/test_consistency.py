"""Actor-consistency QA flags doctors, drugs and amounts the customer invented, and nothing it
was given or heard from the agent."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.content.loader import load_bundle
from app.engine.consistency import amounts_said, numbers_in
from tests.harness import Call

CASE_A = "scenario-101"


@pytest.mark.parametrize(
    ("text", "amounts"),
    [
        ("It's $150 a month.", [Decimal(150)]),
        ("about $1,234.50 a year", [Decimal("1234.50")]),
        ("maybe 35 dollars", [Decimal(35)]),
        ("fifty dollars a month", [Decimal(50)]),
        ("a hundred and fifty bucks", [Decimal(150)]),
        ("two thousand three hundred dollars", [Decimal(2300)]),
        ("forty-five dollars", [Decimal(45)]),
        ("a few dollars", []),
        ("Twenty milligrams, twice a day.", []),
    ],
)
def test_amounts_said(text: str, amounts: list[Decimal]) -> None:
    assert [a for a, _ in amounts_said(text)] == amounts


def test_numbers_in_reads_digits_and_words() -> None:
    found = numbers_in("maybe fifty dollars a month, 40 milligrams, a hundred and twenty days")
    assert {Decimal(50), Decimal(40), Decimal(120)} <= found


def flags(call: Call) -> list[tuple[str, str]]:
    return [(f["kind"], f["said"]) for f in call.get("/actor-qa")["flags"]]


def test_actor_qa_is_locked_until_the_call_ends(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.get("/actor-qa", expect=403)
    call.end()
    call.get("/actor-qa")


def test_facts_from_the_prompt_are_not_flagged(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.say("Who is your heart doctor, and what do you take?")
    call.hear("Dr. Priya Raman. I take apixaban and atorvastatin.")
    call.say("What could you pay?")
    call.hear("Maybe $50 a month, fifty dollars at most.")
    call.end()
    assert flags(call) == []


def test_invented_doctor_drug_and_amount_are_flagged(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.say("Anything else I should know?")
    u = call.hear("Dr. Fischer said to add lisinopril. My copay is $35, I think.")
    call.hear("I also see Doctor Okafor's nurse on Tuesday.")
    call.end()
    body = call.get("/actor-qa")
    assert [(f["kind"], f["said"]) for f in body["flags"]] == [
        ("doctor", "Fischer"),
        ("drug", "lisinopril"),
        ("amount", "$35"),
        ("doctor", "Okafor"),
    ]
    assert body["flags"][0]["utt_id"] == u
    assert body["actor"] == "scripted"


def test_repeating_what_the_agent_said_is_not_invention(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.say("Dr. Naomi Fischer is in network, the premium is 35 a month, lisinopril is tier 1.")
    call.hear("So Dr. Fischer, $35, and lisinopril. Okay.")
    call.end()
    assert flags(call) == []


def test_an_agent_line_after_the_customer_does_not_excuse_it(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.hear("My copay is $35.")
    call.say("You said $35?")
    call.end()
    assert flags(call) == [("amount", "$35")]


def test_weekday_after_doctor_is_not_a_name(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.hear("I'm seeing the doctor Tuesday.")
    call.end()
    assert flags(call) == []


@pytest.mark.parametrize("scenario_id", ["scenario-101", "scenario-102", "scenario-103"])
def test_the_scripted_customer_invents_nothing(client: TestClient, scenario_id: str) -> None:
    """The offline actor answers only from the case's facts, so it must come out clean. A flag
    here means the checker is too eager."""
    scenario = load_bundle().scenario(scenario_id)
    call = Call(client, scenario_id).start()
    for topic in sorted({t for k in scenario.knowledge for t in k.topics}):
        call.turn(f"Can you tell me about your {topic}?")
    call.end()
    assert flags(call) == []
