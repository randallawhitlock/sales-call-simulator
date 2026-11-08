import { useState, type FormEvent } from 'react'
import {
  get,
  post,
  type CompareResult,
  type DocumentSummary,
  type DocumentView,
  type Drug,
  type FormularyResult,
  type Logged,
  type NetworkStatus,
  type Pharmacy,
  type PharmacyNetwork,
  type PlanDetail,
  type PlanSummary,
  type ProviderResult,
} from '../api.ts'
import { Markdown } from '../Markdown.tsx'
import { Badge, ErrorNote, Field, PinButton } from '../ui.tsx'
import { money, useAction } from '../util.ts'
import { ApplicationTab } from './ApplicationTab.tsx'
import { useCall } from './context.tsx'

const TABS = ['Plans', 'Providers', 'Drugs', 'Pharmacy', 'Compare', 'Documents', 'Application'] as const
type Tab = (typeof TABS)[number]

export function ResearchPane() {
  const [tab, setTab] = useState<Tab>('Plans')
  return (
    <section className="pane research-pane" aria-label="Research">
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>
      <div className="tab-body">
        <div hidden={tab !== 'Plans'}><PlansTab /></div>
        <div hidden={tab !== 'Providers'}><ProvidersTab /></div>
        <div hidden={tab !== 'Drugs'}><DrugsTab /></div>
        <div hidden={tab !== 'Pharmacy'}><PharmacyTab /></div>
        <div hidden={tab !== 'Compare'}><CompareTab /></div>
        <div hidden={tab !== 'Documents'}><DocumentsTab /></div>
        <div hidden={tab !== 'Application'}><ApplicationTab /></div>
      </div>
    </section>
  )
}

/** Plans the learner has found. Offers the ZIP search when none are loaded yet. */
export function PlanSelect({ value, onChange, label = 'Plan' }: { value: string; onChange: (id: string) => void; label?: string }) {
  const { seen } = useCall()
  if (seen.plans.length === 0) return <LoadPlans />
  return (
    <Field label={label}>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose a plan</option>
        {seen.plans.map((p) => (
          <option key={p.plan_id} value={p.plan_id}>
            {p.name}
          </option>
        ))}
      </select>
    </Field>
  )
}

function LoadPlans() {
  const { base, session, live, remember } = useCall()
  const { busy, error, run } = useAction()
  return (
    <div className="field">
      <button
        type="button"
        className="small"
        disabled={!live || busy}
        onClick={() =>
          void run(async () => {
            const res = await get<Logged<PlanSummary[]>>(`${base}/research/plans`, { zip: session.crm_seed.zip })
            remember({ plans: res.result })
          })
        }
      >
        Load plans for ZIP {session.crm_seed.zip}
      </button>
      <ErrorNote error={error} />
    </div>
  )
}

function PlansTab() {
  const { base, session, live, remember } = useCall()
  const [zip, setZip] = useState(session.crm_seed.zip)
  const [plans, setPlans] = useState<Logged<PlanSummary[]> | null>(null)
  const [detail, setDetail] = useState<Logged<PlanDetail> | null>(null)
  const { busy, error, run } = useAction()

  function search(e: FormEvent): void {
    e.preventDefault()
    void run(async () => {
      const res = await get<Logged<PlanSummary[]>>(`${base}/research/plans`, { zip })
      setPlans(res)
      setDetail(null)
      remember({ plans: res.result })
    })
  }

  return (
    <div>
      <form className="row" onSubmit={search}>
        <Field label="ZIP code">
          <input value={zip} onChange={(e) => setZip(e.target.value)} inputMode="numeric" maxLength={5} />
        </Field>
        <button type="submit" disabled={!live || busy}>Find plans</button>
      </form>
      <ErrorNote error={error} />
      {plans && (
        <table>
          <thead>
            <tr><th>Plan</th><th>Type</th><th>Premium</th><th>Max out of pocket</th><th /></tr>
          </thead>
          <tbody>
            {plans.result.map((p) => (
              <tr key={p.plan_id}>
                <td>{p.name}<div className="muted small-text">{p.carrier_name}</div></td>
                <td>{p.plan_type.toUpperCase()}</td>
                <td>{money(p.monthly_premium)}/mo</td>
                <td>{money(p.moop_in_network)}</td>
                <td>
                  <button type="button" className="small" disabled={!live || busy}
                    onClick={() => void run(async () => setDetail(await get<Logged<PlanDetail>>(`${base}/research/plans/${p.plan_id}`)))}>
                    Details
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {detail && (
        <div className="card">
          <div className="row spread">
            <h3>{detail.result.name} ({detail.result.plan_year})</h3>
            <PinButton seq={detail.event_seq} />
          </div>
          <p className="muted">
            Premium {money(detail.result.monthly_premium)}/mo · Drug deductible {money(detail.result.drug_deductible)} ·
            PCP required: {detail.result.pcp_required ? 'yes' : 'no'} · Referrals: {detail.result.referrals_required ? 'yes' : 'no'}
          </p>
          <table>
            <thead><tr><th>Benefit</th><th>In network</th><th>Out of network</th><th>Prior auth</th></tr></thead>
            <tbody>
              {detail.result.benefits.map((b) => (
                <tr key={b.category}>
                  <td>{b.label}</td><td>{b.in_network}</td><td>{b.out_of_network ?? '—'}</td><td>{b.prior_auth ? 'Yes' : 'No'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function ProvidersTab() {
  const { base, session, live, remember } = useCall()
  const [q, setQ] = useState('')
  const [results, setResults] = useState<ProviderResult[] | null>(null)
  const [planId, setPlanId] = useState('')
  const [year, setYear] = useState(session.frame.plan_year)
  const [checks, setChecks] = useState<Logged<NetworkStatus>[]>([])
  const { busy, error, run } = useAction()

  function search(e: FormEvent): void {
    e.preventDefault()
    void run(async () => {
      const res = await get<Logged<ProviderResult[]>>(`${base}/research/providers`, { q })
      setResults(res.result)
      remember({ providers: res.result })
    })
  }

  function check(practiceId: string): void {
    void run(async () => {
      const res = await get<Logged<NetworkStatus>>(`${base}/research/providers/network`, { practice_id: practiceId, plan_id: planId, year })
      setChecks((prev) => [res, ...prev])
    })
  }

  return (
    <div>
      <form className="row" onSubmit={search}>
        <Field label="Doctor or group name">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. Raman" />
        </Field>
        <button type="submit" disabled={!live || busy}>Search</button>
      </form>
      <div className="row">
        <PlanSelect value={planId} onChange={setPlanId} />
        <Field label="Network year">
          <select value={year} onChange={(e) => setYear(Number(e.target.value))}>
            <option value={session.frame.plan_year}>{session.frame.plan_year}</option>
            <option value={session.frame.plan_year - 1}>{session.frame.plan_year - 1}</option>
          </select>
        </Field>
      </div>
      <ErrorNote error={error} />
      {results?.length === 0 && <p className="muted">No providers found.</p>}
      {results?.map((p) => (
        <div key={p.provider_id} className="card">
          <strong>{p.name}, {p.credentials}</strong> <span className="muted">· {p.specialty}</span>
          <ul className="plain">
            {p.practices.map((pr) => (
              <li key={pr.practice_id} className="row spread">
                <span>{pr.location.name}<span className="muted small-text"> · {pr.location.address}, {pr.location.city} {pr.location.zip}</span></span>
                <button type="button" className="small" disabled={!live || busy || !planId} onClick={() => check(pr.practice_id)}>
                  Check network
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {checks.length > 0 && <h3>Network checks</h3>}
      {checks.map((c) => (
        <div key={c.event_seq} className="card row spread">
          <span>
            {c.result.provider_name} at {c.result.location.name} on {c.result.plan_name} ({c.result.year}):{' '}
            {c.result.in_network ? <Badge tone="good">In network</Badge> : <Badge tone="bad">Not in network</Badge>}
          </span>
          <PinButton seq={c.event_seq} />
        </div>
      ))}
    </div>
  )
}

function DrugsTab() {
  const { base, live, remember } = useCall()
  const [q, setQ] = useState('')
  const [drugs, setDrugs] = useState<Drug[] | null>(null)
  const [planId, setPlanId] = useState('')
  const [checks, setChecks] = useState<Logged<FormularyResult>[]>([])
  const { busy, error, run } = useAction()

  function search(e: FormEvent): void {
    e.preventDefault()
    void run(async () => {
      const res = await get<Logged<Drug[]>>(`${base}/research/drugs`, { q })
      setDrugs(res.result)
      remember({ drugs: res.result })
    })
  }

  return (
    <div>
      <form className="row" onSubmit={search}>
        <Field label="Drug name">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. atorvastatin" />
        </Field>
        <button type="submit" disabled={!live || busy}>Search</button>
      </form>
      <PlanSelect value={planId} onChange={setPlanId} />
      <ErrorNote error={error} />
      {drugs?.length === 0 && <p className="muted">No drugs found.</p>}
      <ul className="plain">
        {drugs?.map((d) => (
          <li key={d.drug_id} className="row spread">
            <span>{d.name} {d.strength} {d.form}{d.generic ? '' : ' (brand)'}</span>
            <button type="button" className="small" disabled={!live || busy || !planId}
              onClick={() => void run(async () => {
                const res = await get<Logged<FormularyResult>>(`${base}/research/formulary`, { plan_id: planId, drug_id: d.drug_id })
                setChecks((prev) => [res, ...prev])
              })}>
              Check coverage
            </button>
          </li>
        ))}
      </ul>
      {checks.map((c) => {
        const r = c.result
        return (
          <div key={c.event_seq} className="card">
            <div className="row spread">
              <span>
                <strong>{r.drug.name} {r.drug.strength}</strong> on {r.plan_name}:{' '}
                {r.covered ? <Badge tone="good">Covered, tier {r.tier}</Badge> : <Badge tone="bad">Not covered</Badge>}{' '}
                {r.prior_auth && <Badge tone="warn">Prior authorization</Badge>}{' '}
                {r.step_therapy && <Badge tone="warn">Step therapy</Badge>}{' '}
                {r.quantity_limit && <Badge tone="warn">Limit: {r.quantity_limit}</Badge>}
              </span>
              <PinButton seq={c.event_seq} />
            </div>
            {r.copays && (
              <p className="muted small-text">
                Preferred retail 30-day {money(r.copays['preferred_retail_30'] ?? null)} · Standard retail 30-day{' '}
                {money(r.copays['standard_retail_30'] ?? null)} · Mail 90-day {money(r.copays['mail_90'] ?? null)}
                {r.deductible_applies ? ' · Deductible applies' : ''}
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}

function PharmacyTab() {
  const { base, session, live, remember } = useCall()
  const [q, setQ] = useState('')
  const [zip, setZip] = useState('')
  const [found, setFound] = useState<Pharmacy[] | null>(null)
  const [planId, setPlanId] = useState('')
  const [checks, setChecks] = useState<Logged<PharmacyNetwork>[]>([])
  const { busy, error, run } = useAction()

  function search(e: FormEvent): void {
    e.preventDefault()
    void run(async () => {
      const res = await get<Logged<Pharmacy[]>>(`${base}/research/pharmacies`, { q, zip })
      setFound(res.result)
      remember({ pharmacies: res.result })
    })
  }

  return (
    <div>
      <form className="row" onSubmit={search}>
        <Field label="Pharmacy name">
          <input value={q} onChange={(e) => setQ(e.target.value)} />
        </Field>
        <Field label="ZIP (optional)">
          <input value={zip} onChange={(e) => setZip(e.target.value)} placeholder={session.crm_seed.zip} maxLength={5} />
        </Field>
        <button type="submit" disabled={!live || busy}>Search</button>
      </form>
      <PlanSelect value={planId} onChange={setPlanId} />
      <ErrorNote error={error} />
      <ul className="plain">
        {found?.map((p) => (
          <li key={p.pharmacy_id} className="row spread">
            <span>{p.name}<span className="muted small-text"> · {p.mail_order ? 'Mail order' : `${p.location.address}, ${p.location.city}`}</span></span>
            <button type="button" className="small" disabled={!live || busy || !planId}
              onClick={() => void run(async () => {
                const res = await get<Logged<PharmacyNetwork>>(`${base}/research/pharmacies/network`, { plan_id: planId, pharmacy_id: p.pharmacy_id })
                setChecks((prev) => [res, ...prev])
              })}>
              Check network
            </button>
          </li>
        ))}
      </ul>
      {checks.map((c) => (
        <div key={c.event_seq} className="card row spread">
          <span>
            {c.result.pharmacy.name} on {c.result.plan_name}:{' '}
            {c.result.in_network ? <Badge tone="good">{c.result.preferred ? 'Preferred' : 'In network'}</Badge> : <Badge tone="bad">Not in network</Badge>}
          </span>
          <PinButton seq={c.event_seq} />
        </div>
      ))}
    </div>
  )
}

function CompareTab() {
  const { base, live, seen } = useCall()
  const [planIds, setPlanIds] = useState<string[]>([])
  const [drugs, setDrugs] = useState<{ drug_id: string; days_supply: 30 | 90 }[]>([])
  const [pharmacyId, setPharmacyId] = useState('')
  const [result, setResult] = useState<Logged<CompareResult> | null>(null)
  const { busy, error, run } = useAction()

  const togglePlan = (id: string) =>
    setPlanIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : prev.length < 4 ? [...prev, id] : prev))
  const toggleDrug = (id: string) =>
    setDrugs((prev) => (prev.some((d) => d.drug_id === id) ? prev.filter((d) => d.drug_id !== id) : [...prev, { drug_id: id, days_supply: 30 }]))

  return (
    <div>
      <p className="muted small-text">Pick up to four plans, the customer's drugs and pharmacy. Search the other tabs first to fill these lists.</p>
      {seen.plans.length === 0 ? <PlanSelect value="" onChange={() => {}} /> : (
        <fieldset>
          <legend>Plans ({planIds.length}/4)</legend>
          {seen.plans.map((p) => (
            <label key={p.plan_id} className="check">
              <input type="checkbox" checked={planIds.includes(p.plan_id)} onChange={() => togglePlan(p.plan_id)} /> {p.name}
            </label>
          ))}
        </fieldset>
      )}
      <fieldset>
        <legend>Drugs</legend>
        {seen.drugs.length === 0 && <p className="muted small-text">Search drugs in the Drugs tab.</p>}
        {seen.drugs.map((d) => {
          const picked = drugs.find((x) => x.drug_id === d.drug_id)
          return (
            <div key={d.drug_id} className="row">
              <label className="check">
                <input type="checkbox" checked={!!picked} onChange={() => toggleDrug(d.drug_id)} /> {d.name} {d.strength}
              </label>
              {picked && (
                <select value={picked.days_supply} aria-label="Days supply"
                  onChange={(e) => setDrugs((prev) => prev.map((x) => (x.drug_id === d.drug_id ? { ...x, days_supply: Number(e.target.value) as 30 | 90 } : x)))}>
                  <option value={30}>30-day</option>
                  <option value={90}>90-day</option>
                </select>
              )}
            </div>
          )
        })}
      </fieldset>
      <Field label="Pharmacy">
        <select value={pharmacyId} onChange={(e) => setPharmacyId(e.target.value)}>
          <option value="">{seen.pharmacies.length ? 'Choose a pharmacy' : 'Search pharmacies first'}</option>
          {seen.pharmacies.map((p) => <option key={p.pharmacy_id} value={p.pharmacy_id}>{p.name}</option>)}
        </select>
      </Field>
      <button type="button" disabled={!live || busy || planIds.length === 0 || !pharmacyId}
        onClick={() => void run(async () => setResult(await post<Logged<CompareResult>>(`${base}/research/compare`, { plan_ids: planIds, drugs, pharmacy_id: pharmacyId })))}>
        Compare
      </button>
      <ErrorNote error={error} />
      {result && (
        <div className="card">
          <div className="row spread">
            <Badge tone="info">{result.result.label}</Badge>
            <PinButton seq={result.event_seq} />
          </div>
          <p className="muted small-text">{result.result.ranking}</p>
          <table>
            <thead><tr><th>Plan</th><th>Premium/yr</th><th>Drugs/yr</th><th>Deductible</th><th>Total</th></tr></thead>
            <tbody>
              {result.result.plans.map((p) => (
                <tr key={p.plan.plan_id}>
                  <td>
                    {p.plan.name}
                    {!p.estimate_complete && <> <Badge tone="warn">Incomplete</Badge></>}
                    {p.warnings.map((w) => <div key={w} className="muted small-text">{w}</div>)}
                  </td>
                  <td>{money(p.annual_premium)}</td>
                  <td>{money(p.annual_drug_cost)}</td>
                  <td>{money(p.drug_deductible_applied)}</td>
                  <td><strong>{money(p.total)}</strong></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small-text">{result.result.method}</p>
        </div>
      )}
    </div>
  )
}

function DocumentsTab() {
  const { base, live } = useCall()
  const [docs, setDocs] = useState<DocumentSummary[] | null>(null)
  const [open, setOpen] = useState<Logged<DocumentView> | null>(null)
  const { busy, error, run } = useAction()

  return (
    <div>
      {docs === null ? (
        <button type="button" disabled={!live || busy} onClick={() => void run(async () => setDocs(await get<DocumentSummary[]>(`${base}/documents`)))}>
          List documents
        </button>
      ) : (
        <ul className="plain">
          {docs.map((d) => (
            <li key={d.document_id} className="row spread">
              <span>{d.title}</span>
              <button type="button" className="small" disabled={!live || busy}
                onClick={() => void run(async () => setOpen(await get<Logged<DocumentView>>(`${base}/documents/${d.document_id}`)))}>
                Open
              </button>
            </li>
          ))}
        </ul>
      )}
      <ErrorNote error={error} />
      {open && (
        <article className="card document">
          <div className="row spread">
            <span className="watermark">{open.result.watermark}</span>
            <PinButton seq={open.event_seq} />
          </div>
          <Markdown text={open.result.body_markdown} />
        </article>
      )}
    </div>
  )
}
