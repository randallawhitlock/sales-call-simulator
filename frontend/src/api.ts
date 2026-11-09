// Typed client for the learner API. Shapes mirror the backend's pydantic models; money comes
// back as decimal strings and is shown as-is.

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} }
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' }
    init.body = JSON.stringify(body)
  }
  const res = await fetch(`/api${path}`, init)
  const text = await res.text()
  const data: unknown = text ? JSON.parse(text) : null
  if (!res.ok) {
    throw new ApiError(res.status, describeError(data) ?? `HTTP ${res.status}`)
  }
  return data as T
}

function describeError(data: unknown): string | null {
  if (typeof data !== 'object' || data === null) return null
  const detail = (data as Record<string, unknown>)['detail']
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === 'object' && d !== null ? String((d as { msg?: unknown }).msg) : ''))
      .filter(Boolean)
      .join('; ')
  }
  return null
}

export const get = <T>(path: string, params?: Record<string, string | number>): Promise<T> => {
  const query = params
    ? '?' + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString()
    : ''
  return request<T>('GET', path + query)
}
export const post = <T>(path: string, body?: unknown): Promise<T> => request<T>('POST', path, body)
export const put = <T>(path: string, body: unknown): Promise<T> => request<T>('PUT', path, body)

export function clientId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID().replaceAll('-', '').slice(0, 20)}`
}

// Reference

export type Authority = 'regulatory' | 'cms_guidance' | 'plan' | 'organization' | 'training'

export interface StandardText {
  rule_id: string
  title: string
  text: string
  citation: string
  source_urls: string[]
  verified_as_of: string | null
}

export interface RuleReference {
  rule_id: string
  title: string
  requirement: string
  authority: Authority
  citation: string
  source_urls: string[]
  status: 'approved' | 'unverified' | 'retired' | 'disputed'
  verified_as_of: string | null
}

export interface Reference {
  ruleset_version: string
  verified_as_of: string
  standard: StandardText[]
  rules: RuleReference[]
}

// Sessions

export interface Health {
  status: string
  version: string
  actor: string
  catalog_version: string
}

export interface ScenarioCard {
  scenario_id: string
  learner_title: string
}

export interface Frame {
  call_date: string
  plan_year: number
  coverage_effective: string
  agency_name: string
  learner_role: string
  county_name: string
  carriers_represented: number
  products_offered: number
}

export interface CrmSeed {
  customer_name: string
  phone: string
  zip: string
  lead_source: string
  notes: string | null
}

export type SessionStatus = 'created' | 'active' | 'ended'

export interface Session {
  session_id: string
  scenario_id: string
  learner_title: string
  status: SessionStatus
  frame: Frame
  crm_seed: CrmSeed
  created_at: string
  started_at: string | null
  ended_at: string | null
  /** Live state, so a reloaded call screen shows hold and a reply in flight correctly. */
  on_hold: boolean
  turn_pending: boolean
}

export interface Utterance {
  utt_id: string
  speaker: 'learner' | 'customer'
  channel: 'text' | 'voice'
  text: string
  heard_text: string
  t_ms: number
}

export interface TurnOut {
  learner: Utterance
  customer: Utterance | null
}

export interface LogEvent {
  seq: number
  type: string
  t_ms: number
  utt_id: string | null
  payload: Record<string, unknown>
}

export interface Logged<T> {
  event_seq: number
  result: T
}

// Research

export interface PlanSummary {
  plan_id: string
  name: string
  carrier_name: string
  plan_type: string
  plan_year: number
  monthly_premium: string
  medical_deductible: string
  drug_deductible: string
  moop_in_network: string
  moop_combined: string | null
  pcp_required: boolean
  referrals_required: boolean
}

export interface Benefit {
  category: string
  label: string
  in_network: string
  out_of_network: string | null
  prior_auth: boolean
  referral_required: boolean
}

export interface PlanDetail extends PlanSummary {
  benefits: Benefit[]
}

export interface Location {
  location_id: string
  name: string
  address: string
  city: string
  zip: string
}

export interface Practice {
  practice_id: string
  group_name: string
  location: Location
}

export interface ProviderResult {
  provider_id: string
  name: string
  specialty: string
  credentials: string
  practices: Practice[]
}

export interface NetworkStatus {
  practice_id: string
  provider_name: string
  location: Location
  plan_id: string
  plan_name: string
  year: number
  in_network: boolean
  accepting_new_patients: boolean | null
}

export interface Drug {
  drug_id: string
  name: string
  strength: string
  form: string
  generic: boolean
}

export interface FormularyResult {
  plan_id: string
  plan_name: string
  drug: Drug
  covered: boolean
  tier: number | null
  prior_auth: boolean
  step_therapy: boolean
  quantity_limit: string | null
  deductible_applies: boolean
  copays: Record<string, string> | null
}

export interface Pharmacy {
  pharmacy_id: string
  name: string
  mail_order: boolean
  location: Location
}

export interface PharmacyNetwork {
  plan_id: string
  plan_name: string
  pharmacy: Pharmacy
  in_network: boolean
  preferred: boolean
  channel: string | null
}

export interface CompareLine {
  drug: Drug
  covered: boolean
  tier: number | null
  prior_auth: boolean
  step_therapy: boolean
  quantity_limit: string | null
  copay_per_fill: string | null
  fills_per_year: number
  annual_cost: string | null
  note: string | null
}

export interface ComparePlan {
  plan: PlanSummary
  pharmacy_in_network: boolean
  pharmacy_preferred: boolean
  annual_premium: string
  annual_drug_cost: string
  drug_deductible_applied: string
  total: string
  estimate_complete: boolean
  lines: CompareLine[]
  warnings: string[]
}

export interface CompareResult {
  label: string
  ranking: string
  method: string
  plans: ComparePlan[]
}

export interface DocumentSummary {
  document_id: string
  kind: 'summary_of_benefits' | 'pre_enrollment_checklist' | string
  title: string
  plan_id: string | null
  plan_year: number
}

export interface DocumentView extends DocumentSummary {
  watermark: string
  body_markdown: string
  checklist_items: string[]
}

// Records

export interface DoctorEntry {
  name: string
  role: 'pcp' | 'specialist' | 'other'
  practice_id: string | null
  must_keep: boolean
  source_utt_id: string | null
}

export interface DrugEntry {
  drug_id: string | null
  name_reported: string | null
  days_supply: 30 | 90
  source_utt_id: string | null
}

export interface CustomerRecord {
  doctors: { doctors: DoctorEntry[] } | null
  drugs: { status: 'listed' | 'none' | 'unknown'; drugs: DrugEntry[] } | null
  pharmacy: { pharmacy_id: string | null; name_reported: string | null; source_utt_id: string | null } | null
  coverage: {
    current_plan_id: string | null
    original_medicare: boolean | null
    medicare_parts: ('a' | 'b')[]
    medicaid: 'yes' | 'no' | 'unknown'
    other_coverage: { kind: string }[]
    source_utt_id: string | null
  } | null
  contact: {
    residence_zip: string | null
    mailing_same_as_residence: boolean
    mailing_zip: string | null
    helper_name: string | null
    authorized_representative: string | null
    source_utt_id: string | null
  } | null
  notes: Record<string, string> | null
  disposition: { outcome: string; plan_id: string | null; rationale: string } | null
  saved_seq: Record<string, number>
}

export interface ApplicationFields {
  plan_id: string
  applicant_name: string
  residence_zip: string
  mailing_zip: string | null
  pcp_practice_id: string | null
  effective_date: string
}

export interface Application {
  application_id: string
  fields: ApplicationFields
  status: 'draft' | 'submitted' | 'withdrawn'
  submitted_seq: number | null
  pecl_marks: Record<string, number>
  consent_utt_id: string | null
}

export interface ApplicationView {
  application: Application
  warnings: string[]
}

export interface CheckResult {
  check_id: string
  status: 'pass' | 'fail' | 'not_applicable' | 'not_observed' | 'ungraded'
  critical: boolean
  detail: string
  event_seqs: number[]
  utt_ids: string[]
}

export interface Findings {
  results: CheckResult[]
}

/** Content QA on the AI customer. Never scored. */
export interface ConsistencyFlag {
  utt_id: string
  kind: 'doctor' | 'drug' | 'amount'
  said: string
  text: string
}

export interface ActorQa {
  session_id: string
  actor: string | null
  prompt_version: string | null
  flags: ConsistencyFlag[]
}
