// Calibration work list: KPIs, state tabs and the tasks with their tolerance windows.
import { useEffect, useState } from "react";
import { LuClipboardCheck, LuListChecks, LuShieldAlert } from "react-icons/lu";
import { CategoryIcon, Kpi } from "../components/cm";
import { Button, Empty, LoadError, LoadingRows, PageHead, useLoad } from "../components/ui";
import { DueWhen, TaskStateBadge, VerdictBadge, useDay, type TaskRow } from "../components/work";
import { get, qs } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link, navigate } from "../lib/router";
import { useSession } from "../lib/session";
import type { StringKey } from "../lib/strings";

const STATES = ["open", "mine", "overdue", "due_soon", "at_vendor", "in_progress", "to_review", "completed", "oot"] as const;
type State = (typeof STATES)[number];

interface Counts {
  open: number; overdue: number; due_soon: number; at_vendor: number; in_progress: number; completed_30: number;
  oot_12m: number; to_review: number; impacts_open: number;
}
interface ListOut { items: TaskRow[]; total: number; page: number; counts: Counts; today: string; due_soon_days: number }

function readParams() {
  const p = new URLSearchParams(window.location.search);
  const s = p.get("state") as State | null;
  return { state: s && (STATES as readonly string[]).includes(s) ? s : "open" as State, q: p.get("q") ?? "", site_id: p.get("site_id") ?? "",
    page: Number(p.get("page") ?? "1") || 1 };
}

export function CalibrationWork() {
  const { t, lang, number } = useI18n();
  const { me } = useSession();
  const day = useDay();
  const init = readParams();
  const [state, setState] = useState<State>(init.state);
  const [q, setQ] = useState(init.q);
  const [term, setTerm] = useState(init.q);
  const [site, setSite] = useState(init.site_id);
  const [page, setPage] = useState(init.page);
  useEffect(() => { const h = setTimeout(() => { setTerm(q); setPage(1); }, 300); return () => clearTimeout(h); }, [q]);
  const params = { kind: "calibration", state, q: term || undefined, site_id: site || undefined, page: page > 1 ? page : undefined };
  const list = useLoad(() => get<ListOut>(`/api/tasks${qs(params)}`), [JSON.stringify(params)]);
  useEffect(() => { navigate(`/calibration${qs({ state: state === "open" ? undefined : state, q: term, site_id: site, page: page > 1 ? page : undefined })}`, true); },
    [state, term, site, page]);
  const c = list.data?.counts;
  const today = list.data?.today ?? "";
  const closed = state === "completed" || state === "oot" || state === "to_review";
  const pick = (s: State) => { setState(s); setPage(1); };
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / 100)) : 1;
  const sites = me?.sites ?? [];
  return (
    <>
      <PageHead title={t("cw.title")} lead={t("cw.lead")} actions={<>
        <Button icon={<LuListChecks />} onClick={() => navigate("/calibration/templates")}>{t("cw.templates")}</Button>
        <Button icon={<LuShieldAlert />} onClick={() => navigate("/calibration/impacts")}>{t("cw.impacts")}</Button>
      </>} />
      <div className="panel">
        <div className="cm-kpis" style={{ borderTop: 0 }}>
          <button type="button" className="kpi-btn" onClick={() => pick("overdue")}><Kpi label={t("cw.k.overdue")} value={c ? number(c.overdue) : "–"} tone={c?.overdue ? "danger" : undefined} /></button>
          <button type="button" className="kpi-btn" onClick={() => pick("due_soon")}><Kpi label={t("cw.k.due_soon", { n: list.data?.due_soon_days ?? 30 })} value={c ? number(c.due_soon) : "–"} tone={c?.due_soon ? "warn" : undefined} /></button>
          <button type="button" className="kpi-btn" onClick={() => pick("at_vendor")}><Kpi label={t("cw.k.at_vendor")} value={c ? number(c.at_vendor) : "–"} /></button>
          <button type="button" className="kpi-btn" onClick={() => pick("in_progress")}><Kpi label={t("cw.k.in_progress")} value={c ? number(c.in_progress) : "–"} tone="brand" /></button>
          <button type="button" className="kpi-btn" onClick={() => pick("to_review")}><Kpi label={t("cw.k.to_review")} value={c ? number(c.to_review) : "–"} /></button>
          <button type="button" className="kpi-btn" onClick={() => pick("oot")}><Kpi label={t("cw.k.oot")} value={c ? number(c.oot_12m) : "–"} tone={c?.oot_12m ? "warn" : undefined} /></button>
          <button type="button" className="kpi-btn" onClick={() => navigate("/calibration/impacts")}><Kpi label={t("cw.k.impacts")} value={c ? number(c.impacts_open) : "–"} tone={c?.impacts_open ? "danger" : undefined} /></button>
        </div>
      </div>
      <div className="panel">
        <div className="toolbar cm-filters">
          <div className="seg" role="tablist" aria-label={t("cw.col.state")}>
            {STATES.map((s) => (
              <button key={s} type="button" role="tab" aria-pressed={state === s} aria-selected={state === s} onClick={() => pick(s)}>
                {t(`cw.state.${s}` as StringKey)}
                {c && s in c ? <span className="subtle">{number((c as unknown as Record<string, number>)[s])}</span> : null}
              </button>
            ))}
          </div>
          <input className="input" type="search" placeholder={t("common.search")} aria-label={t("common.search")} value={q} onChange={(e) => setQ(e.target.value)} />
          {sites.length > 1 && (
            <select className="select" aria-label={t("assets.filter.site")} value={site} onChange={(e) => { setSite(e.target.value); setPage(1); }}>
              <option value="">{t("shell.allSites")}</option>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}
            </select>
          )}
          <span className="spacer" />
          {list.data && <span className="small muted">{number(list.data.total)}</span>}
        </div>
        {list.error ? <LoadError error={list.error} onRetry={list.reload} /> : list.data && list.data.items.length === 0 ? (
          <Empty icon={<LuClipboardCheck />} title={t("cw.empty")} body={t("cw.emptyBody")} />
        ) : (
          <div className="table-wrap">
            <table className="table cm-tasks">
              <thead><tr>
                <th>{t("cw.col.task")}</th><th>{t("cw.col.asset")}</th>
                <th>{closed ? t("cw.col.done") : t("cw.col.due")}</th>
                {!closed && <th>{t("cw.col.window")}</th>}
                <th>{t("cw.col.by")}</th><th>{t("cw.col.state")}</th>
              </tr></thead>
              {!list.data ? <LoadingRows rows={8} cols={6} /> : (
                <tbody>
                  {list.data.items.map((x) => (
                    <tr key={x.id} className={x.overdue ? "cm-row-late" : ""}>
                      <td className="nowrap"><Link to={`/calibration/tasks/${x.id}`} className="mono-id cell-main">{x.number}</Link></td>
                      <td>
                        <div className="row" style={{ gap: 8 }}>
                          <CategoryIcon category={x.category} />
                          <div>
                            <Link to={`/assets/${x.asset_id}`} className="mono-id">{x.asset_code}</Link>
                            <div className="small subtle">{lang === "ar" && x.asset_name_ar ? x.asset_name_ar : x.asset_name_en} · {x.site_code}{x.location_code ? ` / ${x.location_code}` : ""}</div>
                          </div>
                        </div>
                      </td>
                      {closed ? (
                        <td className="nowrap num">{day(x.performed_date)}</td>
                      ) : (
                        <td className="nowrap"><div className="num">{day(x.due_date)}</div><DueWhen task={x} today={today} /></td>
                      )}
                      {!closed && <td className="small nowrap">{x.window_start ? t("cw.windowRange", { start: day(x.window_start), end: day(x.window_end) })
                        : t("cw.windowAny", { end: day(x.window_end) })}</td>}
                      <td className="small">
                        {x.performer === "external" ? <><span className="cm-kind">{t("perf.external")}</span> {x.vendor_name}</> : (x.completed_by_name ?? x.assigned_name ?? <span className="subtle">{t("perf.internal")}</span>)}
                        {x.responsible_name && x.performer === "external" && <div className="subtle">{x.responsible_name}</div>}
                      </td>
                      <td><div className="row wrap" style={{ gap: 4 }}>
                        {closed ? <VerdictBadge verdict={x.verdict} /> : <TaskStateBadge task={x} />}
                        {closed && x.reviewed_at && <span className="small subtle">✓</span>}
                      </div></td>
                    </tr>
                  ))}
                </tbody>
              )}
            </table>
          </div>
        )}
        {pages > 1 && (
          <div className="pager">
            <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>{t("common.previous")}</Button>
            <span className="small muted">{t("common.pageOf", { page, pages })}</span>
            <Button size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>{t("common.next")}</Button>
          </div>
        )}
      </div>
    </>
  );
}
