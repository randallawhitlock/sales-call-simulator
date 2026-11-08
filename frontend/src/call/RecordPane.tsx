import { useState, type ReactNode } from 'react'
import { post, put, type CustomerRecord, type DoctorEntry, type DrugEntry, type LogEvent } from '../api.ts'
import { ErrorNote, Field } from '../ui.tsx'
import { useAction } from '../util.ts'
import { planName, practices, useCall } from './context.tsx'

export function RecordPane() {
  const { record } = useCall()
  return (
    <section className="pane record-pane" aria-label="Customer record">
      <div className="pane-head">
        <h2>Customer record</h2>
      </div>
      <SourceBar />
      <ScopeSection />
      {record && (
        <>
          <ContactSection key={`contact-${record.saved_seq['contact'] ?? 0}`} />
          <DoctorsSection key={`doctors-${record.saved_seq['doctors'] ?? 0}`} />
          <DrugsSection key={`drugs-${record.saved_seq['drugs'] ?? 0}`} />
          <PharmacySection key={`pharmacy-${record.saved_seq['pharmacy'] ?? 0}`} />
          <CoverageSection key={`coverage-${record.saved_seq['coverage'] ?? 0}`} />
          <NotesSection key={`notes-${record.saved_seq['notes'] ?? 0}`} />
          <DispositionSection key={`disposition-${record.saved_seq['disposition'] ?? 0}`} />
        </>
      )}
      <FollowupSection />
      <EvidenceSection />
    </section>
  )
}

function SourceBar() {
  const { source, setSource } = useCall()
  if (!source) {
    return <p className="muted small-text">Tip: click “Use as source” on a customer line to cite it when you save.</p>
  }
  return (
    <div className="source-bar">
      Citing {source.utt_id}: “{source.heard_text.slice(0, 80)}
      {source.heard_text.length > 80 ? '…' : ''}”
      <button type="button" className="small link" onClick={() => setSource(null)}>Clear</button>
    </div>
  )
}

function Section({ title, savedSeq, onSave, children, saveLabel = 'Save' }: {
  title: string
  savedSeq?: number | undefined
  onSave: () => Promise<void>
  children: ReactNode
  saveLabel?: string
}) {
  const { live, refreshRecord, notify } = useCall()
  const { busy, error, run } = useAction()
  return (
    <details className="section" open>
      <summary>
        {title}
        {savedSeq !== undefined && <span className="muted small-text"> · saved</span>}
      </summary>
      {children}
      <button type="button" className="small" disabled={!live || busy}
        onClick={() => void run(async () => {
          await onSave()
          await refreshRecord()
          notify(`${title} saved`)
        })}>
        {saveLabel}
      </button>
      <ErrorNote error={error} />
    </details>
  )
}

/** A section's editable copy of the saved record. RecordPane keys each section on its saved
 * seq, so a save remounts the section with the fresh value. */
function useSection<T>(pick: (r: CustomerRecord) => T | null | undefined, fallback: T): [T, (v: T) => void] {
  const { record } = useCall()
  const [value, setValue] = useState<T>(() => (record ? (pick(record) ?? fallback) : fallback))
  return [value, setValue]
}

function CiteButton({ value, onChange }: { value: string | null; onChange: (id: string | null) => void }) {
  const { source } = useCall()
  if (value) {
    return <button type="button" className="small link" onClick={() => onChange(null)}>Cited {value} ✕</button>
  }
  return (
    <button type="button" className="small link" disabled={!source} onClick={() => source && onChange(source.utt_id)}>
      Cite selected line
    </button>
  )
}

function ScopeSection() {
  const { base, source } = useCall()
  const [products, setProducts] = useState<string[]>(['ma'])
  const [method, setMethod] = useState<'verbal' | 'written'>('verbal')
  const [done, setDone] = useState<string | null>(null)
  const options: [string, string][] = [['ma', 'Medicare Advantage'], ['pdp', 'Drug plans'], ['medigap', 'Medigap'], ['dental_vision_hearing', 'Dental, vision, hearing']]
  return (
    <Section title="Scope of appointment" saveLabel="Record scope" onSave={async () => {
      const ev = await post<LogEvent>(`${base}/scope`, { products, method, source_utt_id: source?.utt_id ?? null })
      setDone(`Recorded (event ${ev.seq})`)
    }}>
      {options.map(([id, label]) => (
        <label key={id} className="check">
          <input type="checkbox" checked={products.includes(id)}
            onChange={() => setProducts((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))} /> {label}
        </label>
      ))}
      <Field label="How agreed">
        <select value={method} onChange={(e) => setMethod(e.target.value as 'verbal' | 'written')}>
          <option value="verbal">Verbal</option>
          <option value="written">Written</option>
        </select>
      </Field>
      <p className="muted small-text">{done ?? (source ? `Will cite ${source.utt_id}.` : 'Select the customer’s agreement to cite it.')}</p>
    </Section>
  )
}

function ContactSection() {
  const { base, record } = useCall()
  const [c, setC] = useSection<NonNullable<CustomerRecord['contact']>>((r) => r.contact, {
    residence_zip: null, mailing_same_as_residence: true, mailing_zip: null, helper_name: null, authorized_representative: null,
  })
  const set = (patch: Partial<typeof c>) => setC({ ...c, ...patch })
  return (
    <Section title="Contact" savedSeq={record?.saved_seq['contact']} onSave={() => put(`${base}/record/contact`, c).then(() => {})}>
      <Field label="Residence ZIP"><input value={c.residence_zip ?? ''} maxLength={5} onChange={(e) => set({ residence_zip: e.target.value || null })} /></Field>
      <label className="check"><input type="checkbox" checked={c.mailing_same_as_residence} onChange={(e) => set({ mailing_same_as_residence: e.target.checked })} /> Mail goes to the residence</label>
      {!c.mailing_same_as_residence && (
        <Field label="Mailing ZIP"><input value={c.mailing_zip ?? ''} maxLength={5} onChange={(e) => set({ mailing_zip: e.target.value || null })} /></Field>
      )}
      <Field label="Someone helping on the call"><input value={c.helper_name ?? ''} onChange={(e) => set({ helper_name: e.target.value || null })} /></Field>
      <Field label="Authorized representative"><input value={c.authorized_representative ?? ''} onChange={(e) => set({ authorized_representative: e.target.value || null })} /></Field>
    </Section>
  )
}

function DoctorsSection() {
  const { base, record, seen } = useCall()
  const [rows, setRows] = useSection<DoctorEntry[]>((r) => r.doctors?.doctors, [])
  const update = (i: number, patch: Partial<DoctorEntry>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  return (
    <Section title="Doctors" savedSeq={record?.saved_seq['doctors']} onSave={() => put(`${base}/record/doctors`, { doctors: rows }).then(() => {})}>
      {rows.map((d, i) => (
        <div key={i} className="entry">
          <div className="row">
            <Field label="Name"><input value={d.name} onChange={(e) => update(i, { name: e.target.value })} /></Field>
            <Field label="Role">
              <select value={d.role} onChange={(e) => update(i, { role: e.target.value as DoctorEntry['role'] })}>
                <option value="pcp">Primary care</option>
                <option value="specialist">Specialist</option>
                <option value="other">Other</option>
              </select>
            </Field>
          </div>
          <Field label="Matched office">
            <select value={d.practice_id ?? ''} onChange={(e) => update(i, { practice_id: e.target.value || null })}>
              <option value="">Not matched yet</option>
              {practices(seen).map((p) => <option key={p.practice_id} value={p.practice_id}>{p.provider.name}, {p.location.name}</option>)}
            </select>
          </Field>
          <div className="row">
            <label className="check"><input type="checkbox" checked={d.must_keep} onChange={(e) => update(i, { must_keep: e.target.checked })} /> Must keep</label>
            <CiteButton value={d.source_utt_id} onChange={(id) => update(i, { source_utt_id: id })} />
            <button type="button" className="small link" onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button>
          </div>
        </div>
      ))}
      <button type="button" className="small" onClick={() => setRows([...rows, { name: '', role: 'specialist', practice_id: null, must_keep: false, source_utt_id: null }])}>
        Add doctor
      </button>{' '}
    </Section>
  )
}

function DrugsSection() {
  const { base, record, seen } = useCall()
  const [status, setStatus] = useSection<'listed' | 'none' | 'unknown'>((r) => r.drugs?.status, 'unknown')
  const [rows, setRows] = useSection<DrugEntry[]>((r) => r.drugs?.drugs, [])
  const update = (i: number, patch: Partial<DrugEntry>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const effective = rows.length > 0 ? 'listed' : status === 'listed' ? 'unknown' : status
  return (
    <Section title="Prescriptions" savedSeq={record?.saved_seq['drugs']}
      onSave={() => put(`${base}/record/drugs`, { status: effective, drugs: effective === 'listed' ? rows : [] }).then(() => {})}>
      {rows.length === 0 && (
        <Field label="Status">
          <select value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
            <option value="unknown">Not asked yet</option>
            <option value="none">Takes no prescriptions</option>
          </select>
        </Field>
      )}
      {rows.map((d, i) => (
        <div key={i} className="entry">
          <Field label="Drug">
            <select value={d.drug_id ?? ''} onChange={(e) => update(i, { drug_id: e.target.value || null })}>
              <option value="">Not identified yet</option>
              {seen.drugs.map((x) => <option key={x.drug_id} value={x.drug_id}>{x.name} {x.strength} {x.form}</option>)}
            </select>
          </Field>
          <div className="row">
            <Field label="As the customer said it"><input value={d.name_reported ?? ''} onChange={(e) => update(i, { name_reported: e.target.value || null })} /></Field>
            <Field label="Supply">
              <select value={d.days_supply} onChange={(e) => update(i, { days_supply: Number(e.target.value) as 30 | 90 })}>
                <option value={30}>30-day</option>
                <option value={90}>90-day</option>
              </select>
            </Field>
          </div>
          <div className="row">
            <CiteButton value={d.source_utt_id} onChange={(id) => update(i, { source_utt_id: id })} />
            <button type="button" className="small link" onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button>
          </div>
        </div>
      ))}
      <button type="button" className="small" onClick={() => setRows([...rows, { drug_id: null, name_reported: null, days_supply: 30, source_utt_id: null }])}>
        Add drug
      </button>{' '}
    </Section>
  )
}

function PharmacySection() {
  const { base, record, seen } = useCall()
  const [p, setP] = useSection<NonNullable<CustomerRecord['pharmacy']>>((r) => r.pharmacy, { pharmacy_id: null, name_reported: null })
  const [cite, setCite] = useState<string | null>(null)
  return (
    <Section title="Pharmacy" savedSeq={record?.saved_seq['pharmacy']}
      onSave={() => put(`${base}/record/pharmacy`, { ...p, source_utt_id: cite }).then(() => {})}>
      <Field label="Pharmacy">
        <select value={p.pharmacy_id ?? ''} onChange={(e) => setP({ ...p, pharmacy_id: e.target.value || null })}>
          <option value="">Not identified yet</option>
          {seen.pharmacies.map((x) => <option key={x.pharmacy_id} value={x.pharmacy_id}>{x.name}</option>)}
        </select>
      </Field>
      <Field label="As the customer said it"><input value={p.name_reported ?? ''} onChange={(e) => setP({ ...p, name_reported: e.target.value || null })} /></Field>
      <CiteButton value={cite} onChange={setCite} />
    </Section>
  )
}

const OTHER_KINDS: [string, string][] = [['employer_retiree', 'Employer or retiree plan'], ['medigap', 'Medigap'], ['medicaid', 'Medicaid'], ['va', 'VA'], ['tricare', 'TRICARE']]

function CoverageSection() {
  const { base, record, seen } = useCall()
  const [c, setC] = useSection<NonNullable<CustomerRecord['coverage']>>((r) => r.coverage, {
    current_plan_id: null, original_medicare: null, medicare_parts: [], medicaid: 'unknown', other_coverage: [],
  })
  const kinds = c.other_coverage.map((o) => o.kind)
  return (
    <Section title="Coverage" savedSeq={record?.saved_seq['coverage']} onSave={() => put(`${base}/record/coverage`, c).then(() => {})}>
      <Field label="Current plan">
        <select value={c.original_medicare ? 'om' : c.current_plan_id ?? ''}
          onChange={(e) => setC({ ...c, current_plan_id: e.target.value && e.target.value !== 'om' ? e.target.value : null, original_medicare: e.target.value === 'om' ? true : null })}>
          <option value="">Unknown</option>
          <option value="om">Original Medicare</option>
          {seen.plans.map((p) => <option key={p.plan_id} value={p.plan_id}>{planName(seen, p.plan_id)}</option>)}
        </select>
      </Field>
      <div className="row">
        {(['a', 'b'] as const).map((part) => (
          <label key={part} className="check">
            <input type="checkbox" checked={c.medicare_parts.includes(part)}
              onChange={() => setC({ ...c, medicare_parts: c.medicare_parts.includes(part) ? c.medicare_parts.filter((x) => x !== part) : [...c.medicare_parts, part] })} />
            Part {part.toUpperCase()}
          </label>
        ))}
        <Field label="Medicaid">
          <select value={c.medicaid} onChange={(e) => setC({ ...c, medicaid: e.target.value as typeof c.medicaid })}>
            <option value="unknown">Unknown</option>
            <option value="no">No</option>
            <option value="yes">Yes</option>
          </select>
        </Field>
      </div>
      <fieldset>
        <legend>Other coverage</legend>
        {OTHER_KINDS.map(([kind, label]) => (
          <label key={kind} className="check">
            <input type="checkbox" checked={kinds.includes(kind)}
              onChange={() => setC({ ...c, other_coverage: kinds.includes(kind) ? c.other_coverage.filter((o) => o.kind !== kind) : [...c.other_coverage, { kind }] })} />
            {label}
          </label>
        ))}
      </fieldset>
    </Section>
  )
}

const NOTE_FIELDS: [string, string][] = [
  ['reason_for_inquiry', 'Reason for the call'], ['priorities', 'Priorities'], ['findings', 'Findings'],
  ['options_discussed', 'Options discussed'], ['rationale', 'Rationale'], ['next_steps', 'Next steps'],
]

function NotesSection() {
  const { base, record } = useCall()
  const [n, setN] = useSection<Record<string, string>>((r) => r.notes, {})
  return (
    <Section title="Notes" savedSeq={record?.saved_seq['notes']}
      onSave={() => put(`${base}/record/notes`, Object.fromEntries(NOTE_FIELDS.map(([k]) => [k, n[k] ?? '']))).then(() => {})}>
      {NOTE_FIELDS.map(([k, label]) => (
        <Field key={k} label={label}><textarea rows={2} value={n[k] ?? ''} onChange={(e) => setN({ ...n, [k]: e.target.value })} /></Field>
      ))}
    </Section>
  )
}

function DispositionSection() {
  const { base, record, seen } = useCall()
  const [d, setD] = useSection<NonNullable<CustomerRecord['disposition']>>((r) => r.disposition, { outcome: 'defer', plan_id: null, rationale: '' })
  return (
    <Section title="Decision" savedSeq={record?.saved_seq['disposition']}
      onSave={() => put(`${base}/record/disposition`, { ...d, plan_id: d.outcome === 'enroll' ? d.plan_id : null }).then(() => {})}>
      <Field label="Outcome">
        <select value={d.outcome} onChange={(e) => setD({ ...d, outcome: e.target.value })}>
          <option value="enroll">Enroll</option>
          <option value="keep_current">Keep current coverage</option>
          <option value="defer">Defer</option>
          <option value="stop">Stop</option>
        </select>
      </Field>
      {d.outcome === 'enroll' && (
        <Field label="Plan">
          <select value={d.plan_id ?? ''} onChange={(e) => setD({ ...d, plan_id: e.target.value || null })}>
            <option value="">Choose a plan</option>
            {seen.plans.map((p) => <option key={p.plan_id} value={p.plan_id}>{p.name}</option>)}
          </select>
        </Field>
      )}
      <Field label="Rationale"><textarea rows={2} value={d.rationale} onChange={(e) => setD({ ...d, rationale: e.target.value })} /></Field>
    </Section>
  )
}

function FollowupSection() {
  const { base } = useCall()
  const [due, setDue] = useState('')
  const [method, setMethod] = useState('phone')
  const [reason, setReason] = useState('')
  const [done, setDone] = useState<string | null>(null)
  return (
    <Section title="Follow-up" saveLabel="Schedule follow-up" onSave={async () => {
      const ev = await post<LogEvent>(`${base}/followups`, { due_date: due, method, reason })
      setDone(`Scheduled for ${due} (event ${ev.seq})`)
    }}>
      <div className="row">
        <Field label="Due"><input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></Field>
        <Field label="Method">
          <select value={method} onChange={(e) => setMethod(e.target.value)}>
            <option value="phone">Phone</option><option value="mail">Mail</option><option value="email">Email</option>
          </select>
        </Field>
      </div>
      <Field label="Reason"><input value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      {done && <p className="muted small-text">{done}</p>}
    </Section>
  )
}

function EvidenceSection() {
  const { evidence } = useCall()
  return (
    <details className="section" open>
      <summary>Evidence ({evidence.length})</summary>
      {evidence.length === 0 && <p className="muted small-text">Pin research results to save them here.</p>}
      <ul className="plain">
        {evidence.map((e) => (
          <li key={e.seq} className="small-text">{describeSource(e.payload['source'])} <span className="muted">· event {String(e.payload['source_seq'])}</span></li>
        ))}
      </ul>
    </details>
  )
}

function describeSource(source: unknown): string {
  if (typeof source !== 'object' || source === null) return 'Evidence'
  const s = source as Record<string, unknown>
  const parts = [s['kind'], s['plan_id'], s['practice_id'], s['drug_id'], s['pharmacy_id'], s['document_id'], s['year'] ?? s['plan_year']]
  return parts.filter((p) => p !== undefined && p !== null).map(String).join(' · ')
}
