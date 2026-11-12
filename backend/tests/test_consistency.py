"""Actor-consistency QA flags doctors, drugs and amounts the customer invented, and nothing it
was given or heard from the agent."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.actor.prompt import PROMPT_VERSION
from app.content.loader import load_bundle
from app.engine.consistency import amounts_said, numbers_in
from app.engine.events import Channel, Speaker, UtteranceIn
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
        ("It's one, two hundred dollars for the other.", [Decimal(200)]),
        ("I only want the one, and fifty dollars is my limit", [Decimal(50)]),
        ("I only want the one and fifty dollars is my limit", [Decimal(50)]),
        ("thirty five a month, a hundred dollars a year", [Decimal(100)]),
        ("two hundred and fifty dollars", [Decimal(250)]),
        ("A thousand and one dollars", [Decimal(1001)]),
        ("Twenty milligrams, twice a day.", []),
    ],
)
def test_amounts_said(text: str, amounts: list[Decimal]) -> None:
    assert [a for a, _ in amounts_said(text)] == amounts


def test_numbers_in_reads_digits_and_words() -> None:
    found = numbers_in("maybe fifty dollars a month, 40 milligrams, a hundred and twenty days")
    assert {Decimal(50), Decimal(40), Decimal(120)} <= found


def test_numbers_in_keeps_separate_numbers_apart() -> None:
    found = numbers_in("It's thirty five a month for one, two hundred for the other.")
    assert {Decimal(35), Decimal(1), Decimal(200)} <= found
    assert Decimal(300) not in found
    assert Decimal(51) not in numbers_in("I only want the one, and fifty dollars is my limit")


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


def test_a_title_after_doctor_is_not_the_name(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.hear("I see my doctor Dr. Smith.")
    call.hear("My doctor Dr. Raman is the one I trust.")
    call.end()
    assert flags(call) == [("doctor", "Smith")]


def test_a_given_surname_with_another_first_name_is_flagged(client: TestClient) -> None:
    """The prompt gives Dr. Priya Raman only; the catalog also lists an Arjun Raman."""
    call = Call(client, CASE_A).start()
    call.hear("My heart doctor is Dr. Arjun Raman.")
    call.hear("Dr. Priya Raman, I mean. Dr. Raman's office is close.")
    call.end()
    assert flags(call) == [("doctor", "Arjun Raman")]


def test_a_number_the_agent_said_beside_another_is_not_invented(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.say("It's thirty five a month for one, two hundred for the other.")
    call.hear("Two hundred dollars?")
    call.hear("So three hundred dollars?")
    call.end()
    assert flags(call) == [("amount", "three hundred dollars")]


def test_only_heard_words_count_for_either_speaker(client: TestClient) -> None:
    """Voice barge-in: words that were never played were never said."""
    call = Call(client, CASE_A).start()
    call.service.add_utterance(
        call.sid,
        UtteranceIn(
            client_utt_id="learner-voice-0001",
            speaker=Speaker.LEARNER,
            channel=Channel.VOICE,
            text="The premium is $35 a month.",
            heard_text="The premium is",
            interrupted=True,
        ),
    )
    call.hear("So it's $35?")
    call.hear("Dr. Fischer said to add lisinopril.", heard="Dr. Fischer said", interrupted=True)
    call.hear("My copay is $80 with Dr. Okafor.", heard="", interrupted=True)
    call.end()
    body = call.get("/actor-qa")
    assert [(f["kind"], f["said"]) for f in body["flags"]] == [
        ("amount", "$35"),
        ("doctor", "Fischer"),
    ]
    assert body["flags"][1]["text"] == "Dr. Fischer said"


def test_actor_qa_refuses_a_prompt_the_server_no_longer_runs(client: TestClient) -> None:
    """QA rebuilds the prompt from the running template, so it must be the pinned one. The
    actor may differ: nothing is generated in review."""
    call = Call(client, CASE_A).start()
    call.hear("Dr. Fischer said so.")
    call.end()
    row = call.service.get(call.sid)
    call.service.store.put_session(row.model_copy(update={"actor": "gemini:old-model"}))
    body = call.get("/actor-qa")
    assert (body["actor"], body["prompt_version"]) == ("gemini:old-model", PROMPT_VERSION)
    assert flags(call) == [("doctor", "Fischer")]
    call.service.store.put_session(row.model_copy(update={"prompt_version": "customer-old"}))
    body = call.get("/actor-qa", expect=409)
    assert "customer-old" in body["detail"] and PROMPT_VERSION in body["detail"]
