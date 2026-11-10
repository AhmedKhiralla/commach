import { useEffect, useMemo, useState } from "react";
import {
  Badge, Button, Dialog, LoadError, Notice, PageHead, SelectField, Switch, TextField, errorText, fieldErrors, useLoad, useToast,
} from "../components/ui";
import { get, patch, post, put } from "../lib/api";
import { COUNTRIES, timeZones } from "../lib/geo";
import { useI18n } from "../lib/i18n";
import { navigate } from "../lib/router";
import { useSession, type Policy } from "../lib/session";
import type { StringKey } from "../lib/strings";

interface SettingsData {
  company: { id: string; code: string; name: string; country: string; timezone: string; default_language: "en" | "ar" };
  features: Record<string, boolean>;
  feature_groups: Record<string, string>;
  ai: { provider: string | null; daily_limit: number };
  ai_providers: { name: string; label: string; vision_model: string; text_model: string }[];
  terminology: Record<string, { en: string; ar: string }>;
  terminology_defaults: Record<string, { en: string; ar: string }>;
  security: Policy;
  security_limits: Record<string, [number, number]>;
}

const TABS = ["company", "features", "ai", "terms", "security", "apps", "demo"] as const;
type Tab = (typeof TABS)[number];

function useSaver(onSaved: (d: SettingsData) => void) {
  const { t } = useI18n();
  const toast = useToast();
  const { refresh } = useSession();
  const [busy, setBusy] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [general, setGeneral] = useState<string | null>(null);
  const run = async (fn: () => Promise<SettingsData>) => {
    setBusy(true); setErrs({}); setGeneral(null);
    try { const d = await fn(); onSaved(d); toast(t("settings.savedToast")); refresh(); }
    catch (e) { setErrs(fieldErrors(e)); setGeneral(errorText(e, t)); }
    finally { setBusy(false); }
  };
  return { busy, errs, general, run };
}

function CompanyTab({ data, onSaved }: { data: SettingsData; onSaved: (d: SettingsData) => void }) {
  const { t, countryName } = useI18n();
  const [f, setF] = useState({ ...data.company });
  const s = useSaver(onSaved);
  const countries = useMemo(() => COUNTRIES.map((c) => ({ value: c, label: countryName(c) })).sort((a, b) => a.label.localeCompare(b.label)), [countryName]);
  const zones = useMemo(() => timeZones().map((z) => ({ value: z, label: z.replace(/_/g, " ") })), []);
  return (
    <form className="panel" onSubmit={(e) => { e.preventDefault(); s.run(() => patch("/api/settings/company", { name: f.name, country: f.country, timezone: f.timezone, default_language: f.default_language })); }}>
      <div className="panel-head"><p className="muted">{t("settings.companyLead")}</p></div>
      <div className="panel-body stack" style={{ maxWidth: 640 }}>
        {s.general && <Notice tone="danger">{s.general}</Notice>}
        <div className="grid-2">
          <TextField label={t("companies.form.name")} value={f.name} onChange={(v) => setF({ ...f, name: v })} error={s.errs.name} />
          <TextField label={t("companies.form.code")} value={f.code} onChange={() => {}} disabled dir="ltr" />
        </div>
        <div className="grid-2">
          <SelectField label={t("common.country")} value={f.country} onChange={(v) => setF({ ...f, country: v })} options={countries} />
          <SelectField label={t("common.timezone")} value={f.timezone} onChange={(v) => setF({ ...f, timezone: v })} options={zones} />
        </div>
        <div className="grid-2">
          <SelectField label={t("settings.defaultLanguage")} value={f.default_language} onChange={(v) => setF({ ...f, default_language: v as "en" | "ar" })}
            options={[{ value: "en", label: "English" }, { value: "ar", label: "العربية" }]} />
        </div>
        <div><Button type="submit" variant="primary" busy={s.busy}>{t("common.save")}</Button></div>
      </div>
    </form>
  );
}

function FeaturesTab({ data, onSaved }: { data: SettingsData; onSaved: (d: SettingsData) => void }) {
  const { t, tx } = useI18n();
  const [f, setF] = useState({ ...data.features });
  const s = useSaver(onSaved);
  const dirty = Object.keys(f).some((k) => f[k] !== data.features[k]);
  const groups = ["modules", "workflow", "ai"];
  return (
    <div className="stack">
      <div className="row wrap">
        <p className="muted" style={{ flex: "1 1 320px" }}>{t("settings.features.lead")}</p>
      </div>
      {s.general && <Notice tone="danger">{s.general}</Notice>}
      {groups.map((g) => (
        <section className="panel" key={g} aria-labelledby={`fg-${g}`}>
          <div className="panel-head"><h3 id={`fg-${g}`}>{tx(`settings.group.${g}`, g)}</h3></div>
          {Object.keys(f).filter((k) => data.feature_groups[k] === g).map((k) => (
            <div className="feature" key={k}>
              <div className="text"><strong>{tx(`feature.${k}`, k)}</strong><span>{tx(`feature.${k}.d`, "")}</span></div>
              <Switch label={tx(`feature.${k}`, k)} checked={f[k]} onChange={(v) => setF({ ...f, [k]: v })} />
            </div>
          ))}
        </section>
      ))}
      <div className="row">
        <Button variant="primary" busy={s.busy} disabled={!dirty} onClick={() => s.run(() => put("/api/settings/features", { features: f }))}>{t("common.save")}</Button>
        {dirty && <Button variant="ghost" onClick={() => setF({ ...data.features })}>{t("common.cancel")}</Button>}
      </div>
    </div>
  );
}

function TermsTab({ data, onSaved }: { data: SettingsData; onSaved: (d: SettingsData) => void }) {
  const { t, tx } = useI18n();
  const keys = Object.keys(data.terminology_defaults);
  const initial = () => Object.fromEntries(keys.map((k) => [k, {
    en: data.terminology[k].en === data.terminology_defaults[k].en ? "" : data.terminology[k].en,
    ar: data.terminology[k].ar === data.terminology_defaults[k].ar ? "" : data.terminology[k].ar,
  }]));
  const [f, setF] = useState<Record<string, { en: string; ar: string }>>(initial);
  useEffect(() => { setF(initial()); }, [data]); // eslint-disable-line react-hooks/exhaustive-deps
  const s = useSaver(onSaved);
  return (
    <div className="stack">
      <p className="muted">{t("settings.terms.lead")}</p>
      {s.general && <Notice tone="danger">{s.general}</Notice>}
      <div className="panel table-wrap">
        <table className="table">
          <thead><tr><th>{t("settings.terms.term")}</th><th>English</th><th>العربية</th></tr></thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k}>
                <td className="cell-main">{tx(`term.${k}`, k)}</td>
                <td><input className="input" dir="ltr" lang="en" placeholder={data.terminology_defaults[k].en} value={f[k].en} maxLength={60}
                  aria-label={`${tx(`term.${k}`, k)} — English`} onChange={(e) => setF({ ...f, [k]: { ...f[k], en: e.target.value } })} /></td>
                <td><input className="input" dir="rtl" lang="ar" placeholder={data.terminology_defaults[k].ar} value={f[k].ar} maxLength={60}
                  aria-label={`${tx(`term.${k}`, k)} — العربية`} onChange={(e) => setF({ ...f, [k]: { ...f[k], ar: e.target.value } })} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div><Button variant="primary" busy={s.busy} onClick={() => s.run(() => put("/api/settings/terminology", { terminology: f }))}>{t("common.save")}</Button></div>
    </div>
  );
}

function SecurityTab({ data, onSaved }: { data: SettingsData; onSaved: (d: SettingsData) => void }) {
  const { t } = useI18n();
  const [f, setF] = useState<Policy>({ ...data.security });
  const s = useSaver(onSaved);
  const num = (k: keyof Policy, hint?: StringKey) => {
    const [lo, hi] = data.security_limits[k];
    return (
      <TextField key={k} label={t(`sec.${k}` as StringKey)} type="number" inputMode="numeric" min={lo} max={hi}
        value={String(f[k])} onChange={(v) => setF({ ...f, [k]: v === "" ? 0 : Number(v) })}
        hint={(hint ? t(hint) + " " : "") + t("sec.range", { lo, hi })} error={s.errs[k]} />
    );
  };
  const flag = (k: keyof Policy) => (
    <div className="feature" key={k} style={{ padding: "10px 0" }}>
      <div className="text"><strong style={{ fontWeight: 500 }}>{t(`sec.${k}` as StringKey)}</strong></div>
      <Switch label={t(`sec.${k}` as StringKey)} checked={f[k] as boolean} onChange={(v) => setF({ ...f, [k]: v })} />
    </div>
  );
  return (
    <form className="stack" onSubmit={(e) => { e.preventDefault(); s.run(() => put("/api/settings/security", f)); }}>
      <p className="muted">{t("settings.security.lead")}</p>
      {s.general && <Notice tone="danger">{s.general}</Notice>}
      <div className="grid-2" style={{ alignItems: "start" }}>
        <section className="panel" aria-labelledby="sec-pw">
          <div className="panel-head"><h3 id="sec-pw">{t("settings.security.passwords")}</h3></div>
          <div className="panel-body stack">
            {num("min_length")}
            <div>{flag("require_upper")}{flag("require_lower")}{flag("require_digit")}{flag("require_symbol")}</div>
            {num("expiry_days", "sec.expiry_days.h")}
            {num("history")}
          </div>
        </section>
        <section className="panel" aria-labelledby="sec-si">
          <div className="panel-head"><h3 id="sec-si">{t("settings.security.signin")}</h3></div>
          <div className="panel-body stack">
            {num("max_failed_attempts")}
            {num("lockout_minutes")}
            {num("session_timeout_minutes")}
          </div>
        </section>
      </div>
      <div><Button type="submit" variant="primary" busy={s.busy}>{t("common.save")}</Button></div>
    </form>
  );
}

function AiTab({ data, onSaved }: { data: SettingsData; onSaved: (d: SettingsData) => void }) {
  const { t } = useI18n();
  const s = useSaver(onSaved);
  const [f, setF] = useState({ ...data.ai });
  type TestRow = { role: string; model: string; ok: boolean; ms?: number; error?: string };
  const [tests, setTests] = useState<Record<string, TestRow[]>>({});
  const [testing, setTesting] = useState<string | null>(null);
  const runTest = async (name: string) => {
    setTesting(name);
    try { const r = await post<{ results: TestRow[] }>("/api/settings/ai/test", { provider: name }); setTests((x) => ({ ...x, [name]: r.results })); }
    catch (e) { setTests((x) => ({ ...x, [name]: [{ role: "text", model: "—", ok: false, error: errorText(e, t) }] })); }
    finally { setTesting(null); }
  };
  const providers = [{ value: "", label: t("ai.firstAvailable") }, ...data.ai_providers.map((p) => ({ value: p.name, label: p.label }))];
  return (
    <form className="stack" onSubmit={(e) => { e.preventDefault(); s.run(() => put("/api/settings/ai", { provider: f.provider || null, daily_limit: Number(f.daily_limit) })); }}>
      <p className="muted">{t("ai.lead")}</p>
      {s.general && <Notice tone="danger">{s.general}</Notice>}
      <section className="panel"><div className="panel-body stack" style={{ maxWidth: 640 }}>
        {data.ai_providers.length === 0 ? <Notice tone="warn">{t("ai.noProviders")}</Notice> : (
          <>
            <SelectField label={t("ai.provider")} value={f.provider ?? ""} options={providers} error={s.errs.provider}
              onChange={(v) => setF({ ...f, provider: v || null })} />
            <ul className="plain small muted">
              {data.ai_providers.map((p) => (
                <li key={p.name}>
                  <strong>{p.label}</strong>: {t("ai.models", { vision: p.vision_model, text: p.text_model })}{" "}
                  <button type="button" className="link-btn" disabled={testing === p.name} onClick={() => runTest(p.name)}>
                    {testing === p.name ? t("ai.testing") : t("ai.test")}
                  </button>
                  {tests[p.name] && (
                    <ul className="plain ai-test">
                      {tests[p.name].map((r) => (
                        <li key={r.role} className={r.ok ? "ok" : "bad"}>
                          {r.ok ? "✓" : "✕"} {t(r.role === "vision" ? "ai.testVision" : "ai.testText")} · <code>{r.model}</code>
                          {r.ok ? ` · ${r.ms} ms` : <div className="small">{r.error}</div>}
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
        <TextField label={t("ai.dailyLimit")} hint={t("ai.dailyLimitHint")} type="number" min={0} max={5000} value={String(f.daily_limit)}
          onChange={(v) => setF({ ...f, daily_limit: Number(v || 0) })} error={s.errs.daily_limit} />
        <div><Button type="submit" variant="primary" busy={s.busy}>{t("common.save")}</Button></div>
      </div></section>
    </form>
  );
}

function AppsTab() {
  const { t, dateTime } = useI18n();
  const keys = useLoad(() => get<{ items: { id: string; name: string; prefix: string; last_used_at: string | null; revoked_at: string | null; created_at: string }[] }>("/api/integration/keys"));
  const [name, setName] = useState("");
  const [shown, setShown] = useState<{ name: string; key: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="stack">
      <p className="muted">{t("apps.lead")}</p>
      {err && <Notice tone="danger">{err}</Notice>}
      <form className="row wrap" onSubmit={async (e) => {
        e.preventDefault(); setErr(null);
        try { const r = await post<{ key: string; name: string }>("/api/integration/keys", { name }); setShown({ name: r.name, key: r.key }); setName(""); keys.reload(); }
        catch (ex) { setErr(errorText(ex, t)); }
      }}>
        <input className="input" style={{ maxWidth: 280 }} placeholder={t("apps.namePh")} aria-label={t("apps.name")} value={name} onChange={(e) => setName(e.target.value)} />
        <Button type="submit" variant="primary" disabled={!name.trim()}>{t("apps.new")}</Button>
      </form>
      {shown && (
        <Dialog title={t("apps.keyTitle", { name: shown.name })} onClose={() => setShown(null)}
          footer={<Button variant="primary" onClick={() => setShown(null)}>{t("secret.done")}</Button>}>
          <p>{t("apps.keyBody")}</p>
          <div className="secret"><code>{shown.key}</code>
            <Button size="sm" onClick={() => { navigator.clipboard?.writeText(shown.key).catch(() => {}); }}>{t("common.copy")}</Button></div>
        </Dialog>
      )}
      <div className="panel table-wrap">
        <table className="table">
          <thead><tr><th>{t("apps.name")}</th><th>{t("apps.prefix")}</th><th>{t("apps.lastUsed")}</th><th>{t("common.status")}</th><th /></tr></thead>
          <tbody>
            {(keys.data?.items ?? []).length === 0 && <tr><td colSpan={5} className="muted small">{t("apps.empty")}</td></tr>}
            {(keys.data?.items ?? []).map((k) => (
              <tr key={k.id}>
                <td className="cell-main">{k.name}</td><td className="mono-id">{k.prefix}…</td>
                <td className="small">{k.last_used_at ? dateTime(k.last_used_at) : t("common.never")}</td>
                <td>{k.revoked_at ? <Badge>{t("apps.revoked")}</Badge> : <Badge tone="ok" dot>{t("common.active")}</Badge>}</td>
                <td>{!k.revoked_at && <Button size="sm" variant="ghost" onClick={async () => { await post(`/api/integration/keys/${k.id}/revoke`); keys.reload(); }}>{t("apps.revoke")}</Button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="small muted">
        <strong>{t("apps.endpoints")}:</strong> <code>GET /api/integration/v1/assets</code> · <code>GET /api/integration/v1/events?after=&lt;id&gt;</code> (Authorization: Bearer …)
      </div>
    </div>
  );
}

function DemoTab() {
  const { t } = useI18n();
  return (
    <div className="stack">
      <p className="muted">{t("demoTab.lead")}</p>
      <div><Button variant="primary" onClick={() => navigate("/?demo=1")}>{t("ov.loadDemo")}</Button></div>
    </div>
  );
}

export function Settings() {
  const { t, tx } = useI18n();
  const { me } = useSession();
  const { data, error, reload, setData } = useLoad(() => get<SettingsData>("/api/settings"));
  const initialTab = new URLSearchParams(window.location.search).get("tab") as Tab | null;
  const [tab, setTabState] = useState<Tab>(initialTab && TABS.includes(initialTab) ? initialTab : "company");
  const setTab = (x: Tab) => { setTabState(x); navigate(`/settings?tab=${x}`, true); };
  const labels: Record<Tab, StringKey> = { company: "settings.tab.company", features: "settings.tab.features", ai: "settings.tab.ai", terms: "settings.tab.terms", security: "settings.tab.security", apps: "settings.tab.apps", demo: "settings.tab.demo" };
  return (
    <>
      <PageHead title={t("settings.title")} lead={t("settings.lead", { company: me?.tenant?.name ?? "" })} />
      <div className="tabs" role="tablist">
        {TABS.map((x) => (
          <button key={x} type="button" role="tab" className="tab" aria-selected={tab === x} onClick={() => setTab(x)}>{tx(labels[x], x)}</button>
        ))}
      </div>
      {error ? <div className="panel"><LoadError error={error} onRetry={reload} /></div> : !data ? (
        <div className="panel"><div className="panel-body"><div className="skeleton" style={{ width: "40%" }} /></div></div>
      ) : (
        <div role="tabpanel">
          {tab === "company" && <CompanyTab data={data} onSaved={setData} />}
          {tab === "features" && <FeaturesTab key={JSON.stringify(data.features)} data={data} onSaved={setData} />}
          {tab === "terms" && <TermsTab data={data} onSaved={setData} />}
          {tab === "security" && <SecurityTab key={JSON.stringify(data.security)} data={data} onSaved={setData} />}
          {tab === "ai" && <AiTab key={JSON.stringify(data.ai)} data={data} onSaved={setData} />}
          {tab === "apps" && <AppsTab />}
          {tab === "demo" && <DemoTab />}
        </div>
      )}
    </>
  );
}
