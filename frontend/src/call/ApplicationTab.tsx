import { useState, type FormEvent } from 'react'
import {
  clientId,
  get,
  post,
  type ApplicationView,
  type DocumentSummary,
  type DocumentView,
  type Logged,
} from '../api.ts'
import { Badge, ErrorNote, Field } from '../ui.tsx'
import { useAction } from '../util.ts'
import { planName, practiceLabel, practices, useCall } from './context.tsx'
import { PlanSelect } from './ResearchPane.tsx'

export function ApplicationTab() {
  const { applications } = useCall()
  return (
    <div>
      {applications.map((a) => (
        <ApplicationCard key={a.application.application_id} view={a} />
      ))}
      <NewApplication />
    </div>
  )
}

function NewApplication() {
  const { base, session, live, seen, refreshApplications, notify } = useCall()
  const [planId, setPlanId] = useState('')
  const [name, setName] = useState(session.crm_seed.customer_name)
  const [zip, setZip] = useState('')
  const [mailingZip, setMailingZip] = useState('')
  const [pcp, setPcp] = useState('')
  const [effective, setEffective] = useState(session.frame.coverage_effective)
  const { busy, error, run } = useAction()

  function create(e: FormEvent): void {
    e.preventDefault()
    void run(async () => {
      await post(`${base}/applications`, {
        plan_id: planId,
        applicant_name: name,
        residence_zip: zip,
        mailing_zip: mailingZip || null,
        pcp_practice_id: pcp || null,
        effective_date: effective,
      })
      await refreshApplications()
      notify('Application started')
    })
  }

  return (
    <form className="card" onSubmit={create}>
      <h3>New application</h3>
      <PlanSelect value={planId} onChange={setPlanId} />
      <Field label="Applicant name"><input value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <div className="row">
        <Field label="Residence ZIP"><input value={zip} onChange={(e) => setZip(e.target.value)} maxLength={5} /></Field>
        <Field label="Mailing ZIP (if different)"><input value={mailingZip} onChange={(e) => setMailingZip(e.target.value)} maxLength={5} /></Field>
      </div>
      <Field label="Primary care doctor">
        <select value={pcp} onChange={(e) => setPcp(e.target.value)}>
          <option value="">None</option>
          {practices(seen).map((p) => (
            <option key={p.practice_id} value={p.practice_id}>{p.provider.name}, {p.location.name}</option>
          ))}
        </select>
      </Field>
      <Field label="Effective date"><input type="date" value={effective} onChange={(e) => setEffective(e.target.value)} /></Field>
      <button type="submit" disabled={!live || busy || !planId || !zip}>Start application</button>
      <ErrorNote error={error} />
    </form>
  )
}

function ApplicationCard({ view }: { view: ApplicationView }) {
  const { base, live, seen, source, refreshApplications, notify } = useCall()
  const app = view.application
  const [items, setItems] = useState<string[] | null>(null)
  const [submitKey] = useState(() => clientId('submit'))
  const { busy, error, run } = useAction()
  const path = `${base}/applications/${app.application_id}`
  const draft = app.status === 'draft'
  const marked = new Set(Object.keys(app.pecl_marks).map(Number))
  const act = (fn: () => Promise<unknown>, message: string) =>
    void run(async () => {
      await fn()
      await refreshApplications()
      notify(message)
    })

  return (
    <div className="card">
      <div className="row spread">
        <h3>
          {app.application_id}: {planName(seen, app.fields.plan_id)}
        </h3>
        <Badge tone={app.status === 'submitted' ? 'good' : app.status === 'withdrawn' ? 'bad' : 'info'}>{app.status}</Badge>
      </div>
      <p className="muted small-text">
        {app.fields.applicant_name} · ZIP {app.fields.residence_zip}
        {app.fields.mailing_zip ? ` · mail ${app.fields.mailing_zip}` : ''} · PCP{' '}
        {app.fields.pcp_practice_id ? practiceLabel(seen, app.fields.pcp_practice_id) : 'none'} · effective {app.fields.effective_date}
      </p>
      {view.warnings.map((w) => <p key={w} className="notice small-text">{w}</p>)}
      {draft && (
        <>
          <h4>Pre-Enrollment Checklist</h4>
          {items === null ? (
            <button type="button" className="small" disabled={!live || busy}
              onClick={() => void run(async () => {
                const docs = await get<DocumentSummary[]>(`${base}/documents`, { plan_id: app.fields.plan_id })
                const pecl = docs.find((d) => d.kind === 'pre_enrollment_checklist')
                if (!pecl) throw new Error('No checklist for this plan')
                setItems((await get<Logged<DocumentView>>(`${base}/documents/${pecl.document_id}`)).result.checklist_items)
              })}>
              Open checklist
            </button>
          ) : (
            <ol className="checklist">
              {items.map((item, i) => (
                <li key={i}>
                  <label className="check">
                    <input type="checkbox" checked={marked.has(i)} disabled={!live || busy || marked.has(i)}
                      onChange={() => act(() => post(`${path}/pecl`, { item_index: i }), `Checklist item ${i + 1} reviewed`)} />
                    {item}
                  </label>
                </li>
              ))}
            </ol>
          )}
          <h4>Consent</h4>
          <p className="small-text">
            {app.consent_utt_id ? <>Consent cites {app.consent_utt_id}.</> : 'No consent recorded.'}{' '}
            <button type="button" className="small" disabled={!live || busy || !source}
              title={source ? `Cite ${source.utt_id}` : 'Pick the customer’s yes with “Use as source” first'}
              onClick={() => source && act(() => post(`${path}/consent`, { utt_id: source.utt_id }), `Consent cites ${source.utt_id}`)}>
              {source ? `Use ${source.utt_id} as consent` : 'Select the customer’s yes first'}
            </button>
          </p>
          <div className="row">
            <button type="button" className="primary" disabled={!live || busy}
              onClick={() => act(() => post(`${path}/submit`, { idempotency_key: submitKey }), 'Application submitted')}>
              Submit application
            </button>
            <button type="button" disabled={!live || busy} onClick={() => act(() => post(`${path}/withdraw`), 'Application withdrawn')}>
              Withdraw
            </button>
          </div>
        </>
      )}
      {app.status === 'submitted' && (
        <button type="button" className="small" disabled={!live || busy} onClick={() => act(() => post(`${path}/withdraw`), 'Application withdrawn')}>
          Withdraw
        </button>
      )}
      <ErrorNote error={error} />
    </div>
  )
}
