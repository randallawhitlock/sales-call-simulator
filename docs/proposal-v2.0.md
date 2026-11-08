# Medicare Sales Training Simulator

## CMS-based product proposal, configurable workflow specification, and implementation roadmap

Version 2.0 • November 7, 2026

**Product direction:** create an original, vendor-neutral Medicare sales training application grounded in public CMS requirements and Medicare educational resources. Agents practice realistic conversations while researching synthetic plans, documenting customer needs, preparing simulated applications, and optionally completing a verification handoff. Organizations can add authorized workflow, evidence, script, and quality-assurance configurations without changing the shared application.

The baseline product is independent of any employer, CRM, quoting platform, or carrier. Its interface, sample content, and training workflows are original designs. This proposal identifies relevant public requirements; it does not certify the future application or replace annual agent training, carrier certification, or a complete regulatory implementation review.

## 1. Executive proposal

### Objective

Train agents to make accurate, understandable, beneficiary-centered recommendations while performing the operational work a Medicare sales call requires. Successful outcomes include an appropriate plan selection, retaining current coverage, seeking clarification, arranging a follow-up, and stopping an inappropriate enrollment.

The learner should speak to a realistic customer while searching providers and prescriptions, comparing meaningful plan tradeoffs, creating records, and responding to consequences of missing or inaccurate work. Conversational realism and operational realism must reinforce each other.

### Product principles

1. **Public-source foundation:** regulatory content has a public source, effective period, scope, and review status.
2. **Original application design:** independent layouts, labels, fictional organizations, sample scripts, and synthetic records.
3. **Consistent facts:** customer answers, documents, search results, applications, and evaluation use the same scenario version.
4. **Configurable operations:** an agency's handoff sequence, fax-proof policy, timer, or note template is an optional policy, not automatically a federal rule.
5. **Evidence-based grading:** findings link to observable actions, saved records, documents, and conversational segments.
6. **Customer choice:** faster calls and more enrollments are not automatically better performance.
7. **Usable general defaults:** the product works without private workplace documents or production software.

### Audience

| User | Primary value |
|---|---|
| New agent | Practice a complete call and workflow without risking real coverage |
| Experienced agent | Rehearse difficult conversations and unfamiliar plan structures |
| Instructor | Assign cases, inspect evidence, coach, and measure improvement |
| Agency administrator | Add authorized local procedures in a private configuration |
| Curriculum reviewer | Maintain dated rules, approve scenarios, resolve grading disputes |
| Verification trainee, later phase | Practice a separate review role with its own rubric |

### Initial scope

Begin with Medicare Advantage with prescription drug coverage, HMO/PPO examples, one frozen coverage year, one selected county, synthetic customers, and browser audio. Design extension points for standalone Part D, C-SNP, and D-SNP modules; release each after its particular rules and cases are reviewed.

Proposed pilot content: 8–12 fictional plans, approximately 12 customer cases, 30–50 providers with multiple locations, 40–60 medication entries, and 6–10 pharmacies. These are content targets, not regulatory minimums.

Defer nationwide live-plan ingestion, production enrollment, actual phone-network integration, commission accounting, Medigap sales, and a general-purpose workflow builder. The first release should be a dependable practice product.

## 2. Shared core and optional configurations

### Four layers

| Layer | Content | Owner | Example |
|---|---|---|---|
| Public regulatory baseline | Applicable CMS rules, enrollment guidance, public model materials | Qualified curriculum/compliance reviewer | Pre-enrollment requirements |
| Jurisdiction and plan rules | State obligations and requirements of the selected plan | Authorized content reviewer | SNP qualifications and plan-specific conditions |
| Organization workflow | Local documentation, roles, handoffs, corrections, escalation | Tenant administrator and approver | Extra evidence before internal review |
| Training settings | Difficulty, hints, pacing, scenarios, educational scoring | Instructor | Customer patience and coaching visibility |

Layers compose by applicability; this is not “last setting wins.” An organization setting cannot waive an applicable regulatory obligation. A plan rule cannot be invented from a general plan-type label. Conflicting mandatory settings prevent publication of a scored profile until resolved.

### Use of supplied operational examples

The supplied documents inform generic capabilities: conditional forms, evidence collection, review timers, correction routes, multiple-customer cases, optional follow-up activities, and configurable rubrics.

The shared product implements those capabilities in original code and language. It does not embed an employer's scripts, proprietary scorecard, channel names, staff roster, private URLs, vendor payload keys, screenshots, or carrier-specific operational exceptions. Those materials are not public CMS sources.

An organization may later create a private, authorized policy pack. Removing that pack must leave a complete general Medicare training application. Private packs are not bundled with public sample content.

### General-product defaults

| Setting | Default |
|---|---|
| Workspaces | Original Plan Research, Customer Records, Application Review, Reference Center, and Coaching |
| Separate TPV-style handoff | Optional module, not mandatory for every case |
| Internal handoff deadline | None; measure time without an invented federal limit |
| PCP assignment | Follows applicable plan requirements |
| Mandatory fax screenshot | No universal requirement; enabled by an applicable reviewed rule |
| Notes | Original structured assessment and recommendation template |
| Evidence freshness | Requirement-specific; no universal same-day rule |
| QA | Original educational rubric, separate configurable critical findings |
| Dispositions | Complete, needs correction, deferred, declined, or review required |
| External actions | Simulated only |

## 3. CMS-based training foundation

### Starter requirements map

This map identifies public subject areas and proposed teaching mechanisms. It is not an exhaustive restatement of Medicare regulation. Part C/Part D differences, dates, exceptions, state obligations, and carrier processes remain explicit.

| ID | Public-source subject | Proposed implementation | Source |
|---|---|---|---|
| REG-01 | Beneficiary contact and agreed scope | Call-origin/permission records; scope selection and expansion cases | S1 |
| REG-02 | Accurate marketing and voluntary choice | Unsupported-claim and pressure findings; valid refusal/no-change outcomes | S2 |
| REG-03 | Beneficiary needs and relevant coverage discussion | Needs assessment and evidence-linked comparison tasks | S3 |
| REG-04 | Eligibility, election periods, effective dates | Dated eligibility cases and election-period worksheet | S4, S5 |
| REG-05 | Pre-Enrollment Checklist and required materials | Review tasks and correct plan/year document access | S6, S7 |
| REG-06 | Enrollment acknowledgments and consent | Reviewed content, validated variable fields, response evidence | S4, S8 |
| REG-07 | Applicable TPMO disclosures, recording, data sharing | Date-aware role/call-type checks; distinct permission records | S3, S6 |
| REG-08 | Plan-type network/referral differences | HMO/PPO explanation and comparison exercises | S9, S10 |
| REG-09 | Special Needs Plan qualification | Separately reviewed product modules | S11 |
| REG-10 | Conditional supplemental benefits | Separate listed benefit from individual eligibility | S12 |

Public MA requirements address needs discussion and beneficiary understanding. They do not establish one mandatory software vendor or a universal live third-party handoff with a fixed timer. The baseline teaches relevant outcomes; optional workflows assign operational responsibilities. [S3]

The MA Pre-Enrollment Checklist concerns plan documents, networks, drug coverage, cost sharing, plan rules, and effects on existing coverage. For telephonic enrollment, its contents must be reviewed before enrollment completion. Track that review separately from providing access to the selected plan's Summary of Benefits. [S6]

### Distinct permissions

Store permission to contact, agreed scope, applicable recording notice/consent, permission for another participant, legal authority to act, applicable data-sharing consent, and enrollment consent separately. One affirmative answer must not automatically populate all of them.

A helper, interpreter, spouse, and legally authorized representative are different roles. Provide the necessary scenario facts; do not assume family relationship alone grants authority.

### Eligibility

Keep product eligibility, election-period availability, effective-date logic, and optional-benefit qualification separate. Medicare's joining guidance distinguishes Medicare Advantage's A/B requirements from those for a standalone drug plan. [S5]

The learner records underlying events and dates, supporting facts, evidence, and unknowns. Selecting a code does not establish eligibility. Do not infer Medicaid, LIS, a qualifying chronic condition, or a food-benefit entitlement from age or medication use.

### Effective dates and rule versions

Pin the simulated call date, application date, coverage effective date, plan year, jurisdiction, and rule-pack version. These dates may differ during annual enrollment.

CMS publishes year-specific guidance and materials, with implementation timing that may differ from the coverage year. [S4, S13] Do not hard-code appointment waiting periods, recording retention periods, annual financial limits, form fields, or election-period interpretations as timeless rules.

Public summaries, annual training documents, and amended regulations may update on different schedules. Reconcile effective dates and applicability before publication. Store the selected source version and reviewer rationale.

### Training controls are not production obligations

The simulator may intentionally allow mistakes so learners can experience and review their consequences. Guided mode can prevent invalid next steps; assessment mode records attempted mistakes without treating them as compliant.

Synthetic practice applications and employee training recordings do not automatically inherit every production enrollment record-retention requirement. Define a separate training-data policy while teaching the relevant production obligations in scenarios.

## 4. Original application workspaces

| Workspace | Functions |
|---|---|
| Call Console | Audio, participant state, hold, mute, conference, transfer, resume, elapsed time |
| Plan Research | Coverage lookup, service area, providers, drugs, pharmacy, results, comparisons, documents |
| Customer Records | People, households, opportunities, assessments, notes, evidence, tasks, corrections |
| Application Review | Draft application, validation, consent evidence, revisions, statuses, optional review |
| Reference Center | Public-source explanations, glossary, approved training content, applicable private procedures |
| Coaching | Synchronized audio/transcript/action history, findings, corrections, targeted retry |
| Administration | Assignments, scenarios, source/rule review, configuration, publishing, access |

Call connection and audibility are distinct. A beneficiary on hold cannot hear a private consultation. A learner who has left an optional verification call cannot continue listening as though still participating; later review is a separate debrief capability.

The Reference Center distinguishes standardized text, model-based material, original educational wording, and private organization content. These categories have different editing permissions; public Part D rules distinguish standardized and model materials. [S7]

## 5. End-to-end learner workflow

1. Receive the inquiry and identify the caller's reason and expectations.
2. Establish the permitted conversation using applicable contact, scope, disclosure, and recording requirements.
3. Find or create the customer; resolve duplicates and identify decision-maker/representative roles.
4. Gather current coverage and facts relevant to eligibility and enrollment timing.
5. Discover priorities, doctors, recurring care, prescriptions, pharmacy, affordability, other coverage, travel, and accessibility needs.
6. Research available plans for the right year and service area.
7. Verify provider locations, formulary status, pharmacy participation, costs, restrictions, and uncertainty.
8. Compare current coverage with realistic alternatives; explain losses and gains.
9. Support an informed decision: proceed, retain coverage, defer, or stop.
10. Prepare the mock application and perform applicable pre-enrollment and acknowledgment tasks.
11. Save the assessment, rationale, evidence, decision, and next steps.
12. Complete optional review/handoff and correction if the scenario enables it.
13. Close the exercise and review evidence-based feedback.

Audio disconnection, session completion, application submission, and review completion are separate events. None should silently imply another.

## 6. Plan Research detailed specification

### Customer lookup

Search by name, phone, date of birth, synthetic Medicare identifier, and internal customer ID. Support no match, multiple matches, partial information, and temporary unavailability. Results are visibly simulated coverage records, not forged government reports.

Display applicable A/B dates, current coverage, source date, and eligibility facts. Distinguish verified, customer-reported, unknown, declined, and contradictory values. Avoid exposing identifiers beyond the needs of the lesson.

### Plan results

Filters: plan year, state/county, product type, fictional carrier, premium, provider fit, formulary fit, and relevant special-needs criteria. If a ZIP crosses county boundaries, require clarification.

Cards show full training plan name/ID, type, premium, applicable medical deductible, medical maximum out-of-pocket, key copays, drug/pharmacy summary, conditional supplemental benefits, and document links. Uncertain eligibility is visible rather than silently treated as confirmed.

Compare current coverage and up to three alternatives as an original UX choice. Explain ranking criteria. Do not label the largest allowance or lowest premium “best” without reference to needs and tradeoffs.

### Provider search

Represent provider identity × practice location × plan/network × effective period. Fields include name, specialty, training identifier, carrier-specific ID where applicable, address, phone/fax, accepting-patient status, PCP eligibility, and evidence.

Search by names/variants, specialty, location, identifier, group, or facility. Results can be in-network, out-of-network, not found, stale, or conflicting. A matching name at another office is not a verified match.

An NPI identifies a provider; it is not automatically the carrier PCP ID or proof of participation. A discrepancy requires evidence and a resolution event. Writing “verified” in a note does not change the scenario truth.

For PPOs, explain the selected plan's out-of-network costs and coverage accurately. Medicare's PPO explanation states that choosing a PCP is not generally required. Any additional organization collection requirement must be labeled as such. [S10]

### Medications and pharmacy

Capture name, brand/generic distinction, strength, dosage form, quantity, days supply, refill schedule, and pharmacy. Display covered/non-formulary status separately from prior authorization, step therapy, and quantity limits.

Pharmacy participation is independent: preferred, standard, non-network, retail, or mail order as applicable. A pharmacy change can change cost. Keep medication coverage terminology precise even if a private organization uses different shorthand.

Use reviewed cost fixtures for the initial release. A complete annual Part D calculation engine is a separate capability requiring year-specific phases, subsidies, covered/noncovered spending, and validation. Do not multiply one deductible-month price by twelve and present it as a reliable annual estimate.

### Documents

Each plan needs a training Summary of Benefits, detailed benefit/rule reference, formulary, pharmacy/provider directories, and applicable pre-enrollment reference. An abbreviated reference must not be represented as a complete Evidence of Coverage.

All links resolve to the same plan version shown on screen. Wrong-plan/year, missing-page, and broken-link cases are deliberate scenario defects with answer keys, not accidental product errors.

## 7. Customer Records and application data

### Screens

Customer search/create; profile; household/representative relationships; opportunity; needs assessment; notes; documents; application; correction history; and follow-up tasks.

Learners must be able to create a record, save incomplete work, detect a duplicate, and return to an earlier opportunity. Separate household members have separate identifiers, consent, applications, and evidence.

### Field groups

| Group | Example fields | Distinction to preserve |
|---|---|---|
| Identity/contact | Name, DOB, synthetic ID, phone/email, preferences | Required fields depend on form version |
| Residence | Residential/mailing addresses, county, service area | Mailing address is not necessarily residence |
| Current coverage | A/B dates, present plan, source and date | Recollection versus verified fact |
| Other coverage | Employer/union, military, Medicaid, relevant coverage | Record uncertainty and possible impact |
| Needs | Priorities, must-keep doctors, usage, affordability, travel | Preferences are not eligibility findings |
| Drugs | Name, strength, form, quantity, days supply, pharmacy | Unknown is different from taking none |
| Representative | Role, authority, participation, permissions | Helper is not automatically authorized |
| Recommendation | Options, gains/losses, rationale, customer decision | Retaining coverage is valid |
| Enrollment timing | Period, event, evidence, requested date | Code selection is not qualification |
| Evidence | Source, person, plan/year/location, dates, version | Filename alone is not proof |
| Follow-up | Owner, action, unresolved issue, date | Scheduled verification is not completed |

### Original default notes template

Reason for inquiry; current coverage; customer priorities; eligibility/timing facts; providers and verification results; medication/pharmacy findings; other coverage; options considered; recommendation rationale; limitations discussed; customer decision; evidence reviewed; unresolved questions; and next steps.

Private packs may add conditional fields or authorized templates. The public default has no arbitrary character minimum. Completeness and consistency matter more than length.

### Application lifecycle

Use distinct canonical states: draft, ready for review, needs correction, consent documented, simulated submitted, withdrawn, and superseded. The active workflow controls allowed transitions. A cancellation request after submission is not the same operation as withdrawing an unsubmitted draft.

Each submission has an immutable revision and idempotency key. A rewrite creates a linked successor. Correcting notes alone does not correct an inconsistent application.

## 8. Configuration system

Build a bounded configuration schema first. A full visual workflow programming platform is outside the MVP.

### Configuration inventory

| Area | Supported settings | General default |
|---|---|---|
| Verification | None, same-agent, separate simulated reviewer, instructor | No compulsory third-party transfer |
| Handoff | Participant order, review stage, invitation/exit conditions | Optional |
| Timing | Targets, warning, correction window, pause/waiver, timeout route | Informational timing |
| PCP | Plan-based, optional, additional organization field | Plan-based |
| Provider IDs | Training ID, carrier ID, PCP ID, specific exceptions | Generic training identifiers |
| Physician evidence | Required contacts, fax, acceptable sources, matching | No universal fax-proof gate |
| Freshness | Date ranges, same-day policy, year match, cross-day recheck | Rule-specific |
| Notes | Templates, fields, conditional mandatory content, length | Original general template |
| Packet | Documents by product/stage | Applicable reviewed requirements |
| Correction authority | Agent, reviewer, instructor, new application | Explicit permissions |
| Dispositions | Retry, defer, return, rewrite, stop | Neutral canonical outcomes |
| Late additions | Who enters/checks new doctors or drugs | No universal count threshold |
| Multiple people | Separate forms, evidence, permissions, review tasks | Off in introductory cases |
| Scripts | Required content, original wording, authorized private scripts | General educational content |
| Forms | Field types, visibility, validation, summaries | Original schema and keys |
| QA | Weights, critical findings, N/A, confidence, overrides | Original educational rubric |
| Optional activities | HRA, onboarding, benefit activation, mail order | Off unless enabled |
| Technical conditions | Delayed saves, lookup failure, disconnect, locked record | Authored scenario events |
| Escalation | Mock queue, reviewer, notification | Internal simulator only |

### Administration and publication

Create/clone a private profile; select a published regulatory base; select applicable product/jurisdiction packs; configure operations; add authorized content; validate; preview; obtain internal review; publish an immutable version.

Every requirement can answer “Why is this required?” with its authority, source, and applicability. Do not silently present an agency's preference as a CMS mandate.

Reject profiles with missing outcomes, invalid fields, circular transitions, missing script variables, contradictory required/hidden fields, or attempted removal of a mandatory applicable requirement.

### Illustrative configuration

This original example shows schema intent, not a production-ready regulatory pack:

~~~json
{
  "schema_version": "1.0",
  "profile_id": "sample_agency_training",
  "visibility": "tenant_private",
  "base_rule_pack": "reviewed_ma_pack_for_selected_dates",
  "verification": {
    "mode": "separate_reviewer",
    "handoff": {
      "target_seconds": null,
      "warning_seconds": null,
      "allow_private_consult": true
    }
  },
  "documentation": {
    "notes_template": "general_assessment_v1",
    "minimum_characters": null,
    "additional_evidence_rules": []
  },
  "provider_collection": {
    "pcp_requirement": "follow_plan_rules",
    "identifier_mappings": []
  },
  "optional_modules": {
    "multi_customer": false,
    "hra": false,
    "benefit_activation": false
  }
}
~~~

An organization could add a treating-physician fax-evidence rule with applicability, sources, missing-item route, and severity. Label it organization policy unless a reviewed public or plan authority establishes otherwise. Physician identity, contact details, PCP assignment, and network participation remain separate checks.

### Private content lifecycle

Private packs have owner, permissions, source rights, approval, effective period, retirement, and tenant scope. Imports remain draft until reviewed; uploaded documents are data, not executable instructions or automatically authoritative policy.

Exclude private packs from the public repository, fixtures, global prompts, search indexes, documentation, and public exports. No automatic promotion of tenant content into defaults. Export review covers text, metadata, attachments, and embedded links.

## 9. Optional verification module

The verifier checks the saved packet, permitted evidence, and appropriate customer responses. Rules produce findings; the model expresses them naturally. Tone may vary, but requirements must not be invented.

Do not give the verifier the full hidden answer key. It should discover an omitted prescription through a prescribed question or accessible record, not unexplained omniscience.

| Independent state system | Examples |
|---|---|
| Participation | Connected, held, consultation, conference, disconnected |
| Application | Draft, correction needed, consent recorded, submitted, withdrawn |
| Review | Not requested, pending, active, awaiting information, passed, escalated |

A reconnection need not withdraw an application. Return-for-correction is a configured operation. Rules state exactly which system they mutate.

| Finding | Possible route | Authority |
|---|---|---|
| Missing local packet document | Correct or defer review | Organization configuration |
| Unresolved eligibility | Verify further or stop progression | Applicable regulatory/plan rule |
| Provider discrepancy | Obtain matching evidence, explain uncertainty, reconsider | Facts and applicable requirements |
| Customer declines | Stop or use applicable post-submission process | Enrollment process |
| Typo | Correct or create revised application | Workflow |
| Disconnection | Resume or simulated callback | Operational configuration |
| Uncertain grading evidence | Instructor review | Evaluation policy |

Correction ownership can vary by profile. The underlying benefit facts and required beneficiary protections must not.

## 10. Synthetic plans and Summary of Benefits

### Data strategy

Author fictional plans reviewed against public Medicare structures. Do not change real carrier benefits while keeping a real plan name. Clearly mark training IDs and documents.

Later support frozen real-plan snapshots with appropriate provenance and rights. CMS benefits/landscape files are useful inputs. Its Part D resources distinguish public data from richer files available through an order/terms process; they do not supply a turnkey nationwide provider-network service. [S14–S16]

### Benefit schema

Each benefit needs category, service, covered status, cost-sharing method, amount/percentage, unit, network tier, deductible applicability, limit, authorization/referral conditions, eligible population, exclusions, effective period, and source.

Include premium, medical deductible/MOOP, PCP/specialist, hospital, emergency/urgent care, ambulance, diagnostics, behavioral health, therapy, DME, skilled nursing, home health, drugs, and relevant supplemental benefits.

Separate medical limits, prescription spending rules, premiums, noncovered spending, and allowances. Shared supplemental wallets must not be counted multiple times. Conditional food-related benefits need explicit qualification; a diagnosis alone is not a universal entitlement. [S12]

### Generation pipeline

1. Author structured plan records and usage assumptions.
2. Validate geography, dates, units, arithmetic, and eligible populations.
3. Render cards, comparisons, details, and training summaries from one record.
4. Check every displayed numerical claim against its source.
5. Review plausibility and teaching tradeoffs.
6. Publish a frozen catalog version.

Models may draft explanatory prose for review. They cannot invent benefits or prices during a call. Use original template-driven document layouts.

### Useful tradeoffs

Lower premium versus higher recurring copays; larger allowance versus losing a specialist; covered medication with authorization; preferred versus standard pharmacy; broader out-of-network access with higher costs; and an existing plan that remains appropriate.

Some cases should allow several defensible recommendations. Evaluate reasoning against customer preferences rather than forcing one answer regardless of the conversation.

## 11. Evidence and simulated source pages

Provide original provider-office pages, carrier-directory examples, coverage/eligibility responses, and prescription references that learners can search and attach.

| Attribute | Purpose |
|---|---|
| Source type/ID | Distinguish directory, office, attestation, coverage response |
| Person/provider | Prevent evidence being applied to someone else |
| Plan/year/location | Preserve the context of the fact |
| Observation/effective dates | Distinguish collection time from validity |
| Content facts | Validate evidence, not just presence of a file |
| Status/confidence | Represent unavailable or contradictory information |
| Version/attachment event | Reconstruct what the reviewer actually saw |

Seed wrong-location, old-year, missing-page, unreadable, and wrong-opportunity defects. Network proof and contact proof are independent.

No live government lookup, real patient record, production carrier account, or actual fax is required. Avoid official logos or presentation that could make a mock report appear authentic.

## 12. Scenario engine, database, and event history

### Four views

1. Scenario truth: fixed facts, constraints, plans, outcomes.
2. Customer knowledge: known, forgotten, misunderstood, or conditionally revealed facts.
3. Learner records: actual entries, including mistakes.
4. Reviewer view: records/evidence accessible to that role.

Only the evaluator and authorized instructor compare all four. Hidden answers stay out of browser payloads and actor prompts.

### Entities

| Entity | Relationships |
|---|---|
| Tenant/membership | Roles, assignments, private content |
| Rule pack | Sources, dates, applicability, approvals |
| Organization profile | Packs, settings, templates |
| Scenario version | Dates, geography, truth, objectives, defects |
| Person/household/representative | Separate identities, contact, authority |
| Plan/benefit version | Product, geography, costs, rules, documents |
| Provider/location | Identity, specialty, contact, offices |
| Participation | Provider location, plan/network, dates, evidence |
| Drug/formulary/pharmacy | Coverage, restrictions, participation, pricing |
| Opportunity/application | Customer, plan, state, revision, predecessor |
| Evidence/note | Source facts, versions, saved text |
| Session/event | Participants, turns, actions, timing, revisions |
| Evaluation/review | Findings, confidence, evidence, overrides |

Log searches, selections, comparisons, document access, attachments, saves, revisions, disclosures, responses, participation changes, review requests, corrections, and outcomes.

Use server event timestamps and monotonic audio timing. Review uses saved records and a specific application revision; unsaved text is not magically visible. Model delayed synchronization intentionally, while treating accidental data loss as a product defect.

Pin scenario, catalog, rules, scripts, prompts/models, and rubric versions. Updating next year's content must not silently change an earlier session or score.

## 13. Conversation realism

Structured state controls identity, coverage, medications, eligibility, and plan facts. The model controls phrasing, hesitation, interruption, and tone. New hard facts require permitted state changes.

Use Medicare-relevant variables: trust, understanding, fatigue, available time, provider loyalty, affordability, willingness to change, ad expectations, and representative availability.

Objections respond to events: unexplained identifiers, doctor uncertainty, unexpected premium, rushed explanation, silence, or inconsistent claims. Repeating a rebuttal does not automatically increase receptivity. A reasonable refusal can remain a refusal.

Customers may remember another medication or misunderstand a question within authored reveal conditions. Do not make success depend on guessing undiscoverable facts.

Use diverse voices without treating age, disability, accent, or diagnosis as incapacity. Confusion scenarios teach clarification, support, authority checks, and appropriate escalation using observable facts.

Tag scripts as exact required text, validated variable text, semantic objective, or flexible dialogue. Original educational wording is not described as an approved carrier script.

## 14. Technical architecture and audio

### Modular architecture

Recommended components: React/TypeScript frontend; application API/rule engine; PostgreSQL database; private object storage; realtime voice worker; asynchronous document/evaluation worker.

The API owns data and permissions. Voice workers obtain role-permitted facts through constrained tools. Evaluators read finalized evidence. Model dialogue cannot directly change eligibility, submit an application, or access another tenant.

Start modular rather than creating many microservices. Host long-lived voice connections on suitable infrastructure; the static frontend can be deployed separately.

### Voice candidates

| Approach | Advantage | Key question |
|---|---|---|
| Managed voice-agent API | Fast prototype | Adequate tools, transcripts, and interruption control? |
| Framework plus STT/text model/TTS | Explicit text control and provider choice | Acceptable latency with real workflow tools? |
| Native audio model | Natural speech | Reliable facts, heard-content tracking, and resume? |
| Custom audio pipeline | Transport control | Is extra engineering justified? |

LiveKit documents turn handling/testing, Deepgram documents audio-buffer clearing, and Google documents realtime session management. These are candidates, not irrevocable dependencies. [S17–S19]

Benchmark identical cases: drug spelling, numeric IDs, interruptions, pauses, refusal, and connection recovery. Choose based on task quality and measured cost.

### Audio requirements

- Use supported microphone capture/echo-cancellation constraints; an AudioWorklet is not an echo canceller. [S20]
- Distinguish speech activity from completed conversational intent.
- Stop queued playback, cancel obsolete generation, discard stale chunks, and track what was actually heard.
- Align separate speaker tracks and transcripts where feasible.
- Confirm uncertain drug names and identifiers rather than silently inventing corrections.
- Resume without duplicating consent or submission.
- Measure holds, technical delays, silence, and overlap separately.
- Maintain a text harness for development and accessibility.

Proposed targets: median end-of-turn to audible response ≤1.2 seconds; p95 ≤2.5 seconds; p95 interruption stop ≤300 milliseconds on supported conditions; stable 45–60 minute sessions with reconnect. These are targets, not observed results.

## 15. Scoring and coaching

### Original educational rubric

This is a proposed product rubric, not a CMS-issued or employer scorecard.

| Dimension | Weight |
|---|---:|
| Discovery and listening | 15 |
| Eligibility and timing diligence | 15 |
| Provider/drug/pharmacy verification | 20 |
| Recommendation accuracy and tradeoffs | 20 |
| Documentation/application accuracy | 15 |
| Communication and customer choice | 10 |
| Completion and follow-up | 5 |
| Total | 100 |

Verification-role scoring is separate. A sales learner cannot lose points for work only the verifier performs. Handoff-enabled lessons explicitly define changes to applicable criteria.

### Findings and fairness

Define critical findings separately: for example, fabricated consent, material false benefit claims, or proceeding despite a known disqualifying fact. Each needs reviewed applicability, evidence, and severity.

Statuses: pass, fail, not applicable, not observed, review required. N/A items leave the denominator. Low-confidence transcription requires review, not automatic failure. Instructor overrides retain reasons and audit history.

Timers and minimum note length are organization/training criteria unless a source establishes otherwise. A private profile must not alter general-profile grades.

### Evaluation method

Deterministic checks cover fields, dates, evidence, arithmetic, state transitions, and permissions. Model-assisted checks cover conversational meaning, limited to recorded transcript/events.

Each finding includes criterion, authority, expected/observed behavior, timestamps/events, severity, confidence, and improvement. Generated speech that was interrupted before playback does not count as delivered.

Debriefs show outcome, critical issues, correct decisions, three useful improvements, replay evidence, and a targeted retry. Speaking speed, talk/listen ratio, and sentiment are optional signals, not universal pass/fail standards.

## 16. Initial scenario library

| Case | Objective | Optional agency variant |
|---|---|---|
| Advertised allowance expectation | Clarify conditions | Additional disclosure |
| Current coverage fits | Appropriate no-change decision | Required rationale field |
| Must-keep specialist | Exact location/network match | Directory evidence attachment |
| Conflicting directories | Investigate uncertainty | Reviewer approval |
| Wrong drug strength | Correct data/cost | Late-change ownership |
| Pharmacy cost difference | Compare participation | Mail-order education |
| HMO/PPO tradeoff | Explain access/cost | Additional PCP field |
| Insufficient election facts | Ask or defer | Eligibility review queue |
| Other coverage | Identify impact/seek guidance | Private escalation |
| Customer declines | Respect and record | Documentation review |
| Incomplete packet | Correct without erasing history | Timed verification |
| Disconnected application | Resume without invented consent | Callback routing |

Expand to SNPs, interpreter changes, representative authority, multiple beneficiaries, post-submission requests, HRA, and annual transitions after review.

Each case needs fixed facts, reveal conditions, reasonable discovery paths, applicable packs, acceptable outcomes, severity rules, and regression examples. Unreviewed complex rules remain exploratory rather than authoritative graded lessons.

## 17. Roadmap

| Phase | Timing estimate | Deliverables | Exit gate |
|---|---|---|---|
| 0 Public baseline | Weeks 1–2 | Source/rule register, original flow, schema, configuration boundaries | Reviewed scope and three cases |
| 1 Deterministic core | Weeks 3–4 | Catalog, states, rules, event log, text harness | Repeatable correct outcomes |
| 2 Workspaces | Weeks 5–7 | Research, profiles, notes, documents, application | Full case without admin shortcuts |
| 3 Voice/optional review | Weeks 8–10 | Customer voice, routing, interruption, resume, verifier | Consistent complete practice call |
| 4 Configuration/scoring | Weeks 11–13 | Settings UI, private packs, evaluation, replay | Two profiles with no leakage |
| 5 Pilot/hardening | Weeks 14–16 | Reviewed cases, learner pilot, testing, costs, runbook | Acceptance gates met |
| Expansion | After pilot | More products/states, real snapshots, integrations | Independently reviewed extensions |

Assumption: two experienced engineers plus part-time domain review, design, and QA. Not a commitment. Design configuration and permissions in phases 0–1; phase 4 supplies management UI.

### First vertical slice

One beneficiary, one current plan, three alternatives, provider/drug/pharmacy search, benefit documents, original CRM records, and a simulated application. Support a suitable enrollment, retaining coverage, and deferring for missing facts.

Next add one optional verifier and correctable documentation defect. Changing profile must change workflow requirements without changing plan benefits or customer facts.

### First ten working days

Select scope/dates/browser; build source register; design original journey; author three cases; define schemas; define general and sample private profiles; create synthetic catalog/evidence; implement text state transitions; review cases; benchmark voice and revise estimates.

## 18. Prioritized backlog

| ID | Priority | Work | Acceptance |
|---|---|---|---|
| CORE-01 | P0 | Versioned rule/scenario resolution | Historical replay stable |
| CORE-02 | P0 | Authority labels | Every finding explains origin |
| CFG-01 | P0 | Schema validation | Conflicting profiles cannot publish |
| CFG-02 | P0 | General/private separation | Baseline works without private packs |
| DATA-01 | P0 | Plan/document generation | Values agree across all surfaces |
| DATA-02 | P0 | Provider-location relationships | Wrong-location case identifiable |
| DATA-03 | P0 | Rx/pharmacy fixtures | Correct authored cost effects |
| CRM-01 | P0 | Customer/opportunity creation | Duplicates and households handled |
| CRM-02 | P0 | Evidence/version history | Review sees saved matching evidence |
| APP-01 | P0 | Lifecycle/idempotency | No duplicate submissions |
| VOICE-01 | P0 | Interruption/resume | Heard text and state remain accurate |
| REVIEW-01 | P1 | Optional verifier | Enabled/disabled paths both work |
| EVAL-01 | P0 | Evidence-linked scoring | Every penalty is supportable |
| SEC-01 | P0 | Role/tenant isolation | No hidden answers or cross-tenant data |
| ADMIN-01 | P1 | Publishing | Approved immutable versions |
| TEST-01 | P0 | Golden scenarios | Rules/data regressions detected |
| EXT-01 | P2 | Real-plan import | Rights, provenance, mapping validated |
| EXT-02 | P2 | External integrations | Authorized and separate from simulation |

P0 is essential for a dependable pilot; P1 adds breadth; P2 requires evidence of value.

## 19. Testing and pilot acceptance

### Test layers

Data: plan/year/geography joins, cost units, provider locations, drug strength, document consistency.

Rules: applicability, conflicts, conditional fields, eligibility fixtures, states, permissions.

End-to-end: customer creation, evidence saving, revision control, optional handoff, correction, and replay.

Conversation: interruption, silence, names/IDs, refusal, misunderstanding, new participants, disconnect, and instructions to ignore fixed facts. Use event/tool assertions and reviewer-labeled examples rather than relying solely on a model judge. [S21]

### Configuration-independence checks

- Same case under general/private profiles changes only intended operational findings.
- Disabling verification preserves complete baseline training.
- Extra PCP collection does not become a claimed federal PPO requirement.
- Fax proof enabled for one product does not leak into others.
- Removing private packs removes their text, metadata, links, and prompt access.
- Attempting to suppress an applicable mandatory finding fails publication.

### Proposed pilot gates

All released deterministic gold cases pass; no cross-person evidence/application mix-ups in scripted testing; no answer-key or cross-tenant exposure; each failure has evidence; unresolved policy and low-confidence transcription do not automatically fail learners; supported browser/audio/resume checks pass.

Target at least 95% reviewer agreement on critical findings in a reported pilot sample, investigating all disagreements. Report sample size and error categories; a small pilot does not establish universal reliability.

Measure coverage accuracy, appropriate no-change decisions, missing-data rates, unsupported promises, documentation quality, and instructor time. Enrollment conversion alone is insufficient.

## 20. Budget and staffing

Plan for a full-stack/workflow engineer, realtime/backend engineer, part-time Medicare reviewer, and part-time design/QA. Individuals may cover multiple roles, but software creation and subject-matter validation remain separate tasks.

Illustrative pilot estimate: 900–1,400 engineering hours plus 120–220 combined review/design/QA hours. At assumed engineering rates of $75–$150/hour, engineering alone is approximately $67,500–$210,000. These are project assumptions, not vendor quotes or researched labor rates. A one-case prototype is much smaller and should be the first funding gate.

Owner-building with AI can reduce cash expense, but not eliminate content validation, testing, and maintenance. Nationwide data, external-product replicas, and an unrestricted visual workflow builder are outside this estimate.

### Operating sensitivity

Monthly cost includes voice/model usage, evaluation, hosting minimums, recordings/documents, egress, and monitoring. Measure hold-time billing and avoid double-counting components included in managed services.

| Usage | Minutes/month | $0.05/min | $0.10/min | $0.20/min |
|---|---:|---:|---:|---:|
| 10 learners × 10 calls × 30 min | 3,000 | $150 | $300 | $600 |
| 50 learners × 12 calls × 30 min | 18,000 | $900 | $1,800 | $3,600 |
| 100 learners × 20 calls × 30 min | 60,000 | $3,000 | $6,000 | $12,000 |

These are variable-cost sensitivities, not quotes or guaranteed bounds. They exclude fixed hosting, evaluation, storage, and taxes. Benchmark completed and abandoned calls before setting a budget. No free credits or student promotions are assumed.

Funding gates: baseline/text case; functional screens/voice; configuration/scoring/pilot; then broader products and data only after measured learning value and cost.

## 21. Security, ownership, and maintenance

Use synthetic beneficiary data. Protect real employee accounts/recordings with scoped access, private storage, defined retention, and deletion. Keep secrets server-side and browser session authorization short-lived.

Tenant isolation covers database, files, queues, caches, indexes, model context, analytics, and exports. Privileged workers must enforce tenant boundaries too; database row-level security is only one component. [S22]

Use original assets and fictional samples. Public accessibility of third-party carrier content does not establish unrestricted redistribution rights. Non-proprietary foundations do not automatically mean every dependency, dataset, or private pack is open source. Choose the code license separately and track content/dependency rights.

Public distributions contain approved references, original writing/design, synthetic fixtures, and permitted dependencies. Private workplace imports remain separately controlled.

Maintain source owner, URL/version, effective dates, review date, and affected tests. Publish new plan-year/catalog versions and regression-test rule changes. Preserve historical sessions rather than silently rescoring them under new policy.

## 22. Principal risks

| Risk | Mitigation |
|---|---|
| Agency policy presented as federal rule | Authority labels and configuration isolation |
| Stale content | Effective-date resolution and review gate |
| Inconsistent dialogue/screens/documents | Shared canonical facts |
| Invented eligibility/benefits | Constrained tools and deterministic validation |
| Arbitrary verifier pushback | Findings from saved packet and active rules |
| Incentives favor pressure or unnecessary switches | Valid no-sale outcomes and critical reviews |
| Private-content leakage | Tenant boundaries and export checks |
| Configuration scope explodes | Bounded initial schema |
| Voice demo fails on real workflows | Long-session, identifier, and interruption testing |
| Nationwide data overwhelms project | Synthetic catalog first |

## 23. Definition of the first complete release

An instructor can assign a reviewed synthetic case. A learner can converse, research plans, create records, document evidence, reach an appropriate outcome, and receive evidence-linked coaching. An administrator can enable a separate review workflow without modifying application code or importing proprietary workplace content.

**The application runs in its general configuration from a fresh installation with every private pack absent.** That is the acceptance test for the non-proprietary starting point.

## Appendix A. Rule contract

| Field | Meaning |
|---|---|
| rule_id/version | Stable identity and immutable revision |
| authority_type | Regulatory, plan, organization, training |
| source_reference | Public citation or controlled private source |
| effective_from/to | Applicability interval |
| applicability | Product, geography, call type, role, stage |
| required_facts | Information needed for fair evaluation |
| evaluation | Deterministic predicate or reviewed semantic criterion |
| on_unknown | Ask, defer, or instructor review |
| workflow_effect | Warning, correction, stop, routing |
| grading_effect | Points, critical finding, informational, ungraded |
| review_status | Draft, approved, retired, disputed |
| test_cases | Positive, negative, unknown, boundary |

Keep workflow effects separate from grades. Repair may resolve the operational problem without erasing its occurrence; a harmless typo need not become a critical finding.

## Appendix B. Traceability examples

| Requirement | Origin | Implementation/test |
|---|---|---|
| Appropriate pre-enrollment review | Public requirement | Document open alone cannot prove the required discussion occurred |
| Exact provider location | Plan/scenario fact | Same name at a different office produces a different result |
| Voluntary choice | Public guidance/education | No fabricated agreement after refusal |
| Timed handoff | Optional policy | General profile receives no timing penalty |
| Conditional fax proof | Reviewed plan/organization rule | Disabled rule produces no missing-fax finding |
| Private note template | Organization | General tenant cannot read text or metadata |
| Original scoring | Product design | N/A items do not lower scores |
| Stable historical case | Product design | New-year release does not alter old session |

## Appendix C. Public sources

Reviewed for this proposal on November 7, 2026. Capture applicable versions during implementation. These references support the public foundation; architecture, schemas, rubrics, estimates, and sample scenarios are proposed product designs.

- **S1:** [42 CFR 422.2264 — Beneficiary contact](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2264).
- **S2:** [Medicare.gov marketing rules](https://www.medicare.gov/health-drug-plans/health-plans/your-coverage-options/plan-marketing-rules).
- **S3:** [42 CFR 422.2274 — Agent, broker, and TPMO requirements](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2274). Map applicable Part D counterparts separately when implementing that module.
- **S4:** [CMS Managed Care Eligibility and Enrollment](https://www.cms.gov/medicare/enrollment-renewal/managed-care-eligibility-enrollment).
- **S5:** [Medicare.gov Joining a plan](https://www.medicare.gov/basics/get-started-with-medicare/get-more-coverage/joining-a-plan).
- **S6:** [42 CFR 422.2267 — MA required materials/content](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2267).
- **S7:** [42 CFR 423.2267 — Part D required materials/content](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-423/subpart-V/section-423.2267).
- **S8:** [CMS 2027 Agent/Broker Training and Testing Guidelines](https://www.cms.gov/files/document/cy2027-agent-broker-training-testing-guidelines-pdf.pdf). Reconcile with applicable amended rules and dates.
- **S9:** [Medicare.gov plan-type comparison](https://www.medicare.gov/health-drug-plans/health-plans/your-coverage-options/compare).
- **S10:** [Medicare.gov PPOs](https://www.medicare.gov/health-drug-plans/health-plans/your-health-plan-options/PPO).
- **S11:** [CMS Special Needs Plans](https://www.cms.gov/medicare/enrollment-renewal/special-needs-plans).
- **S12:** [HHS Medicare Advantage and food-related supplemental benefits](https://odphp.health.gov/foodismedicine/federal-resource-hub/medicare-advantage-health-plans).
- **S13:** [CMS Marketing Models and Standard Documents](https://www.cms.gov/medicare/health-drug-plans/managed-care-marketing/models-standard-documents-educational-materials).
- **S14:** [CMS Benefits Data](https://www.cms.gov/data-research/statistics-trends-and-reports/medicare-advantagepart-d-contract-and-enrollment-data/benefits-data).
- **S15:** [CMS Prescription Drug Coverage / landscape data](https://www.cms.gov/medicare/coverage/prescription-drug-coverage).
- **S16:** [CMS Formulary, Pharmacy Network, and Pricing files](https://www.cms.gov/research-statistics-data-and-systems/files-for-order/nonidentifiabledatafiles/prescriptiondrugplanformularypharmacynetworkandpricinginformationfiles).
- **S17:** [LiveKit turn handling](https://docs.livekit.io/agents/logic/turns/).
- **S18:** [Deepgram audio buffer clearing](https://developers.deepgram.com/docs/tts-ws-clear).
- **S19:** [Google Live API session management](https://ai.google.dev/gemini-api/docs/live-api/session-management).
- **S20:** [MDN echoCancellation](https://developer.mozilla.org/en-US/docs/Web/API/MediaTrackConstraints/echoCancellation).
- **S21:** [LiveKit agent tests](https://docs.livekit.io/testing/unit-tests/).
- **S22:** [Supabase Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security).

No private source-file links, internal scripts, employer-specific scorecards, or proprietary screenshots are included in this specification. Authorized organization packs are separate optional configuration artifacts.

