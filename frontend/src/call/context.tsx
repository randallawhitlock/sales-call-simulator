import { createContext, useContext } from 'react'
import type {
  ApplicationView,
  CustomerRecord,
  Drug,
  LogEvent,
  Pharmacy,
  PlanSummary,
  Practice,
  ProviderResult,
  Session,
  Utterance,
} from '../api.ts'

/** Catalog items the learner has found through research. Record and application pickers offer
 * only these, so identifying a doctor or drug still takes a search. */
export interface Seen {
  plans: PlanSummary[]
  providers: ProviderResult[]
  drugs: Drug[]
  pharmacies: Pharmacy[]
}

export interface CallContextValue {
  session: Session
  base: string
  live: boolean
  transcript: Utterance[]
  setTranscript: (update: (prev: Utterance[]) => Utterance[]) => void
  record: CustomerRecord | null
  refreshRecord: () => Promise<void>
  applications: ApplicationView[]
  refreshApplications: () => Promise<void>
  evidence: LogEvent[]
  pin: (seq: number) => Promise<void>
  pinned: Set<number>
  /** A customer line picked with "Use as source"; record saves and consent cite it. */
  source: Utterance | null
  setSource: (utt: Utterance | null) => void
  seen: Seen
  remember: (found: Partial<Seen>) => void
  notify: (message: string) => void
}

export const CallContext = createContext<CallContextValue | null>(null)

export function useCall(): CallContextValue {
  const value = useContext(CallContext)
  if (value === null) throw new Error('useCall outside CallScreen')
  return value
}

export function practices(seen: Seen): (Practice & { provider: ProviderResult })[] {
  return seen.providers.flatMap((p) => p.practices.map((pr) => ({ ...pr, provider: p })))
}

export function practiceLabel(seen: Seen, practiceId: string): string {
  const found = practices(seen).find((p) => p.practice_id === practiceId)
  return found ? `${found.provider.name}, ${found.location.name}` : practiceId
}

export function planName(seen: Seen, planId: string | null): string {
  if (planId === null) return 'none'
  return seen.plans.find((p) => p.plan_id === planId)?.name ?? planId
}

export function mergeSeen(prev: Seen, found: Partial<Seen>): Seen {
  return {
    plans: mergeById(prev.plans, found.plans ?? [], (p) => p.plan_id),
    providers: mergeById(prev.providers, found.providers ?? [], (p) => p.provider_id),
    drugs: mergeById(prev.drugs, found.drugs ?? [], (d) => d.drug_id),
    pharmacies: mergeById(prev.pharmacies, found.pharmacies ?? [], (p) => p.pharmacy_id),
  }
}

export function mergeById<T>(prev: T[], next: T[], id: (item: T) => string): T[] {
  const out = [...prev]
  for (const item of next) {
    const i = out.findIndex((x) => id(x) === id(item))
    if (i === -1) out.push(item)
    else out[i] = item
  }
  return out
}

export function mergeTranscript(prev: Utterance[], next: Utterance[]): Utterance[] {
  return mergeById(prev, next, (u) => u.utt_id).sort((a, b) => a.utt_id.localeCompare(b.utt_id))
}
