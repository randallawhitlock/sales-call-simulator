# MVP rule register

Register version 1 • verified 2026-11-07

These are the rules the MVP grades against. Each one has an authority type, an exact cite, a status and the check that implements it. Findings from a rule with status `unverified` carry that label in the debrief.

## How the sources were read

eCFR's normal web pages redirected to a bot check, so Title 42 text was read through the eCFR API renderer, which serves the same text:

`https://www.ecfr.gov/api/renderer/v1/content/enhanced/current/title-42?part=422&section=<section>`

Title 42 there is current as of 2026-11-05. Sections 422.2264, 422.2267 and 422.2274 were last amended by the CY2027 final rule (91 FR 17384, April 6, 2026, effective June 1, 2026). For 422.2264, the 2026-05-15 and current versions were compared to confirm what changed. CMS applied the marketing changes to CY2027 marketing beginning October 1, 2026.

## Call frame the register assumes

An inbound call on 2026-11-12, during the Annual Election Period, about plan year 2027 coverage. The learner is an agent at a fictional TPMO that represents 3 of the county's MA organizations, so the full TPMO disclaimer applies.

## Status values

| Status | Meaning |
|---|---|
| approved | Cite and requirement confirmed against the primary source on the date shown |
| unverified | In scope, but the exact paragraph or cite still needs confirming |

## Summary

| ID | Requirement | Cite | Authority | Status | Grading |
|---|---|---|---|---|---|
| R-SOA | Scope of Appointment before a personal marketing appointment; no waiting period | 42 CFR 422.2264(c)(3) | Regulatory | approved | High weight |
| R-DISC | TPMO disclaimer before any benefit discussion | 42 CFR 422.2267(e)(41); 422.2274(g)(2)(iv) | Regulatory | approved | High weight |
| R-NEEDS | Seven topics fully discussed before enrollment | 42 CFR 422.2274(c)(12) | Regulatory when enrolling, training otherwise | approved | Per-topic criteria |
| R-PECL | Pre-Enrollment Checklist reviewed before a phone enrollment completes | 42 CFR 422.2267(e)(4) | Regulatory | approved | Critical if skipped |
| R-SB | Summary of Benefits for the selected plan | 42 CFR 422.2267(e), paragraph not confirmed | Regulatory | unverified | Standard |
| R-CONSENT | Enroll only on a documented, uncoerced yes | Enrollment guidance, cite not confirmed | Regulatory | unverified | Critical |
| G-PREENR | Longer pre-enrollment checklist | CY2027 Agent and Broker Training and Testing Guidelines, §4 | CMS guidance | approved | Training criteria |
| P-PCP | PCP selection follows plan rules | Plan record; Medicare.gov PPO page | Plan | approved | Standard |
| O-RECNOTE | Tell the caller the call is recorded | State law varies | Organization | approved as org policy | Low weight |

## Rules

### R-SOA: Scope of Appointment

| Field | Value |
|---|---|
| Requirement | The scope of the appointment is agreed and recorded before a personal marketing appointment. It must be in writing for in-person appointments ((c)(3)(i)). A scope is valid for 12 months, other health products need a separate scope, and non-health products such as annuities are barred ((c)(3)(iii)(A) through (C)). |
| What changed for 2027 | The 48-hour wait between scope and appointment is gone. It was in the 2026-05-15 text, with exceptions for the last four days of a valid election period and beneficiary-initiated walk-ins. The current text has no waiting period. The 12-hour gap between an educational event and a marketing event is also gone, and scopes can now be collected at educational events. |
| Cite | 42 CFR 422.2264(c)(3) |
| Authority | Regulatory |
| Status | approved, verified 2026-11-07 |
| Source | [eCFR 422.2264](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2264) |
| Check | Deterministic `scope_before_plan_discussion`: a `scope_recorded` event whose products include Medicare Advantage exists before the first lookup or document open on any plan other than the current one. The judge confirms the learner asked for the scope instead of assuming it. |
| Grading | High weight, not critical. No penalty depends on the time between scope and appointment. |

### R-DISC: TPMO disclaimer

| Field | Value |
|---|---|
| Requirement | A TPMO selling for more than one MA organization gives the disclaimer before any benefit discussion. Current wording: "We do not offer every plan available in your area. Currently we represent [#] organizations which offer [#] products in your area. Please contact Medicare.gov or 1-800-MEDICARE to get information on all of your options." A TPMO that sells for every MA organization in the area drops the first sentence and ends with "You can always contact Medicare.gov or 1-800-MEDICARE for help with plan choices." |
| What changed for 2027 | The SHIP reference was removed, and the timing moved from "within the first minute of a sales call" to "prior to the discussion of any benefits." The disclaimer must also be conveyed in email and chat, shown prominently on TPMO websites and included in marketing materials, TV ads among them. Those channels are outside the call simulation. |
| Cite | 42 CFR 422.2267(e)(41) defines it; 422.2274(g)(2)(iv) requires it |
| Authority | Regulatory |
| Status | approved, verified 2026-11-07 |
| Source | [eCFR 422.2267](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2267), [eCFR 422.2274](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2274) |
| Check | Deterministic `disclaimer_before_benefits`: element match on heard learner speech for the three elements (not every plan; represents [#] organizations offering [#] products; Medicare.gov or 1-800-MEDICARE), with the numbers taken from the call frame, which startup validation ties to the catalog. All elements must be heard before the learner first discusses benefits (premiums, copays, deductibles, dental, allowances, "extra benefits" and similar phrases; agency and office names excluded). Order is judged by position inside a line as well as between lines, so benefits followed by the disclaimer in one utterance fails. A bare "benefit" ("the main benefit of switching") is too weak to fail on: if it comes first, the result is `ungraded` and the judge rules on it. Every failure of this check is a wording match, so if the judge reads the same lines as a complete, timely disclaimer and its quotes verify, the item is held for review. The judge never turns a failed match into a pass on its own. |
| Grading | High weight |

### R-NEEDS: Pre-enrollment needs discussion

| Field | Value |
|---|---|
| Requirement | Prior to an enrollment, the agent fully discusses: (1) whether current primary care doctors and specialists are in network; (2) whether the current pharmacy is in network; (3) drug coverage and costs, including whether current prescriptions are covered; (4) costs of health care services; (5) premiums; (6) benefits; (7) specific health care needs. |
| Scope note | LIS, Medicare Savings Programs, Medicaid and Medigap are not in this regulation. A keyword search found no "Medigap," "Medicare Savings" or "Extra Help." "LIS" and "Medicaid" appear only in the commission chargeback exceptions at (d)(5)(iii)(B)(6). CMS's longer checklist is guidance; see G-PREENR. |
| Cite | 42 CFR 422.2274(c)(12) |
| Authority | Regulatory when the call ends in enrollment; a training objective otherwise |
| Status | approved, verified 2026-11-07 |
| Source | [eCFR 422.2274](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2274) |
| Check | The judge rates each topic and cites utterances. Deterministic lookups back the first three topics: `provider_verified_at_location`, `pharmacy_checked`, `drugs_checked`. |
| Grading | One criterion per topic |

### R-PECL: Pre-Enrollment Checklist

| Field | Value |
|---|---|
| Requirement | The standardized Pre-Enrollment Checklist is provided with the enrollment form. For telephonic enrollments, its contents are reviewed with the beneficiary before the enrollment is completed. |
| Cite | 42 CFR 422.2267(e)(4) |
| Authority | Regulatory |
| Status | approved, verified 2026-11-07 |
| Source | [eCFR 422.2267](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2267) |
| Check | Deterministic `pecl_complete_before_submit`: every item on the submitted plan's checklist is marked before the `application_submitted` event, and after the last change to the application. Every submission is audited, including ones later withdrawn. The judge confirms the contents were discussed, since opening the document proves nothing about the conversation. Both must pass. |
| Grading | Critical if items were not marked, or if a verified quote shows the agent skipping the review. A review the judge simply did not see earns no points but is not a critical finding, since nothing can be quoted for an absence. |

### R-SB: Summary of Benefits

| Field | Value |
|---|---|
| Requirement | The Summary of Benefits for the selected plan and plan year is provided or accessed, tracked apart from the checklist review. |
| Cite | 42 CFR 422.2267(e); the exact paragraph has not been confirmed |
| Authority | Regulatory |
| Status | unverified |
| Source | [eCFR 422.2267](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2267) |
| Check | Deterministic `sob_for_submitted_plan`: a document-access event for the submitted plan's 2027 Summary of Benefits exists before submission. Every submission is audited, including ones later withdrawn. |
| Grading | Standard |

### R-CONSENT: Enrollment consent

| Field | Value |
|---|---|
| Requirement | An enrollment goes forward only on the beneficiary's documented, uncoerced agreement. |
| Cite | Not confirmed. Candidates to check: CMS MA enrollment and disenrollment guidance, and the enrollment section of the CY2027 training guidelines. |
| Authority | Regulatory |
| Status | unverified |
| Source | [CMS Managed Care Eligibility and Enrollment](https://www.cms.gov/medicare/enrollment-renewal/managed-care-eligibility-enrollment), [CY2027 Agent and Broker Training and Testing Guidelines](https://www.cms.gov/files/document/cy2027-agent-broker-training-testing-guidelines-pdf.pdf) |
| Check | Deterministic `consent_before_submit`: the consent mark points to a customer "yes" utterance that was heard (not interrupted), comes after the last checklist item and before submission, and does not follow an unretracted refusal. The yes must be about enrolling: either the customer's line names it ("sign me up"), or it follows a learner sentence that asks leave to enroll or submit, within the last three learner lines, with no customer objection in between and no other learner question directly before the yes. A yes that fails only this test is `ungraded` for the judge, not failed. The application must not change after the yes. Clear objections ("wait", "cancel", "don't submit", "not yet") fail; everyday negatives ("no problem", "I don't have any questions") don't; any other stray "no" or "not" makes the result `ungraded` for the judge. Every submission is audited, including ones later withdrawn, so a clean resubmission doesn't hide a faulty first one. The judge rules on an `ungraded` result with a verified quote of the customer's line. Where the check failed on wording alone (not a clear yes, or an objection after it) and the judge reads the line as a clear yes, the item is held for review. Failures that rest on events (no consent marked, speech cut off, the yes before the checklist or after submission, the application changed) are not the judge's to dispute. |
| Grading | Critical if consent is missing or fabricated on an enrollment |

### G-PREENR: Longer pre-enrollment checklist

| Field | Value |
|---|---|
| Requirement | Section 4 of the 2027 guidelines lists further topics: preferred hospital, DME and therapy, dental, vision and hearing, the Part B premium, out-of-network and foreign coverage, the effect on current coverage (Medigap or retiree coverage, for example), plan-type requirements, and the right to cancel. |
| Cite | CY2027 Agent and Broker Training and Testing Guidelines, §4 (PDF metadata dated May 27, 2026) |
| Authority | CMS guidance |
| Status | approved, verified 2026-11-07 |
| Source | [CY2027 Agent and Broker Training and Testing Guidelines](https://www.cms.gov/files/document/cy2027-agent-broker-training-testing-guidelines-pdf.pdf) |
| Check | The judge rates each item; items irrelevant to the case are N/A. The effect on current coverage is central to Case C. |
| Grading | Training criteria, never critical |

### P-PCP: PCP selection

| Field | Value |
|---|---|
| Requirement | PCP selection follows the selected plan's rules. Catalog HMO plans require a PCP. Medicare.gov's PPO page answers "No" to needing a primary care doctor and "No" to needing specialist referrals. |
| Cite | Plan record in the catalog; Medicare.gov PPO page (undated) |
| Authority | Plan |
| Status | approved, verified 2026-11-07 |
| Source | [Medicare.gov PPOs](https://www.medicare.gov/health-drug-plans/health-plans/your-health-plan-options/PPO) |
| Check | Application validator: a PCP is required exactly when the plan record says so. Collecting a PCP on a PPO is allowed only as labeled organization policy. |
| Grading | Standard |

### O-RECNOTE: Recording notice

| Field | Value |
|---|---|
| Requirement | Tell the caller the call is recorded. The federal duty is to record the call (422.2274(g)(2)(ii)). Notice to, or consent from, the caller comes from state law, which varies. The MVP treats the notice as organization policy, on by default. |
| Cite | State law varies; not a federal requirement in this form |
| Authority | Organization |
| Status | approved as organization policy |
| Source | [eCFR 422.2274](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2274) for the federal recording duty |
| Check | Deterministic `recording_notice_given`: a learner utterance among the first three mentions that the call is recorded |
| Grading | Low weight, labeled organization policy in the debrief |

## Out of MVP

| Topic | Cite | Why it's out |
|---|---|---|
| TPMO data-sharing consent | 42 CFR 422.2274(g)(4) | Prior express written consent, given separately for each receiving entity, before a TPMO shares a beneficiary's data with another TPMO; in effect since October 1, 2024. No MVP case transfers data between TPMOs |
| Subcontract disclosure, disciplinary reporting | 422.2274(g)(2)(i), (g)(2)(iii) | Organization duties, not call behavior |
| Lead-generation and call-transfer disclosures | 422.2274(g)(3) | No MVP case involves a lead or transfer |
| Call recording and retention | 422.2274(g)(2)(ii) | All marketing and sales calls, including the audio of web-based calls, recorded and kept at least 6 years (audio for years 1–3, audio or a complete transcript for years 4–6). This is the TPMO's operational duty, so it is taught in scenarios but not graded as learner behavior. Training recordings follow a separate training-data policy |
| Agent compensation | 422.2274 | Not learner behavior |

## Open items

| # | Item | Notes |
|---|---|---|
| 1 | R-SB paragraph | Find the paragraph in 422.2267(e) that covers the Summary of Benefits, then move R-SB to approved |
| 2 | R-CONSENT cite | Confirm the enrollment-consent source, then move R-CONSENT to approved |
| 3 | Recording wording mismatch | 422.2274(g)(2)(ii) now says "all marketing and sales calls." The 2027 training guidelines still quote the older "record all marketing, sales, and enrollment calls." The register follows the regulation and keeps this open |
| 4 | Court ruling not reflected in eCFR | A Texas federal court stayed the CY2025 rule's (89 FR 30829, April 23, 2024) payment-cap and contract-terms provisions on July 3, 2024, vacated them on August 18, 2025, and upheld the (g)(4) consent rule. eCFR still prints (c)(13) and the administrative-payment language, and no CMS document removing them turned up. Medium confidence, from secondary sources only (Medicare Advocacy, Fierce Healthcare); check the court docket. Compensation is out of MVP scope, so nothing graded depends on it |
| 5 | SOA retention period | 422.2264 states none; other sections were not checked |
| 6 | Unreviewed amendments | 89 FR 63827 (August 2024) and the CY2026 rule (90 FR 15911), both of which amended 422.2267, have not been reviewed |

## Sources

- [eCFR 42 CFR 422.2264](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2264), current as of 2026-11-05
- [eCFR 42 CFR 422.2267](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2267), current as of 2026-11-05
- [eCFR 42 CFR 422.2274](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-422/subpart-V/section-422.2274), current as of 2026-11-05
- [CY2027 MA and Part D final rule, 91 FR 17384, FR Doc. 2026-06600](https://www.govinfo.gov/content/pkg/FR-2026-04-06/html/2026-06600.htm)
- [CMS fact sheet, CY2027 MA and Part D final rule](https://www.cms.gov/newsroom/fact-sheets/contract-year-2027-medicare-advantage-part-d-final-rule)
- [CY2027 Agent and Broker Training and Testing Guidelines](https://www.cms.gov/files/document/cy2027-agent-broker-training-testing-guidelines-pdf.pdf)
- [Medicare.gov PPOs](https://www.medicare.gov/health-drug-plans/health-plans/your-health-plan-options/PPO)
- [CMS Managed Care Eligibility and Enrollment](https://www.cms.gov/medicare/enrollment-renewal/managed-care-eligibility-enrollment)
