// The annual calibration / maintenance plan of a site: a year grid of every due date, reviewed by engineering and
// approved by QA with e-signatures. With plan approval switched on, work is released only from an approved plan.
import { useEffect, useMemo, useState } from "react";
import { LuCalendarRange, LuChevronLeft, LuChevronRight, LuPrinter, LuSignature } from "react-icons/lu";
import { Badge, Button, Empty, LoadError, Notice, PageHead, useLoad } from "../components/ui";
import { PrintFormButton } from "../components/forms";
import { SignDialog, useDay, useEvery } from "../components/work";
import { get, post, qs } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link, navigate } from "../lib/router";
import { useSession } from "../lib/session";
import type { StringKey } from "../lib/strings";

interface Entry { task_id?: string; number?: string; due_date: string; status: string; verdict?: string | null; overdue?: boolean; created_at?: string }
interface PlanRow {
  schedule_id: string | null; asset_id: string; asset_code: string; asset_name_en: string; asset_name_ar: string | null; location_code: string | null;
  active: boolean; interval_value: number | null; interval_unit: string | null; template_code: string | null; template_title: string | null;
  performer: string | null; vendor_name: string | null; entries: Entry[];
}
interface SigInfo { user_name: string; user_email: string; signed_at: string; statement: string; record_hash: string }
interface PlanView {
  plan: null | { id: string; status: string; reviewed_at: string | null; reviewed_by_name: string | null; approved_at: string | null;
    approved_by_name: string | null; reviewed_signature: SigInfo | null; approved_signature: SigInfo | null; source: string };
  status: "draft" | "reviewed" | "approved"; rows: PlanRow[]; count: number; hash: string; changed_since_signed: boolean; gating: boolean;
  can_review: boolean; can_approve: boolean; site_id: string; kind: string; year: number;
}

export function PlanPage({ kind }: { kind: "calibration" | "pm" }) {
  const { t, lang, month, dateTime } = useI18n();
  const { me, siteId: currentSite } = useSession();
  const day = useDay();
  const every = useEvery();
  const p0 = new URLSearchParams(window.location.search);
  const [site, setSite] = useState(p0.get("site_id") || currentSite || me?.sites[0]?.id || "");
  const [year, setYear] = useState(Number(p0.get("year")) || new Date().getFullYear());
  const [q, setQ] = useState("");
  const [signing, setSigning] = useState<null | "review" | "approve">(null);
  const v = useLoad(() => get<PlanView>(`/api/plans${qs({ site_id: site, kind, year })}`), [site, kind, year]);
  useEffect(() => { navigate(`/plans/${kind}${qs({ site_id: site, year })}`, true); }, [site, year, kind]);
  const base = kind === "pm" ? "/maintenance" : "/calibration";
  const rows = useMemo(() => (v.data?.rows ?? []).filter((r) => !q || `${r.asset_code} ${r.asset_name_en} ${r.template_code ?? ""}`.toLowerCase().includes(q.toLowerCase())), [v.data, q]);
  const approvedAt = v.data?.plan?.approved_at;
  const statusTone = { draft: undefined, reviewed: "warn", approved: "ok" } as const;
  const sign = (which: "review" | "approve") => async (s: { email: string; password: string; reason: string }) => {
    v.setData(await post<PlanView>(`/api/plans/${which}`, { site_id: site, kind, year, ...s, reason: s.reason || null }));
  };
  return (
    <>
      <PageHead title={t(`plan.title.${kind}` as StringKey)} lead={<>{t("plan.lead")}{v.data?.gating ? ` ${t("plan.leadGate")}` : ""}</>} actions={<>
        <Button icon={<LuPrinter />} onClick={() => window.open(`/api/plans/pdf${qs({ site_id: site, kind, year })}`, "_blank", "noopener")}>{t("plan.pdf")}</Button>
        <PrintFormButton source={kind === "pm" ? "pm_plan" : "calibration_plan"} params={{ site_id: site, year }} label={t("fm.printPlan")} />
        {v.data?.can_review && (v.data.status === "draft" || v.data.changed_since_signed) && <Button icon={<LuSignature />} onClick={() => setSigning("review")}>{t("plan.review")}</Button>}
        {v.data?.can_approve && <Button variant="primary" icon={<LuSignature />} onClick={() => setSigning("approve")}>{t("plan.approve")}</Button>}
      </>} />
      <div className="panel">
        <div className="toolbar cm-filters">
          {(me?.sites.length ?? 0) > 1 && (
            <select className="select" aria-label={t("assets.filter.site")} value={site} onChange={(e) => setSite(e.target.value)}>
              {me!.sites.map((s) => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}
            </select>
          )}
          <div className="row" style={{ gap: 4 }}>
            <Button size="sm" variant="ghost" iconOnly aria-label={String(year - 1)} icon={<LuChevronLeft className="flip-rtl" />} onClick={() => setYear(year - 1)} />
            <strong className="num" aria-label={t("plan.year")}>{year}</strong>
            <Button size="sm" variant="ghost" iconOnly aria-label={String(year + 1)} icon={<LuChevronRight className="flip-rtl" />} onClick={() => setYear(year + 1)} />
          </div>
          {v.data && <Badge tone={statusTone[v.data.status]} dot>{t(`plan.status.${v.data.status}` as StringKey)}</Badge>}
          <input className="input" type="search" placeholder={t("plan.search")} aria-label={t("plan.search")} value={q} onChange={(e) => setQ(e.target.value)} />
          <span className="spacer" />
          {v.data && <span className="small muted">{t("plan.rows", { n: v.data.rows.length })} · {t("plan.count", { n: v.data.count })}</span>}
        </div>
        {v.data?.plan && <div className="panel-body small stack" style={{ gap: 4 }}>
        {v.data?.plan?.reviewed_signature && <div><LuSignature aria-hidden /> {t("plan.reviewedBy", { name: v.data.plan.reviewed_signature.user_name, date: dateTime(v.data.plan.reviewed_signature.signed_at) })}
          <span className="subtle mono-id"> · SHA-256 {v.data.plan.reviewed_signature.record_hash.slice(0, 16)}…</span></div>}
        {v.data?.plan && !v.data.plan.reviewed_signature && v.data.plan.reviewed_by_name && <div><LuSignature aria-hidden /> {t("plan.reviewedBy", { name: v.data.plan.reviewed_by_name, date: dateTime(v.data.plan.reviewed_at) })}</div>}
        {v.data?.plan?.approved_by_name && <div><LuSignature aria-hidden /> {t("plan.approvedBy", { name: v.data.plan.approved_by_name, date: dateTime(v.data.plan.approved_at) })}
          {v.data.plan.approved_signature && <span className="subtle mono-id"> · SHA-256 {v.data.plan.approved_signature.record_hash.slice(0, 16)}…</span>}</div>}
        </div>}
        {v.data?.changed_since_signed && v.data.status !== "approved" && <div className="panel-body"><Notice tone="warn">{t("plan.changed")}</Notice></div>}
        {v.error ? <LoadError error={v.error} onRetry={v.reload} /> : !v.data ? <div className="panel-body"><div className="skeleton" style={{ width: "60%" }} /></div>
          : v.data.rows.length === 0 ? <Empty icon={<LuCalendarRange />} title={t("plan.empty")} body={t("plan.emptyBody")} /> : (
            <div className="table-wrap">
              <table className="table cm-plan">
                <thead><tr>
                  <th>{t("plan.asset")}</th>
                  {Array.from({ length: 12 }, (_, m) => <th key={m} className="cm-plan-m">{month(`${year}-${String(m + 1).padStart(2, "0")}`)}</th>)}
                </tr></thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={`${r.schedule_id}-${i}`} className={r.active ? "" : "ended"}>
                      <td className="cm-plan-asset">
                        <Link to={`/assets/${r.asset_id}`} className="mono-id cell-main">{r.asset_code}</Link>
                        <div className="small subtle ellipsis">{lang === "ar" && r.asset_name_ar ? r.asset_name_ar : r.asset_name_en}</div>
                        <div className="small subtle">{[r.template_code, r.interval_value ? every(r.interval_value, r.interval_unit ?? "month") : null, r.vendor_name].filter(Boolean).join(" · ")}</div>
                      </td>
                      {Array.from({ length: 12 }, (_, m) => {
                        const es = r.entries.filter((e) => Number(e.due_date.slice(5, 7)) === m + 1);
                        return (
                          <td key={m} className="cm-plan-cell">
                            {es.map((e, k) => {
                              const cls = e.status === "completed" ? (e.verdict === "pass" ? "done" : "done warn") : e.overdue ? "late" : e.status === "planned" ? "planned" : "open";
                              const added = approvedAt && e.created_at && e.created_at > approvedAt && e.status !== "planned";
                              const label = `${day(e.due_date)}${e.number ? ` · ${e.number}` : ""}`;
                              return e.task_id
                                ? <Link key={k} to={`${base}/tasks/${e.task_id}`} className={`cm-plan-chip ${cls} ${added ? "added" : ""}`} title={label}>{Number(e.due_date.slice(8, 10))}</Link>
                                : <span key={k} className={`cm-plan-chip ${cls}`} title={label}>{Number(e.due_date.slice(8, 10))}</span>;
                            })}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        <div className="panel-body cm-plan-legend small">
          <span><i className="cm-plan-chip done" />{t("plan.legend.done")}</span>
          <span><i className="cm-plan-chip open" />{t("plan.legend.open")}</span>
          <span><i className="cm-plan-chip late" />{t("plan.legend.late")}</span>
          <span><i className="cm-plan-chip planned" />{t("plan.legend.planned")}</span>
          {approvedAt && <span><i className="cm-plan-chip open added" />{t("plan.legend.added")}</span>}
        </div>
      </div>
      {signing && v.data && (
        <SignDialog title={`${signing === "review" ? t("plan.review") : t("plan.approve")} · ${t(`plan.title.${kind}` as StringKey)} ${year}`}
          lead={t("plan.count", { n: v.data.count })} meaning={signing === "review" ? "plan_reviewed" : "plan_approved"}
          onClose={() => setSigning(null)} onSign={sign(signing)} />
      )}
    </>
  );
}
