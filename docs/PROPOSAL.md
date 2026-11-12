# Medicare Sales Training Simulator

## CMS-based product proposal, MVP definition, and roadmap

Version 2.1 • November 7, 2026

An original, vendor-neutral Medicare sales training application built on public CMS requirements. Agents practice realistic calls while they research synthetic plans, document needs and evidence, and prepare simulated applications. The product is independent of any employer, CRM, quoting platform or carrier, and its interface, content and workflows are original. This document names relevant public requirements. It does not certify the application or replace agent training, carrier certification or a regulatory review.

## Changes from v2.0

| # | Change |
|---|---|
| 1 | New §0 defines the MVP |
| 2 | Tenancy, private packs, publishing, config UI and verifier move to Part II |
| 3 | Server-hosted voice worker specified (LiveKit, Python agent); browser-direct is the fallback |
| 4 | Rules carry paragraph cites, verified-as-of dates and testable checks |
| 5 | CY2027 rule: no 48-hour SOA wait; disclaimer before any benefit discussion; recording rules updated |
| 6 | New CMS guidance authority type |
| 7 | Recording duty separated from the state-law caller notice |
| 8 | MVP pinned to AEP for plan year 2027; retiree-coverage case added |
| 9 | Part D out-of-pocket threshold and Prescription Payment Plan pinned by year |
| 10 | Judge cites `{utt_id, quote}`; the server verifies it |
| 11 | Canary leak test; answer key locked until the call ends |
| 12 | Actor-consistency QA |
| 13 | Judge eval set of labeled transcripts |
| 14 | One three-pane call screen |
| 15 | Gemini Live limits named as voice risks |
| 16 | Stated MVP acceptance test |
| 17 | v2.0 §2, §8 and §9 collapsed into Part II |

## 0. MVP

A learner takes one inbound AEP call from a synthetic beneficiary, researches fictional 2027 plans, records needs and evidence, reaches the right outcome (enroll, keep the current plan, or defer), completes a simulated application where appropriate, and gets an evidence-linked debrief, in text or voice, on one machine.

### Acceptance test

The MVP is done when one user finishes all three cases in text and in voice, every penalty links to the event or utterance behind it, and the grader agrees with that user on critical findings in a 10-call sample. Each disagreement gets investigated and logged.

### Call frame

| Field | MVP value |
|---|---|
| Call date | 2026-11-12, during the Annual Election Period |
| Plan year | 2027, coverage effective 2027-01-01 |
| Geography | One fictional county with fake ZIP codes |
| Learner role | Agent at a fictional agency representing 3 of the county's carriers, so the full TPMO disclaimer applies |

### In and out

| In the MVP | Later |
|---|---|
| 3 fictional carriers, 6 MAPD plans, about 12 providers, 12 drugs, 4 pharmacies | PDP and SNP products, larger catalog |
| 3 cases with seeded mood, voice and phrasing | 12-case library |
| Three-pane call screen, deterministic checks, verified-quote judge, debrief | Instructor tools, admin screens |
| Text actor; Gemini Live voice through a local LiveKit worker | Other providers, hosted deployment |
| SQLite, single user | Postgres, auth, tenants, private packs |

### Launch cases

| Case | Setup | Right outcome | Traps |
|---|---|---|---|
| A. Must-keep specialist | Her HMO drops her cardiologist's group for 2027 | Enroll where that cardiologist, at her office, is in network and her drugs work | Same doctor at another office; a drug behind prior authorization; the 2026 network |
| B. The ad promised money | A TV ad promised an allowance; his plan still fits | Keep the plan, unless the learner finds a real gain and states the losses | Conditional allowance, narrower network, "everyone gets $150" |
| C. Retiree coverage | Joining an MA plan may end his and his spouse's retiree drug coverage | Defer, explain the impact, refer him to the benefits administrator, follow up before December 7 | He pushes to "just sign me up" |

Keeping coverage and deferring count as correct outcomes.

## 1. Objective and principles

Train agents to make accurate, beneficiary-centered recommendations while doing the research and record-keeping a sales call requires. A kept plan or a stopped enrollment can be the right result, and shorter calls or more enrollments are not automatically better.

Five principles hold in every phase.

1. Every regulatory statement has a public source, effective period, scope and review status.
2. Customer answers, documents, search results, applications and grades come from one scenario version.
3. Agency procedures such as handoffs, fax proof, timers and note templates are optional policy, never presented as federal rules.
4. Every finding links to an observable action, record, document or utterance.
5. Content is original and synthetic, and the product runs without private workplace documents or production software.

## 2. Authority layers

Every rule carries an authority type. Layers compose by applicability, so an organization setting cannot waive an applicable regulation and a plan rule cannot be inferred from a plan-type label. Conflicting mandatory settings block publication.

| Authority | Example |
|---|---|
| Regulatory | 42 CFR 422.2274(c)(12) pre-enrollment topics |
| CMS guidance | The longer checklist in the 2027 Agent and Broker Training Guidelines |
| Plan | PCP rules for a specific plan |
| Organization | A recording-notice script, an extra evidence step |
| Training | Hints, pacing, educational scoring |

Missing a 422.2274(c)(12) topic before an enrollment is a regulatory finding. Missing an item from the guidelines' longer checklist is a training finding.

## 3. Regulatory foundation

`docs/rule-register.md` holds the MVP rule subset with cites, status and checks.

### 2027 changes

The CY2027 final rule (91 FR 17384, April 6, 2026, effective June 1, 2026) amended 42 CFR 422.2264, 422.2267 and 422.2274. [S23, S24]

| Topic | Before | Now |
|---|---|---|
| Scope of Appointment | 48-hour wait before a personal marketing appointment, with narrow exceptions | No wait. Scope is still agreed and recorded first, lasts 12 months, and other health products need their own scope [S1] |
| TPMO disclaimer | Within the first minute of a sales call, with a SHIP reference | Before any benefit discussion, no SHIP reference. A TPMO selling for every MA organization in the area uses a shorter form [S6] |
| Call recording | Named marketing, sales and enrollment calls | All marketing and sales calls, kept at least 6 years: audio for years 1–3, audio or a full transcript for years 4–6 [S3] |

CMS applied the marketing changes starting October 1, 2026. The 2027 training guidelines still quote the older recording wording, so the register tracks that mismatch. [S8]

### Subject areas

The v2.0 subject areas carry over: contact and scope [S1], accurate marketing and voluntary choice [S2], the needs discussion [S3], eligibility and election periods [S4, S5], the checklist [S6], consent [S4, S8], disclaimer and recording [S3, S6], HMO and PPO rules [S9, S10], SNP qualification [S11] and conditional supplemental benefits [S12]. No public requirement mandates a software vendor or a timed third-party handoff. TPMO data-sharing consent (422.2274(g)(4)) is out of MVP scope.

### Permissions, eligibility and dates

Contact permission, scope, recording notice, legal authority, data-sharing consent and enrollment consent are separate records, and one "yes" never fills them all. A helper, spouse and authorized representative are different roles. An election code is not proof of eligibility, and age or medication use never implies Medicaid or LIS.

Each session pins its call date, plan year and rule version. Waiting periods and annual limits are never hard-coded; the 2027 SOA change shows why. Training recordings follow a training-data policy, not production retention rules.

## 4. Four views of truth

1. Scenario truth: facts, plans, traps, acceptable outcomes.
2. Customer knowledge: what the customer knows, misremembers or reveals only when asked.
3. Learner records: what the learner entered, mistakes included.
4. Reviewer view: what a reviewer role can see.

Only the evaluator, and an instructor after the call, compares all four, and the code enforces it. Until the customer says something, the only customer data a learner endpoint returns is the CRM seed the agency already had. Canary strings planted in truth and traps fail a test if they show up in any learner response, and the answer key returns 403 until the call ends. An earlier prototype served its answer key mid-call; this test exists to catch that.

## 5. Learner workflow

1. Record scope and give required disclosures before discussing benefits.
2. Gather coverage, eligibility, doctors, prescriptions, pharmacy, costs and other coverage.
3. Research plans for the right year and county, verifying locations, restrictions and pharmacies.
4. Compare the current plan with alternatives, stating gains and losses.
5. Support the decision to enroll, keep, defer or stop.
6. If enrolling, review the Pre-Enrollment Checklist and document consent.
7. Save rationale, evidence and follow-up, then review the debrief.

Disconnection, call end, submission and review are separate events.

## 6. The call screen

| Pane | Contents |
|---|---|
| Left | Transcript, mic, hold, end, timer |
| Center | Plans, Providers, Drugs, Pharmacy, Compare, Documents, and the application |
| Right | Customer record: doctors, drugs, pharmacy, coverage, scope, notes, disposition |

"Pin to record" on a research result writes evidence with its plan, year and practice location. "Use as source" on a customer's transcript line fills a customer-reported field and is how consent gets marked. Normal use of the screen produces the evidence grading needs. A reference drawer separates standardized CMS text from original wording.

## 7. Plan research and synthetic data

Compare puts the current plan beside up to three alternatives with stated ranking criteria, and nothing is labeled "best" without reference to the customer's needs. Provider participation is provider × practice location × network × effective period, so the same name at another office is not a match, and an NPI does not prove participation. Medicare.gov says PPO members generally need no PCP and no referrals, so extra PCP collection is labeled organization policy. [S10]

Drug identity includes strength and form. Coverage shows apart from prior authorization, step therapy and quantity limits, and pharmacy status changes cost. The MVP uses authored cost fixtures and a per-case annual estimate labeled "training estimate." The Part D out-of-pocket threshold and the Prescription Payment Plan are year-pinned parameters.

Each plan has a Summary of Benefits, formulary, directory pages and a Pre-Enrollment Checklist, rendered from the same records as the screens and watermarked "Training document." Wrong-year or wrong-location documents appear only as deliberate defects. Evidence records its source, provider, plan, year, location and the version a reviewer saw.

Plans are structured records, validated for geography, dates and arithmetic, and a test checks every displayed number against its source. Models may draft prose but never invent benefits or prices. Real-plan snapshots from CMS files can come later with rights checks. [S14–S16] Cases teach tradeoffs, such as a bigger allowance against losing a specialist, and some allow more than one defensible answer.

## 8. Customer records and application

Records keep the distinctions grading relies on: mailing address versus residence, recollection versus verified fact, unknown drugs versus none, a helper versus an authorized representative, a scheduled follow-up versus a completed one. The default notes template covers reason for inquiry, coverage, priorities, timing facts, provider and drug findings, options, rationale, decision, evidence and next steps, with no minimum length.

MVP application states are draft, submitted and withdrawn. An idempotency key makes a double submit create one application. Checklist items and consent are separate timestamped records. Review states and post-submission changes arrive with the verifier module.

## 9. Scenario engine and event log

The event log is the spine. Searches, document opens, pins, saves, application actions and utterances are append-only events with server timestamps. Each utterance carries `utt_id`, speaker, `t_ms`, `heard_text` and an `interrupted` flag, and one utterance contract serves text and voice. Reviews read saved records only.

Sessions pin scenario, catalog, rule, prompt, model and rubric versions. Replay means the same seed gives the same persona rolls and prompt text, a stored log gives the same deterministic findings, and stored judge output is reused unless someone regrades. New content never changes an old session's score. Tenant and membership exist only as schema hooks in the MVP.

## 10. Conversation realism

Structured state owns identity, coverage, medications, eligibility and plan facts, while the model owns phrasing, hesitation and tone. Each knowledge item has a reveal rule (volunteer, when asked about a topic, only if asked specifically) and may carry a misconception to correct.

Reveal rules live in the customer prompt because Gemini Live accepts no mid-call system messages. Text mode uses the same prompt builder, so text tests exercise the actor that voice runs. A build-time leak guard rejects prompts containing plan codes, internal IDs or jargon the customer wouldn't use. An actor-consistency check flags any doctor, drug or dollar amount the customer states that the scenario didn't give it; that is content QA and never counts against the learner.

Objections respond to events such as a rushed explanation, and a reasonable refusal can stay a refusal. Success never depends on undiscoverable facts, and age or accent is never treated as incapacity.

## 11. Voice architecture

Voice runs through a server-hosted agent worker. The browser sends and receives media over WebRTC through LiveKit, self-hosted for local development with LiveKit Cloud as a later option. A Python worker joins the call, runs the customer on Gemini Live, executes tools in-process against the engine and writes utterances to the event log. API keys and the persona stay on the server. The model layer stays provider-agnostic (Gemini Live, OpenAI Realtime, or speech-to-text, a text model and text-to-speech). A browser-direct connection with server-minted, config-locked ephemeral tokens is the fallback.

| Concern | Browser to model API | Server worker |
|---|---|---|
| User's network | Browser-direct Gemini uses a plain WebSocket, so a lost packet stalls later audio (OpenAI's direct path uses WebRTC) | WebRTC with jitter buffering to the user; datacenter network to the model [S27] |
| Gemini limits: ~10-minute connections, GoAway warnings, 15-minute audio sessions without compression | Handled in every tab; an earlier prototype's client line reached 1,939 lines | Handled once on the server [S19] |
| Tab reload | The model session dies unless client resume code is flawless | The worker holds the session while the browser rejoins |
| Tools, recording, evidence | Routed through or reported by the browser | Next to the database, authoritative |
| Latency | One fewer hop | One more hop; a field test found transports about equal because the model dominated [S28] |
| Operations | Nothing to host | Workers to scale, drain and monitor; a crash drops its calls |

A worker doesn't lower the per-minute model bill, the largest cost. It adds platform or hosting cost (LiveKit Cloud includes 1,000 free agent minutes a month; self-hosted LiveKit has no platform fee) [S31], and saves money by making cascaded pipelines practical and controlling what audio is sent during pauses. OpenAI offers a sideband mode where the server attaches to a browser's WebRTC session [S25]; we found no Gemini equivalent. Google positions ephemeral tokens for client-to-server use [S26], and Pipecat is an alternative framework [S29].

When this was written, LiveKit's docs flagged one newer Gemini Live model as incompatible [S30], so the first build milestone (M1) included a half-day spike running Case A through LiveKit and the chosen model, measuring latency, barge-in and a forced reconnect. The spike has been run ([voice-spike.md](voice-spike.md)). The page no longer marks any model as incompatible and lists per-model limits instead. The spike provisionally keeps the server-hosted worker; if the worker fails the M4 acceptance gate in a way browser-direct wouldn't, the decision gets revisited.

The audio path uses supported echo-cancellation constraints and headsets [S20], requires voiced evidence before counting a turn, records only what was heard when interrupted, confirms uncertain drug names, and resumes without duplicating consent or submission. Unmeasured targets: median end-of-turn to audio at or under 1.2 seconds, p95 at or under 2.5 seconds, stable 45–60 minute sessions. Usable latency needs the paid Gemini tier.

## 12. Scoring and evidence

The 100-point rubric is an original educational design, not issued by CMS or any employer.

| Dimension | Weight |
|---|---:|
| Discovery and listening | 15 |
| Eligibility and timing diligence | 15 |
| Provider, drug and pharmacy verification | 20 |
| Recommendation accuracy and tradeoffs | 20 |
| Documentation and application accuracy | 15 |
| Communication and customer choice | 10 |
| Completion and follow-up | 5 |

Each criterion is deterministic, judged or hybrid, with an evidence policy of event, transcript or both. Deterministic checks read fields, dates, lookups, event order and state, for example "the must-keep practice was found in network on the outcome plan for 2027." The judge handles conversational meaning from the transcript and events. In a hybrid check the judge finds a claim in the learner's speech and code verifies it, such as a coverage claim with no earlier lookup.

The judge cites evidence as `{utt_id, quote}`. The server looks for each quote in the cited utterance and marks the criterion "review required" if it isn't there. Speech interrupted before playback doesn't count as delivered.

Statuses are pass, fail, not applicable, not observed, review required and ungraded; not-applicable and ungraded items leave the denominator. Missing judged items make a call "incomplete," never "fail." Critical findings are enrolling without consent, enrolling when the case excludes it, claiming unverified coverage, and submitting before the checklist review. A judge eval set runs on every grader change, and debriefs end with a targeted retry.

## 13. Testing

| Layer | Coverage |
|---|---|
| Data | Joins, units, locations, drug strengths, every displayed number against its source |
| Goldens | Scripted paths per case (right plan, wrong office, wrong year, prior-auth drug, no checklist, double submit, keep, defer, pressure) with exact expected findings |
| Leaks | Canary sweep of learner endpoints before, during and after the call; prompt leak check across seeds |
| Replay and judge | Same seed and log reproduce findings; judge outage yields "incomplete" |
| Live actor (opt-in) | Real text actor over several seeds, reporting early reveals and invented facts |
| Voice | Interruption, IDs, tab reload, forced reconnect, a 25-minute call with research pauses |

Model-judged results never stand alone as test evidence. [S21]

## 14. Security, rights and maintenance

Beneficiary data is synthetic, and secrets stay server-side, read from environment variables by name. Original assets and fictional samples only; public carrier content does not carry redistribution rights. Each source records its URL, version, effective dates and review date, new plan years ship as new catalog versions, and historical sessions are never silently rescored.

## 15. Risks

| Risk | Mitigation |
|---|---|
| Agency policy presented as federal rule | Authority labels |
| Rules change again | Verified-as-of dates; pinned rule versions; unverified rules labeled in debriefs |
| Screens, documents and dialogue disagree | One record per fact |
| AI customer invents or leaks facts | Lay-fact prompt, leak guard, consistency QA |
| Unfair penalties | Deterministic checks first; verified quotes; review-required and incomplete states |
| Incentives favor switching | Valid no-sale outcomes; critical findings for unsupported claims |
| Voice framework lags the Gemini model | Day-3 spike, pinned model, browser-direct fallback |
| Cost creep | Per-call token and minute telemetry |

# Part II. Later phases

## 16. Organization configuration

Organizations can add a private policy pack (evidence steps, templates, scripts, timers, handoffs, QA weights). Removing it must leave a complete general application. Packs have an owner, source rights, approval and tenant scope, stay drafts until reviewed, and never enter the public repository, prompts or exports. The schema stays bounded, with no visual workflow builder.

| Area | General default |
|---|---|
| Verification: none, same agent, simulated reviewer, instructor | No compulsory transfer |
| Timing targets and correction windows | Informational only |
| PCP and carrier provider IDs | Plan-based |
| Physician evidence and fax proof | No universal fax gate |
| Evidence freshness | Rule-specific |
| Notes, forms, correction authority | Original template, explicit permissions |
| QA weights and overrides | Original rubric |
| Optional activities such as an HRA | Off |

An administrator clones a profile, picks a regulatory base and packs, configures, validates, gets approval and publishes an immutable version. Every requirement answers "why is this required?" with its authority, and publication rejects contradictory fields, circular transitions or removal of a mandatory requirement.

## 17. Optional verification module

The verifier checks the saved packet, permitted evidence and customer responses. Rules produce findings and the model phrases them, without inventing requirements or reading the answer key. Participation, application state and review state are independent, and each rule names the one it changes. Benefit facts and beneficiary protections never vary by profile. Verifier scoring is separate, so a sales learner never loses points for the verifier's work.

## 18. Tenancy and isolation

Isolation covers the database, files, queues, caches, indexes, model context, analytics and exports, and privileged workers enforce it too; row-level security is only one layer. [S22] Tests confirm that a case under two profiles changes only intended findings, that disabling verification keeps full baseline training, and that removing a pack removes its text, metadata and prompt access.

## 19. Roadmap beyond the MVP

| Phase | Deliverables | Exit gate |
|---|---|---|
| Pilot | Instructors, overrides, 12 cases, hosting, auth | At least 95% reviewer agreement on critical findings in a reported sample |
| Configuration | Private packs, settings UI, publishing, verifier | Two profiles with no leakage |
| Products | Part D, C-SNP, D-SNP, more counties and years | Each module reviewed |
| Data | Real-plan snapshots, full Part D cost engine | Rights and provenance validated |

v2.0 estimated 900–1,400 engineering hours for a hired team; owner-building with AI lowers cash cost but not review or testing. Variable cost is mostly voice minutes, from $150 a month (3,000 minutes at $0.05) to $12,000 (60,000 minutes at $0.20). The first complete release runs from a fresh install with every private pack absent.

## Appendix A. Rule contract

| Field | Meaning |
|---|---|
| rule_id, version | Stable identity and immutable revision |
| authority | regulatory, cms_guidance, plan, organization, training |
| citation, source_url | Public cite or controlled private source |
| status | unverified, approved, retired, disputed |
| verified_as_of | Date the cite was last checked against its source |
| effective_from, effective_to | Applicability interval |
| applies_when | Product, geography, call type, role, stage, outcome |
| required_facts | Information needed for fair evaluation |
| evaluation | Deterministic predicate, judged criterion or hybrid |
| evidence_policy | event, transcript or both |
| on_unknown | Ask, defer or instructor review |
| workflow_effect | Warning, correction, stop, routing |
| grading_effect | Points, critical finding, informational, ungraded |
| test_cases | Positive, negative, unknown, boundary |

Workflow effects stay separate from grades, so a repair can fix a problem without erasing that it happened.

## Appendix B. Sources

S1 through S22 were reviewed for v2.0 on November 7, 2026. For v2.1 the eCFR sections were re-read through the eCFR API renderer, where Title 42 is current as of 2026-11-05. Architecture, schemas, rubrics, estimates and scenarios are product designs, not sourced claims.

- S1: [42 CFR 422.2264, Beneficiary contact](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2264). Current as of 2026-11-05.
- S2: [Medicare.gov marketing rules](https://www.medicare.gov/health-drug-plans/health-plans/your-coverage-options/plan-marketing-rules).
- S3: [42 CFR 422.2274, Agent, broker, and TPMO requirements](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2274). Current as of 2026-11-05. Map Part D counterparts separately.
- S4: [CMS Managed Care Eligibility and Enrollment](https://www.cms.gov/medicare/enrollment-renewal/managed-care-eligibility-enrollment).
- S5: [Medicare.gov Joining a plan](https://www.medicare.gov/basics/get-started-with-medicare/get-more-coverage/joining-a-plan).
- S6: [42 CFR 422.2267, MA required materials and content](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2267). Current as of 2026-11-05.
- S7: [42 CFR 423.2267, Part D required materials and content](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-423/subpart-V/section-423.2267).
- S8: [CMS 2027 Agent and Broker Training and Testing Guidelines](https://www.cms.gov/files/document/cy2027-agent-broker-training-testing-guidelines-pdf.pdf). Its recording wording predates the amended regulation.
- S9: [Medicare.gov plan-type comparison](https://www.medicare.gov/health-drug-plans/health-plans/your-coverage-options/compare).
- S10: [Medicare.gov PPOs](https://www.medicare.gov/health-drug-plans/health-plans/your-health-plan-options/PPO).
- S11: [CMS Special Needs Plans](https://www.cms.gov/medicare/enrollment-renewal/special-needs-plans).
- S12: [HHS Medicare Advantage and food-related supplemental benefits](https://odphp.health.gov/foodismedicine/federal-resource-hub/medicare-advantage-health-plans).
- S13: [CMS Marketing Models and Standard Documents](https://www.cms.gov/medicare/health-drug-plans/managed-care-marketing/models-standard-documents-educational-materials).
- S14: [CMS Benefits Data](https://www.cms.gov/data-research/statistics-trends-and-reports/medicare-advantagepart-d-contract-and-enrollment-data/benefits-data).
- S15: [CMS Prescription Drug Coverage and landscape data](https://www.cms.gov/medicare/coverage/prescription-drug-coverage).
- S16: [CMS Formulary, Pharmacy Network, and Pricing files](https://www.cms.gov/research-statistics-data-and-systems/files-for-order/nonidentifiabledatafiles/prescriptiondrugplanformularypharmacynetworkandpricinginformationfiles).
- S17: [LiveKit turn handling](https://docs.livekit.io/agents/logic/turns/).
- S18: [Deepgram audio buffer clearing](https://developers.deepgram.com/docs/tts-ws-clear).
- S19: [Google Live API session management](https://ai.google.dev/gemini-api/docs/live-api/session-management).
- S20: [MDN echoCancellation](https://developer.mozilla.org/en-US/docs/Web/API/MediaTrackConstraints/echoCancellation).
- S21: [LiveKit agent tests](https://docs.livekit.io/testing/unit-tests/).
- S22: [Supabase Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security).
- S23: [CY2027 Medicare Advantage and Part D final rule, 91 FR 17384 (April 6, 2026), FR Doc. 2026-06600](https://www.govinfo.gov/content/pkg/FR-2026-04-06/html/2026-06600.htm).
- S24: [CMS fact sheet, Contract Year 2027 Medicare Advantage and Part D final rule](https://www.cms.gov/newsroom/fact-sheets/contract-year-2027-medicare-advantage-part-d-final-rule).
- S25: [OpenAI Realtime webhooks and server-side controls](https://developers.openai.com/api/docs/guides/realtime-server-controls).
- S26: [Gemini API ephemeral tokens](https://ai.google.dev/gemini-api/docs/ephemeral-tokens).
- S27: [LiveKit, Why WebRTC beats WebSockets for voice AI agents](https://livekit.com/blog/why-webrtc-beats-websockets-for-voice-ai-agents). Vendor source.
- S28: [DEV Community, WebSocket versus WebRTC audio pipeline test](https://dev.to/nick_lackman/i-tested-our-websocket-audio-pipeline-with-webrtc-heres-why-i-switched-it-back-3g1j). A single field report.
- S29: [Pipecat, Choosing a transport](https://docs.pipecat.ai/client/concepts/choosing-a-transport).
- S30: [LiveKit Gemini Live plugin](https://docs.livekit.io/agents/models/realtime/plugins/gemini/).
- S31: [LiveKit pricing](https://livekit.io/pricing) and [LiveKit Cloud pricing docs](https://docs.livekit.io/cloud/pricing/).

This document includes no private source-file links, internal scripts, employer scorecards or proprietary screenshots.
