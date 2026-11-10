// Impact assessments for out-of-tolerance calibrations: list, edit (with an AI first draft) and QA approval.
import { useEffect, useState } from "react";
import { LuArrowLeft, LuSave, LuShieldAlert, LuSignature, LuSparkles } from "react-icons/lu";
import { Badge, Button, Empty, LoadError, LoadingRows, Notice, PageHead, errorText, useLoad, useToast } from "../components/ui";
import { SignDialog, VerdictBadge, useDay, type Verdict } from "../components/work";
import { get, patch, post } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link } from "../lib/router";
import { useSession } from "../lib/session";
import type { StringKey } from "../lib/strings";
import { nameOf } from "../lib/types";

interface ImpactRow {
  id: string; number: string; status: "draft" | "approved"; period_from: string | null; period_to: string; risk: "low" | "medium" | "high" | null;
  asset_id: string; asset_code: string; asset_name_en: string; asset_name_ar: string | null; task_id: string; task_number: string;
  verdict: Verdict; performed_date: string; site_code: string; approved_by_name: string | null; approved_at: string | null; created_at: string;
}
const FIELDS = ["summary", "scope", "products", "actions", "conclusion"] as const;
type FieldKey = (typeof FIELDS)[number];
interface Impact extends ImpactRow {
  summary: string | null; scope: string | null; products: string | null; actions: string | null; conclusion: string | null;
  results: { seq: number; parameter: string; nominal: number | null; unit: string | null; low_limit: number | null; high_limit: number | null;
    as_found: number | null; as_left: number | null; ok_found: boolean | null; ok_left: boolean | null }[];
  asset: { id: string; code: string; name_en: string; name_ar: string | null; location_code: string | null; line_code: string | null; parent_code: string | null };
  signature: { user_name: string; user_email: string; signed_at: string; statement: string; record_hash: string } | null;
  ai: { id: string; model: string; changed_fields: Record<string, unknown> | null; reviewed_by_name: string | null } | null;
  can_edit: boolean; can_approve: boolean;
}

const RISK_TONE = { low: "ok", medium: "warn", high: "danger" } as const;

export function Impacts() {
  const { t, lang } = useI18n();
  const day = useDay();
  const [status, setStatus] = useState("");
  const list = useLoad(() => get<{ items: ImpactRow[] }>(`/api/impacts${status ? `?status=${status}` : ""}`), [status]);
  return (
    <>
      <PageHead title={t("ia.title")} lead={t("ia.lead")} />
      <div className="panel">
        <div className="toolbar">
          <div className="seg" role="group" aria-label={t("cw.col.state")}>
            {["", "draft", "approved"].map((s) => (
              <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}>{s ? t(`ia.status.${s}` as StringKey) : t("common.all")}</button>
            ))}
          </div>
        </div>
        {list.error ? <LoadError error={list.error} onRetry={list.reload} /> : list.data && list.data.items.length === 0 ? (
          <Empty icon={<LuShieldAlert />} title={t("ia.empty")} body={t("ia.emptyBody")} />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>{t("ia.number")}</th><th>{t("cw.col.asset")}</th><th>{t("cw.col.task")}</th><th>{t("ia.period")}</th><th>{t("ia.risk")}</th><th>{t("cw.col.state")}</th></tr></thead>
              {!list.data ? <LoadingRows rows={4} cols={6} /> : (
                <tbody>
                  {list.data.items.map((x) => (
                    <tr key={x.id}>
                      <td><Link to={`/calibration/impacts/${x.id}`} className="mono-id cell-main">{x.number}</Link></td>
                      <td><Link to={`/assets/${x.asset_id}`} className="mono-id">{x.asset_code}</Link>
                        <div className="small subtle">{lang === "ar" && x.asset_name_ar ? x.asset_name_ar : x.asset_name_en} · {x.site_code}</div></td>
                      <td className="nowrap"><Link to={`/calibration/tasks/${x.task_id}`} className="mono-id">{x.task_number}</Link> <VerdictBadge verdict={x.verdict} /></td>
                      <td className="small nowrap">{x.period_from ? day(x.period_from) : t("ia.unknown")} – {day(x.period_to)}</td>
                      <td>{x.risk ? <Badge tone={RISK_TONE[x.risk]}>{t(`ia.risk.${x.risk}` as StringKey)}</Badge> : <span className="subtle">—</span>}</td>
                      <td><Badge tone={x.status === "approved" ? "ok" : "warn"} dot>{t(`ia.status.${x.status}` as StringKey)}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              )}
            </table>
          </div>
        )}
      </div>
    </>
  );
}

export function ImpactPage({ impactId }: { impactId: string }) {
  const { t, lang, qty, dateTime } = useI18n();
  const { me } = useSession();
  const toast = useToast();
  const day = useDay();
  const { data: x, error, reload, setData } = useLoad(() => get<Impact>(`/api/impacts/${impactId}`), [impactId]);
  const [f, setF] = useState<Record<FieldKey, string>>({ summary: "", scope: "", products: "", actions: "", conclusion: "" });
  const [risk, setRisk] = useState<string>("");
  const [periodFrom, setPeriodFrom] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [aiId, setAiId] = useState<string | null>(null);
  const [aiReason, setAiReason] = useState<string | null>(null);
  const [signing, setSigning] = useState(false);
  useEffect(() => {
    if (!x) return;
    setF({ summary: x.summary ?? "", scope: x.scope ?? "", products: x.products ?? "", actions: x.actions ?? "", conclusion: x.conclusion ?? "" });
    setRisk(x.risk ?? ""); setPeriodFrom(x.period_from ?? ""); setDirty(false);
  }, [x]);
  if (error) return <div className="panel"><LoadError error={error} onRetry={reload} /></div>;
  if (!x) return <div className="panel"><div className="panel-body"><div className="skeleton" style={{ width: "40%" }} /></div></div>;
  const edit = x.can_edit;
  const save = async () => {
    setBusy("save"); setErr(null);
    try {
      const out = await patch<Impact>(`/api/impacts/${x.id}`, { ...Object.fromEntries(FIELDS.map((k) => [k, f[k] || null])), risk: risk || null,
        period_from: periodFrom || null, ...(aiId ? { ai_suggestion_id: aiId } : {}) });
      setData(out); setAiId(null); toast(t("list.saved")); return out;
    } catch (e) { setErr(errorText(e, t)); throw e; } finally { setBusy(null); }
  };
  const draft = async () => {
    setBusy("ai"); setErr(null);
    try {
      const r = await post<{ suggestion: { id: string }; fields: Record<string, string | null>; risk_reason: string }>(`/api/impacts/${x.id}/ai-draft`);
      setF((cur) => Object.fromEntries(FIELDS.map((k) => [k, r.fields[k] || cur[k]])) as Record<FieldKey, string>);
      if (r.fields.risk) setRisk(r.fields.risk);
      setAiId(r.suggestion.id); setAiReason(r.risk_reason || null); setDirty(true);
      toast(t("ia.aiDone"));
    } catch (e) { setErr(errorText(e, t)); } finally { setBusy(null); }
  };
  const labels: Record<FieldKey, StringKey> = { summary: "ia.summary", scope: "ia.scope", products: "ia.products", actions: "ia.actions", conclusion: "ia.conclusion" };
  return (
    <>
      <Link to="/calibration/impacts" className="back-link"><LuArrowLeft className="flip-rtl" aria-hidden /> {t("ia.title")}</Link>
      <div className="page-head">
        <div className="titles">
          <div className="row wrap" style={{ gap: 8 }}>
            <span className="mono-id cm-code">{x.number}</span>
            <Badge tone={x.status === "approved" ? "ok" : "warn"} dot>{t(`ia.status.${x.status}` as StringKey)}</Badge>
            <VerdictBadge verdict={x.verdict} />
          </div>
          <h1><Link to={`/assets/${x.asset_id}`} className="mono-id">{x.asset.code}</Link> · {nameOf(x.asset, lang)}</h1>
          <p className="small muted">
            <Link to={`/calibration/tasks/${x.task_id}`} className="mono-id">{x.task_number}</Link> · {day(x.performed_date)}
            {x.asset.location_code && <> · {x.asset.location_code}</>}{x.asset.line_code && <> · {x.asset.line_code}</>}
            {x.asset.parent_code && <> · {t("assets.partOf", { code: x.asset.parent_code })}</>}
          </p>
        </div>
        <div className="actions row wrap">
          {edit && me?.tenant?.ai.impact && <Button icon={<LuSparkles />} busy={busy === "ai"} onClick={draft}>{busy === "ai" ? t("ia.aiBusy") : t("ia.aiDraft")}</Button>}
          {edit && <Button icon={<LuSave />} busy={busy === "save"} disabled={!dirty} onClick={() => save().catch(() => {})}>{t("ia.save")}</Button>}
          {x.can_approve && <Button variant="primary" icon={<LuSignature />} onClick={() => setSigning(true)}>{t("ia.approve")}</Button>}
        </div>
      </div>
      {err && <Notice tone="danger">{err}</Notice>}
      {x.signature && <div className="sign-record small"><strong><LuSignature aria-hidden /> {t("ia.approved", { name: x.signature.user_name, date: dateTime(x.signature.signed_at) })}</strong> &lt;{x.signature.user_email}&gt;
        <div className="subtle">“{x.signature.statement}”</div><div className="subtle mono-id">SHA-256 {x.signature.record_hash}</div></div>}

      <div className="cm-task-grid">
        <section className="panel">
          <div className="panel-body stack">
            <div className="grid-2">
              <div className="field">
                <label htmlFor="ia-from">{t("ia.periodFrom")}</label>
                <input id="ia-from" className="input" type="date" value={periodFrom} disabled={!edit} onChange={(e) => { setPeriodFrom(e.target.value); setDirty(true); }} />
              </div>
              <div className="field"><span className="field-label">{t("ia.periodTo")}</span><div className="num">{day(x.period_to)}</div></div>
            </div>
            <div className="field">
              <span className="field-label">{t("ia.risk")}</span>
              <div className="seg" role="group" aria-label={t("ia.risk")}>
                {(["low", "medium", "high"] as const).map((r) => (
                  <button key={r} type="button" disabled={!edit} aria-pressed={risk === r} onClick={() => { setRisk(r); setDirty(true); }}>{t(`ia.risk.${r}` as StringKey)}</button>
                ))}
              </div>
              {aiReason && <div className="small muted"><LuSparkles aria-hidden /> {t("ia.aiReason")}: {aiReason}</div>}
            </div>
            {FIELDS.map((k) => (
              <div className="field" key={k}>
                <label htmlFor={`ia-${k}`}>{t(labels[k])}</label>
                <textarea id={`ia-${k}`} className={`textarea ${aiId ? "ai-filled" : ""}`} rows={k === "actions" ? 6 : 4} value={f[k]} disabled={!edit} maxLength={8000}
                  onChange={(e) => { setF({ ...f, [k]: e.target.value }); setDirty(true); }} />
              </div>
            ))}
            {x.ai && <p className="small subtle"><LuSparkles aria-hidden /> {x.ai.model}{x.ai.reviewed_by_name ? ` · ${x.ai.reviewed_by_name}` : ""}
              {x.ai.changed_fields && Object.keys(x.ai.changed_fields).length ? ` · ${t("asset.ai.changed", { fields: Object.keys(x.ai.changed_fields).join(", ") })}` : ""}</p>}
          </div>
        </section>
        <aside className="stack">
          <section className="panel">
            <div className="panel-head"><h3>{t("ia.results")}</h3></div>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>{t("task.r.point")}</th><th className="num">{t("task.r.limits")}</th><th className="num">{t("task.r.found")}</th><th className="num">{t("task.r.left")}</th></tr></thead>
                <tbody>
                  {x.results.map((r) => (
                    <tr key={r.seq} className={r.ok_found === false ? "cm-row-bad" : ""}>
                      <td className="small">{r.parameter} {r.nominal !== null ? `${qty(r.nominal, 6)} ${r.unit ?? ""}` : ""}</td>
                      <td className="num small nowrap">{r.low_limit !== null ? qty(r.low_limit, 6) : "–∞"} … {r.high_limit !== null ? qty(r.high_limit, 6) : "∞"}</td>
                      <td className={`num ${r.ok_found === false ? "text-danger" : ""}`}>{r.as_found !== null ? qty(r.as_found, 6) : "–"}</td>
                      <td className={`num ${r.ok_left === false ? "text-danger" : ""}`}>{r.as_left !== null ? qty(r.as_left, 6) : "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </aside>
      </div>
      {signing && (
        <SignDialog title={`${t("ia.approve")} · ${x.number}`} meaning="impact_approved" onClose={() => setSigning(false)}
          onSign={async (v) => {
            if (dirty) await save();
            setData(await post<Impact>(`/api/impacts/${x.id}/approve`, { ...v, reason: v.reason || null }));
          }} />
      )}
    </>
  );
}

