// Deviations raised by escalation (level 3), investigated and closed by QA with an e-signature.
import { useEffect, useState } from "react";
import { LuArrowLeft, LuOctagonAlert, LuSave, LuSignature } from "react-icons/lu";
import { Badge, Button, Empty, LoadError, LoadingRows, Notice, PageHead, errorText, useLoad, useToast } from "../components/ui";
import { SignDialog } from "../components/work";
import { get, patch, post } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link } from "../lib/router";
import type { StringKey } from "../lib/strings";

interface Dev {
  id: string; number: string; title: string; description: string | null; status: "open" | "closed"; root_cause: string | null; actions: string | null;
  asset_id: string; asset_code: string; asset_name_en: string; site_code: string; task_id: string | null; task_number: string | null;
  task_kind: string | null; task_status: string | null; created_at: string; closed_at: string | null; closed_by_name: string | null;
  can_edit?: boolean; can_close?: boolean; escalations?: { level: number; trigger: string; created_at: string; recipients: number }[];
  signature?: { user_name: string; user_email: string; signed_at: string; statement: string; record_hash: string } | null;
}

export function Deviations() {
  const { t, dateTime } = useI18n();
  const [status, setStatus] = useState("open");
  const list = useLoad(() => get<{ items: Dev[] }>(`/api/deviations${status ? `?status=${status}` : ""}`), [status]);
  return (
    <>
      <PageHead title={t("dev.title")} lead={t("dev.lead")} />
      <div className="panel">
        <div className="toolbar">
          <div className="seg" role="group" aria-label={t("cw.col.state")}>
            {["open", "closed", ""].map((s) => <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}>
              {s ? t(`dev.status.${s}` as StringKey) : t("common.all")}</button>)}
          </div>
        </div>
        {list.error ? <LoadError error={list.error} onRetry={list.reload} /> : list.data && !list.data.items.length ? (
          <Empty icon={<LuOctagonAlert />} title={t("dev.empty")} body={t("dev.emptyBody")} />
        ) : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>{t("dev.number")}</th><th>{t("tpl.titleField")}</th><th>{t("cw.col.asset")}</th><th>{t("dev.task")}</th><th>{t("cw.col.state")}</th></tr></thead>
            {!list.data ? <LoadingRows rows={4} cols={5} /> : (
              <tbody>{list.data.items.map((d) => (
                <tr key={d.id}>
                  <td className="nowrap"><Link to={`/deviations/${d.id}`} className="mono-id cell-main">{d.number}</Link><div className="small subtle">{dateTime(d.created_at)}</div></td>
                  <td>{d.title}</td>
                  <td><Link to={`/assets/${d.asset_id}`} className="mono-id">{d.asset_code}</Link><div className="small subtle">{d.site_code}</div></td>
                  <td>{d.task_id && <Link to={`/${d.task_kind === "pm" ? "maintenance" : "calibration"}/tasks/${d.task_id}`} className="mono-id">{d.task_number}</Link>}</td>
                  <td><Badge tone={d.status === "open" ? "danger" : "ok"} dot>{t(`dev.status.${d.status}` as StringKey)}</Badge></td>
                </tr>))}</tbody>
            )}
          </table></div>
        )}
      </div>
    </>
  );
}

export function DeviationPage({ id }: { id: string }) {
  const { t, dateTime } = useI18n();
  const toast = useToast();
  const { data: d, error, reload, setData } = useLoad(() => get<Dev>(`/api/deviations/${id}`), [id]);
  const [f, setF] = useState({ description: "", root_cause: "", actions: "" });
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [signing, setSigning] = useState(false);
  useEffect(() => { if (d) { setF({ description: d.description ?? "", root_cause: d.root_cause ?? "", actions: d.actions ?? "" }); setDirty(false); } }, [d]);
  if (error) return <div className="panel"><LoadError error={error} onRetry={reload} /></div>;
  if (!d) return <div className="panel"><div className="panel-body"><div className="skeleton" style={{ width: "40%" }} /></div></div>;
  const save = async () => {
    setBusy(true); setErr(null);
    try { setData(await patch<Dev>(`/api/deviations/${d.id}`, f)); toast(t("list.saved")); } catch (e) { setErr(errorText(e, t)); throw e; } finally { setBusy(false); }
  };
  const field = (k: keyof typeof f, label: string) => (
    <div className="field" key={k}>
      <label htmlFor={`dev-${k}`}>{label}</label>
      <textarea id={`dev-${k}`} className="textarea" rows={4} value={f[k]} disabled={!d.can_edit} maxLength={8000}
        onChange={(e) => { setF({ ...f, [k]: e.target.value }); setDirty(true); }} />
    </div>
  );
  return (
    <>
      <Link to="/deviations" className="back-link"><LuArrowLeft className="flip-rtl" aria-hidden /> {t("dev.title")}</Link>
      <div className="page-head">
        <div className="titles">
          <div className="row" style={{ gap: 8 }}><span className="mono-id cm-code">{d.number}</span>
            <Badge tone={d.status === "open" ? "danger" : "ok"} dot>{t(`dev.status.${d.status}` as StringKey)}</Badge></div>
          <h1>{d.title}</h1>
          <p className="small muted"><Link to={`/assets/${d.asset_id}`} className="mono-id">{d.asset_code}</Link> · {d.asset_name_en} · {d.site_code}
            {d.task_id && <> · <Link to={`/${d.task_kind === "pm" ? "maintenance" : "calibration"}/tasks/${d.task_id}`} className="mono-id">{d.task_number}</Link></>}
            {" · "}{dateTime(d.created_at)}</p>
        </div>
        <div className="actions row wrap">
          {d.can_edit && <Button icon={<LuSave />} busy={busy} disabled={!dirty} onClick={() => save().catch(() => {})}>{t("ia.save")}</Button>}
          {d.can_close && <Button variant="primary" icon={<LuSignature />} onClick={() => setSigning(true)}>{t("dev.close")}</Button>}
        </div>
      </div>
      {err && <Notice tone="danger">{err}</Notice>}
      {d.signature && <div className="sign-record small"><strong><LuSignature aria-hidden /> {t("dev.closedBy", { name: d.signature.user_name, date: dateTime(d.signature.signed_at) })}</strong>
        <div className="subtle">“{d.signature.statement}”</div><div className="subtle mono-id">SHA-256 {d.signature.record_hash}</div></div>}
      <div className="cm-task-grid">
        <section className="panel"><div className="panel-body stack">
          {field("description", t("dev.description"))}
          {field("root_cause", t("dev.rootCause"))}
          {field("actions", t("dev.actions"))}
        </div></section>
        <aside className="stack">
          {!!d.escalations?.length && <section className="panel">
            <div className="panel-head"><h3>{t("esc.title")}</h3></div>
            <ul className="cm-prev">{d.escalations.map((e, i) => (
              <li key={i}><Badge tone={e.level >= 2 ? "danger" : "warn"}>{t("nt.level", { n: e.level })}</Badge>
                <span className="grow small">{t(`esc.${e.trigger}` as StringKey)}</span><span className="small subtle">{dateTime(e.created_at)}</span></li>))}</ul>
          </section>}
        </aside>
      </div>
      {signing && <SignDialog title={`${t("dev.close")} · ${d.number}`} meaning="deviation_closed" onClose={() => setSigning(false)}
        onSign={async (v) => { if (dirty) await save(); setData(await post<Dev>(`/api/deviations/${d.id}/close`, { ...v, reason: v.reason || null })); }} />}
    </>
  );
}
