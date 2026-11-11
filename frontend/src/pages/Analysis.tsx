// Phase 6: results analysis — KPIs, monthly work, classes, vendors, interval recommendations, PM between campaigns;
// plus the drift panel (asset calibration tab) and the run-hours panel (asset maintenance tab).
import { useMemo, useState } from "react";
import { LuArrowDownRight, LuArrowUpRight, LuClock, LuFactory, LuGauge, LuPlus, LuTriangleAlert } from "react-icons/lu";
import { Kpi } from "../components/cm";
import {
  Badge, Button, Empty, LoadError, LoadingRows, Notice, PageHead, SelectField, Sheet, TextField, errorText, fieldErrors, useLoad, useToast,
} from "../components/ui";
import { VerdictBadge, useEvery, type Verdict } from "../components/work";
import { get, patch, post } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link } from "../lib/router";
import type { StringKey } from "../lib/strings";

type Kind = "calibration" | "pm";
interface Month { month: string; completed: number; on_time: number; oot: number }
interface Summary {
  kind: Kind; since: string; until: string; months: number; completed: number; on_time: number; oot: number; first_pass: number;
  failed: number; man_hours: number; external: number; overdue: number; open: number; due_by_hours: number;
  on_time_pct: number | null; oot_pct: number | null; first_pass_pct: number | null; monthly: Month[];
  by_class: { code: string; name_en: string; name_ar: string; completed: number; oot: number; on_time: number; oot_pct: number | null }[];
  vendors: { id: string; code: string; name: string; completed: number; on_time: number; oot: number; on_time_pct: number | null;
    oot_pct: number | null; turnaround_days: number | null; record_days: number | null; missing_certificates: number }[];
}
interface Rec {
  id: string; asset_id: string; asset_code: string; asset_name_en: string; asset_name_ar: string; interval_value: number; interval_unit: string;
  last_done: string | null; history: Verdict[]; reason: "last_oot" | "drift" | "stable"; crossing?: string; max_margin_pct?: number | null;
  suggested_value: number; suggested_unit: string; direction: "extend" | "shorten"; next_due_if_applied: string;
}
interface Gap {
  id: string; number: string; asset_id: string; asset_code: string; asset_name_en: string; asset_name_ar: string; due_date: string;
  window_end: string; conflict: boolean; need_minutes: number;
  suggestion: { start: string; end: string; date: string; hours_free: number } | null;
}

const pct = (v: number | null | undefined) => (v == null ? "—" : `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`);

export function AnalysisPage() {
  const { t } = useI18n();
  const [kind, setKind] = useState<Kind>("calibration");
  const [months, setMonths] = useState(12);
  const sum = useLoad(() => get<Summary>(`/api/analysis/summary?kind=${kind}&months=${months}`), [kind, months]);
  const s = sum.data;
  return (
    <>
      <PageHead title={t("an.title")} lead={t("an.lead")} />
      <div className="toolbar cm-an-filters">
        <div className="seg" role="group" aria-label={t("an.title")}>
          {(["calibration", "pm"] as const).map((k) => (
            <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}>{t(`an.kind.${k}`)}</button>
          ))}
        </div>
        <label className="row small" style={{ gap: 6 }}>{t("an.months")}
          <select className="select cell-input" value={months} onChange={(e) => setMonths(Number(e.target.value))}>
            {[3, 6, 12, 24, 36].map((n) => <option key={n} value={n}>{t("an.monthsN", { n })}</option>)}
          </select>
        </label>
      </div>
      {sum.error ? <div className="panel"><LoadError error={sum.error} onRetry={sum.reload} /></div> : !s ? (
        <div className="panel"><LoadingRows rows={4} cols={4} /></div>
      ) : (
        <>
          <div className="panel">
            <div className="cm-kpis" style={{ borderTop: 0 }}>
              <Kpi label={t("an.completed")} value={s.completed} />
              <Kpi label={t("an.onTime")} value={pct(s.on_time_pct)} tone={(s.on_time_pct ?? 100) >= 95 ? "ok" : "warn"}
                hint={t("an.ofN", { n: s.on_time, total: s.completed })} />
              <Kpi label={t(kind === "pm" ? "an.findings" : "an.oot")} value={pct(s.oot_pct)} tone={s.oot ? "danger" : "ok"}
                hint={t("an.ofN", { n: s.oot, total: s.completed })} />
              {kind === "calibration"
                ? <Kpi label={t("an.firstPass")} value={pct(s.first_pass_pct)} />
                : <Kpi label={t("an.manHours")} value={Math.round(Number(s.man_hours)).toLocaleString()} />}
              <Kpi label={t("an.overdue")} value={s.overdue} tone={s.overdue ? "danger" : "ok"} />
              {s.due_by_hours > 0 && <Kpi label={t("an.dueByHours")} value={s.due_by_hours} tone="brand" />}
            </div>
          </div>
          <div className="grid-2 cm-an-grid">
            <section className="panel cm-sec">
              <div className="panel-head"><h3>{t("an.byMonth")}</h3></div>
              <p className="small muted">{t("an.byMonthHint")}</p>
              <MonthChart data={s.monthly} />
            </section>
            <section className="panel">
              <div className="panel-head"><h3>{t("an.byClass")}</h3></div>
              <ClassTable s={s} kind={kind} />
            </section>
          </div>
          <section className="panel">
            <div className="panel-head"><h3>{t("an.vendors")}</h3></div>
            {s.vendors.length === 0 ? <p className="panel-body small muted">{t("an.noVendors")}</p> : (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>{t("an.vendor")}</th><th className="num">{t("an.completed")}</th><th className="num">{t("an.onTime")}</th>
                    <th className="num">{t(kind === "pm" ? "an.findings" : "an.oot")}</th><th className="num">{t("an.turnaround")}</th>
                    <th className="num">{t("an.recordDays")}</th>{kind === "calibration" && <th className="num">{t("an.missingCerts")}</th>}</tr></thead>
                  <tbody>
                    {s.vendors.map((v) => (
                      <tr key={v.id}>
                        <td><span className="cell-main">{v.name}</span> <span className="small subtle mono-id">{v.code}</span></td>
                        <td className="num">{v.completed}</td>
                        <td className="num"><Bar v={v.on_time_pct} good /> {pct(v.on_time_pct)}</td>
                        <td className="num">{pct(v.oot_pct)}</td>
                        <td className="num">{v.turnaround_days == null ? "—" : t("an.days", { n: v.turnaround_days })}</td>
                        <td className="num">{v.record_days == null ? "—" : t("an.days", { n: v.record_days })}</td>
                        {kind === "calibration" && <td className="num">{v.missing_certificates ? <Badge tone="warn">{v.missing_certificates}</Badge> : 0}</td>}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          {kind === "calibration" ? <Intervals /> : <Gaps />}
        </>
      )}
    </>
  );
}

function Bar({ v, good }: { v: number | null; good?: boolean }) {
  if (v == null) return null;
  return <span className={`cm-an-bar ${good ? "good" : ""}`} aria-hidden><span style={{ width: `${v > 0 ? Math.max(2, Math.min(100, v)) : 0}%` }} /></span>;
}

function MonthChart({ data }: { data: Month[] }) {
  const { t, lang } = useI18n();
  const [hover, setHover] = useState<number | null>(null);
  const W = 640, H = 210, L = 34, B = 26, T = 10;
  const max = Math.max(4, ...data.map((d) => d.completed));
  const step = Math.ceil(max / 4);
  const top = step * 4;
  const bw = (W - L - 8) / data.length;
  const y = (v: number) => T + (H - T - B) * (1 - v / top);
  const label = (m: string) => new Date(`${m}-01T00:00:00`).toLocaleDateString(lang === "ar" ? "ar-EG" : "en-GB", { month: "short" });
  const h = hover != null ? data[hover] : null;
  return (
    <div className="cm-chart">
      <div className="cm-legend small">
        <span><i className="sw in" />{t("an.inWindow")}</span><span><i className="sw late" />{t("an.late")}</span>
      </div>
      <div className="cm-chart-box">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t("an.byMonth")} onMouseLeave={() => setHover(null)}>
          {[0, 1, 2, 3, 4].map((i) => (
            <g key={i}>
              <line x1={L} x2={W - 4} y1={y(i * step)} y2={y(i * step)} className="grid" />
              <text x={L - 6} y={y(i * step) + 4} className="tick" textAnchor="end">{i * step}</text>
            </g>
          ))}
          {data.map((d, i) => {
            const x = L + i * bw + bw * 0.18, w = bw * 0.64;
            const late = d.completed - d.on_time;
            return (
              <g key={d.month} onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={0}>
                <rect x={L + i * bw} y={T} width={bw} height={H - T - B} className="hit" />
                {d.on_time > 0 && <rect x={x} y={y(d.on_time)} width={w} height={y(0) - y(d.on_time)} rx={late ? 0 : 3} className="bar-in" />}
                {late > 0 && <rect x={x} y={y(d.completed)} width={w} height={y(d.on_time) - y(d.completed) - (d.on_time ? 2 : 0)} rx={3} className="bar-late" />}
                {(data.length <= 12 || i % 2 === 0) && <text x={L + i * bw + bw / 2} y={H - 8} className="tick" textAnchor="middle">{label(d.month)}</text>}
                {hover === i && <rect x={L + i * bw} y={T} width={bw} height={H - T - B} className="hover" />}
              </g>
            );
          })}
        </svg>
        {h && (
          <div className="cm-tip small" style={{ insetInlineStart: `${((L + (hover! + 0.5) * bw) / W) * 100}%` }}>
            <strong>{label(h.month)} {h.month.slice(0, 4)}</strong>
            <div>{t("an.completed")}: {h.completed}</div>
            <div>{t("an.late")}: {h.completed - h.on_time}</div>
            <div>{t("an.oot")}: {h.oot}</div>
          </div>
        )}
      </div>
      <details className="small">
        <summary>{t("an.table")}</summary>
        <table className="table compact">
          <thead><tr><th>{t("an.month")}</th><th className="num">{t("an.completed")}</th><th className="num">{t("an.late")}</th><th className="num">{t("an.oot")}</th></tr></thead>
          <tbody>{data.map((d) => <tr key={d.month}><td>{d.month}</td><td className="num">{d.completed}</td><td className="num">{d.completed - d.on_time}</td><td className="num">{d.oot}</td></tr>)}</tbody>
        </table>
      </details>
    </div>
  );
}

function ClassTable({ s, kind }: { s: Summary; kind: Kind }) {
  const { t, lang } = useI18n();
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th>{t("an.class")}</th><th className="num">{t("an.completed")}</th><th className="num">{t(kind === "pm" ? "an.findings" : "an.oot")}</th><th>{t("an.rate")}</th></tr></thead>
        <tbody>
          {s.by_class.map((c) => (
            <tr key={c.code}>
              <td><span className="cell-main">{lang === "ar" ? c.name_ar : c.name_en}</span></td>
              <td className="num">{c.completed}</td>
              <td className="num">{c.oot}</td>
              <td className="nowrap"><Bar v={c.oot_pct} /> <span className="small">{pct(c.oot_pct)}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Intervals() {
  const { t, lang, date } = useI18n();
  const every = useEvery();
  const list = useLoad(() => get<{ items: Rec[]; unchanged: number }>("/api/analysis/intervals"), []);
  const [apply, setApply] = useState<Rec | null>(null);
  const [all, setAll] = useState(false);
  const SHOW = 8;
  return (
    <section className="panel">
      <div className="panel-head"><h3>{t("an.intervals")}</h3></div>
      <p className="panel-body small muted" style={{ paddingBottom: 0 }}>{t("an.intervalsLead")}</p>
      {list.error ? <LoadError error={list.error} onRetry={list.reload} /> : !list.data ? <LoadingRows rows={4} cols={5} /> :
        list.data.items.length === 0 ? <Empty icon={<LuClock />} title={t("an.noIntervals")} body={t("an.unchanged", { n: list.data.unchanged })} /> : (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>{t("af.code")}</th><th>{t("an.history")}</th><th>{t("an.current")}</th><th>{t("an.suggested")}</th><th>{t("an.why")}</th><th /></tr></thead>
                <tbody>
                  {list.data.items.filter((r, i) => all || r.direction === "shorten" || i < SHOW).map((r) => (
                    <tr key={r.id}>
                      <td><Link to={`/assets/${r.asset_id}?tab=calibration`} className="mono-id cell-main">{r.asset_code}</Link>
                        <div className="small subtle">{lang === "ar" ? r.asset_name_ar : r.asset_name_en}</div></td>
                      <td className="nowrap">{[...r.history].reverse().slice(-4).map((v, i) => <VerdictBadge key={i} verdict={v} kind="calibration" />)}</td>
                      <td className="nowrap small">{every(r.interval_value, r.interval_unit)}</td>
                      <td className="nowrap"><Badge tone={r.direction === "shorten" ? "warn" : "ok"}>
                        {r.direction === "shorten" ? <LuArrowDownRight aria-hidden /> : <LuArrowUpRight aria-hidden />} {every(r.suggested_value, r.suggested_unit)}</Badge></td>
                      <td className="small cm-an-why">{t(`an.reason.${r.reason}` as StringKey, { date: r.crossing ? date(r.crossing) : "", pct: r.max_margin_pct ?? 0 })}</td>
                      <td><Button size="sm" onClick={() => setApply(r)}>{t("an.apply")}</Button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="panel-body small muted row wrap" style={{ gap: 12 }}>
              {!all && list.data.items.length > SHOW && <Button size="sm" variant="ghost" onClick={() => setAll(true)}>{t("an.showAll", { n: list.data.items.length })}</Button>}
              {t("an.unchanged", { n: list.data.unchanged })}</p>
          </>
        )}
      {apply && <ApplyInterval rec={apply} onClose={() => setApply(null)} onDone={() => { setApply(null); list.reload(); }} />}
    </section>
  );
}

function ApplyInterval({ rec, onClose, onDone }: { rec: Rec; onClose: () => void; onDone: () => void }) {
  const { t, date } = useI18n();
  const every = useEvery();
  const toast = useToast();
  const [reason, setReason] = useState(t(`an.reason.${rec.reason}` as StringKey, { date: rec.crossing ? date(rec.crossing) : "", pct: rec.max_margin_pct ?? 0 }));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    setBusy(true); setErr(null);
    try {
      await patch(`/api/schedules/${rec.id}`, { interval_value: rec.suggested_value, interval_unit: rec.suggested_unit,
        next_due: rec.next_due_if_applied, reason });
      toast(t("an.applied")); onDone();
    } catch (e) { setErr(errorText(e, t)); setBusy(false); }
  };
  return (
    <Sheet title={t("an.applyTitle", { code: rec.asset_code })} onClose={onClose} onSubmit={go} footer={<>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      <Button type="submit" variant="primary" busy={busy} disabled={reason.trim().length < 3}>{t("an.apply")}</Button>
    </>}>
      {err && <Notice tone="danger">{err}</Notice>}
      <p>{t("an.applyLead", { from: every(rec.interval_value, rec.interval_unit), to: every(rec.suggested_value, rec.suggested_unit),
        due: date(rec.next_due_if_applied) })}</p>
      <TextField label={t("an.reasonLabel")} value={reason} onChange={setReason} maxLength={500} required />
    </Sheet>
  );
}

function Gaps() {
  const { t, lang, date, dateTime } = useI18n();
  const list = useLoad(() => get<{ items: Gap[] }>("/api/analysis/pm-gaps"), []);
  return (
    <section className="panel">
      <div className="panel-head"><h3><LuFactory aria-hidden /> {t("an.gaps")}</h3></div>
      <p className="panel-body small muted" style={{ paddingBottom: 0 }}>{t("an.gapsLead")}</p>
      {list.error ? <LoadError error={list.error} onRetry={list.reload} /> : !list.data ? <LoadingRows rows={4} cols={4} /> :
        list.data.items.length === 0 ? <p className="panel-body small muted">{t("an.noGaps")}</p> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>{t("cw.col.task")}</th><th>{t("af.code")}</th><th>{t("cw.col.due")}</th><th /><th>{t("an.slot")}</th></tr></thead>
              <tbody>
                {list.data.items.map((g) => (
                  <tr key={g.id}>
                    <td><Link to={`/maintenance/tasks/${g.id}`} className="mono-id cell-main">{g.number}</Link></td>
                    <td><span className="mono-id">{g.asset_code}</span> <span className="small subtle">{lang === "ar" ? g.asset_name_ar : g.asset_name_en}</span></td>
                    <td className="nowrap small">{date(g.due_date)}</td>
                    <td>{g.conflict ? <Badge tone="warn"><LuTriangleAlert aria-hidden /> {t("an.conflict")}</Badge> : <Badge tone="ok">{t("an.fits")}</Badge>}</td>
                    <td className="small">{g.suggestion ? `${dateTime(g.suggestion.start)} · ${g.suggestion.hours_free} h`
                      : t("an.noSlot", { h: Math.round(g.need_minutes / 60) })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </section>
  );
}

// ------------------------------------------------------------------ drift (asset calibration tab)
interface Pt { task_id: string; number: string; performed_date: string; as_found: number | null; as_left: number | null; low_limit: number | null; high_limit: number | null }
interface Series {
  parameter: string; nominal: number | null; unit: string | null; low_limit: number | null; high_limit: number | null; points: Pt[];
  fit: { from: string; a: number; b_per_day: number } | null; slope_per_year: number | null; projected_crossing: string | null;
  margin_used_pct: number | null; points_in_fit: number;
}

export function DriftPanel({ assetId }: { assetId: string }) {
  const { t } = useI18n();
  const d = useLoad(() => get<{ series: Series[] }>(`/api/analysis/drift?asset_id=${assetId}`), [assetId]);
  if (d.error) return <LoadError error={d.error} onRetry={d.reload} />;
  if (!d.data) return <LoadingRows rows={3} cols={3} />;
  return (
    <section className="panel cm-sec">
      <div className="panel-head"><h3><LuGauge aria-hidden /> {t("dr.title")}</h3></div>
      <p className="small muted">{t("dr.lead")}</p>
      {d.data.series.length === 0 ? <p className="small muted">{t("dr.none")}</p> : (
        <div className="cm-drift-grid">{d.data.series.map((s, i) => <DriftChart key={i} s={s} />)}</div>
      )}
    </section>
  );
}

const num = (v: number) => Number(v.toPrecision(Math.abs(v) < 1 ? 3 : 6)).toString();

function DriftChart({ s }: { s: Series }) {
  const { t, date } = useI18n();
  const [hover, setHover] = useState<number | null>(null);
  const W = 420, H = 190, L = 54, R = 10, T = 12, B = 24;
  const geo = useMemo(() => {
    const day = (iso: string) => new Date(`${iso.slice(0, 10)}T00:00:00Z`).getTime() / 86_400_000;
    const xs = s.points.map((p) => day(p.performed_date));
    const end = Math.max(...xs, s.projected_crossing ? day(s.projected_crossing) : -Infinity, Date.now() / 86_400_000);
    const x0 = Math.min(...xs), x1 = end + Math.max(10, (end - x0) * 0.04);
    const vals = s.points.flatMap((p) => [p.as_found, p.as_left]).filter((v): v is number => v != null).map(Number);
    const lim = [s.low_limit, s.high_limit].filter((v): v is number => v != null).map(Number);
    let y0 = Math.min(...vals, ...lim), y1 = Math.max(...vals, ...lim);
    if (y0 === y1) { y0 -= 1; y1 += 1; }
    const pad = (y1 - y0) * 0.12;
    y0 -= pad; y1 += pad;
    const X = (v: number) => L + ((v - x0) / Math.max(1, x1 - x0)) * (W - L - R);
    const Y = (v: number) => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
    return { day, xs, x0, x1, X, Y };
  }, [s]);
  const { day, X, Y, x1 } = geo;
  const found = s.points.map((p, i) => ({ p, i })).filter(({ p }) => p.as_found != null);
  const path = found.map(({ p }, k) => `${k ? "L" : "M"}${X(day(p.performed_date)).toFixed(1)},${Y(Number(p.as_found)).toFixed(1)}`).join("");
  let proj: string | null = null;
  if (s.fit) {
    const f0 = day(s.fit.from);
    const fx = s.projected_crossing ? day(s.projected_crossing) : x1;
    proj = `M${X(f0)},${Y(s.fit.a)}L${X(fx)},${Y(s.fit.a + s.fit.b_per_day * (fx - f0))}`;
  }
  const h = hover != null ? s.points[hover] : null;
  const title = `${s.parameter}${s.nominal != null ? ` ${num(Number(s.nominal))}` : ""} ${s.unit ?? ""}`;
  return (
    <figure className="cm-drift">
      <figcaption>
        <strong>{title}</strong>
        <span className="small">
          {s.projected_crossing ? <Badge tone="warn">{t("dr.crossing", { date: date(s.projected_crossing) })}</Badge>
            : s.fit ? <span className="muted">{t("dr.noCrossing")}</span> : <span className="muted">{t("dr.tooFew")}</span>}
          {s.margin_used_pct != null && <span className="muted"> · {t("dr.margin", { pct: s.margin_used_pct })}</span>}
          {s.slope_per_year != null && <span className="muted"> · {t("dr.slope", { v: num(s.slope_per_year), u: s.unit ?? "" })}</span>}
        </span>
      </figcaption>
      <div className="cm-chart-box">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} onMouseLeave={() => setHover(null)}>
          {s.low_limit != null && s.high_limit != null && (
            <rect x={L} width={W - L - R} y={Y(Number(s.high_limit))} height={Y(Number(s.low_limit)) - Y(Number(s.high_limit))} className="band" />
          )}
          {[s.low_limit, s.high_limit].map((v, i) => v != null && (
            <g key={i}>
              <line x1={L} x2={W - R} y1={Y(Number(v))} y2={Y(Number(v))} className="limit" />
              <text x={L - 6} y={Y(Number(v)) + 4} className="tick" textAnchor="end">{num(Number(v))}</text>
            </g>
          ))}
          {s.nominal != null && <line x1={L} x2={W - R} y1={Y(Number(s.nominal))} y2={Y(Number(s.nominal))} className="grid" />}
          {proj && <path d={proj} className="proj" />}
          {s.projected_crossing && s.fit && (() => {
            const cx = X(day(s.projected_crossing!));
            const cy = Y(s.fit!.a + s.fit!.b_per_day * (day(s.projected_crossing!) - day(s.fit!.from)));
            return <circle cx={cx} cy={cy} r={5} className="cross" />;
          })()}
          <path d={path} className="line" />
          {s.points.map((p, i) => {
            const x = X(day(p.performed_date));
            return (
              <g key={i} onMouseEnter={() => setHover(i)} tabIndex={0} onFocus={() => setHover(i)}>
                <rect x={x - 10} y={T} width={20} height={H - T - B} className="hit" />
                {p.as_found != null && <circle cx={x} cy={Y(Number(p.as_found))} r={4} className={`dot ${p.as_found != null && ((p.low_limit != null && Number(p.as_found) < Number(p.low_limit)) || (p.high_limit != null && Number(p.as_found) > Number(p.high_limit))) ? "out" : ""}`} />}
                {p.as_left != null && <circle cx={x} cy={Y(Number(p.as_left))} r={4} className="left" />}
              </g>
            );
          })}
          <text x={L} y={H - 6} className="tick">{date(s.points[0].performed_date)}</text>
          <text x={W - R} y={H - 6} className="tick" textAnchor="end">{date(new Date(x1 * 86_400_000).toISOString())}</text>
        </svg>
        {h && (
          <div className="cm-tip small" style={{ insetInlineStart: `${(X(day(h.performed_date)) / W) * 100}%` }}>
            <strong>{h.number}</strong> · {date(h.performed_date)}
            {h.as_found != null && <div>{t("dr.asFound")}: {num(Number(h.as_found))} {s.unit}</div>}
            {h.as_left != null && <div>{t("dr.asLeft")}: {num(Number(h.as_left))} {s.unit}</div>}
          </div>
        )}
      </div>
      <div className="cm-legend small">
        <span><i className="sw dot" />{t("dr.asFound")}</span><span><i className="sw left" />{t("dr.asLeft")}</span>
        <span><i className="sw band" />{t("dr.limits")}</span>
      </div>
    </figure>
  );
}

// ------------------------------------------------------------------ run-hours (asset maintenance tab)
interface HoursStatus { hours_interval: number; used: number | null; remaining: number | null; projected_date: string | null; due: boolean }
interface Meter {
  meter_unit: "hours" | "cycles" | null; total: number | null; last: number | null; read_at: string | null; rate_per_day: number | null;
  can_enter: boolean; tasks_brought_forward?: number;
  schedules: { id: string; kind: Kind; title: string | null; template_title: string | null; hours_interval: number; hours: HoursStatus | null }[];
  readings: { id: string; reading: number; read_at: string; source: string; meter_reset: boolean; note: string | null; entered_by_name: string }[];
}

export function RunHoursPanel({ assetId }: { assetId: string }) {
  const { t, date, dateTime } = useI18n();
  const m = useLoad(() => get<Meter>(`/api/assets/${assetId}/meter`), [assetId]);
  const [adding, setAdding] = useState(false);
  if (m.error) return <LoadError error={m.error} onRetry={m.reload} />;
  if (!m.data) return <LoadingRows rows={2} cols={3} />;
  const x = m.data;
  const u = t(`rh.unit.${x.meter_unit ?? "hours"}` as StringKey);
  const n = (v: number | null) => (v == null ? "—" : Math.round(Number(v)).toLocaleString());
  return (
    <section className="panel cm-sec">
      <div className="panel-head">
        <h3><LuClock aria-hidden /> {t(x.meter_unit === "cycles" ? "rh.titleCycles" : "rh.title")}</h3>
        <span className="spacer" />
        {x.can_enter && <Button size="sm" icon={<LuPlus />} onClick={() => setAdding(true)}>{t("rh.add")}</Button>}
      </div>
      <p className="small muted">{t("rh.lead")}</p>
      {x.readings.length === 0 ? <p className="small muted">{t("rh.none")}</p> : (
        <div className="cm-kpis cm-rh-kpis">
          <Kpi label={t("rh.total")} value={`${n(x.total)} ${u}`} />
          <Kpi label={t("rh.perDay")} value={x.rate_per_day == null ? "—" : `${x.rate_per_day} ${u}`} />
          <Kpi label={t("rh.lastRead")} value={<span className="cm-kpi-date">{x.read_at ? date(x.read_at) : "—"}</span>} />
        </div>
      )}
      {x.schedules.length === 0 ? <p className="small muted">{t("rh.noSchedules")}</p> : x.schedules.map((s) => {
        const h = s.hours;
        const used = h?.used == null ? 0 : Number(h.used);
        const share = Math.min(100, (used / Number(s.hours_interval)) * 100);
        return (
          <div key={s.id} className="cm-rh-row">
            <div className="row wrap" style={{ gap: 8 }}>
              <strong className="small">{s.template_title ?? s.title ?? t(`an.kind.${s.kind}`)}</strong>
              {h?.due && <Badge tone="warn">{t("rh.dueByHours")}</Badge>}
              <span className="spacer" />
              <span className="small muted">{h?.used == null ? "" : t("rh.used", { used: `${n(h.used)} ${u}`, interval: `${n(s.hours_interval)} ${u}` })}</span>
            </div>
            <div className={`cm-rh-bar ${h?.due ? "due" : share > 80 ? "near" : ""}`} role="meter" aria-valuemin={0} aria-valuemax={Number(s.hours_interval)} aria-valuenow={used}>
              <span style={{ width: `${share}%` }} />
            </div>
            <div className="small muted">
              {h?.remaining != null && (Number(h.remaining) >= 0 ? t("rh.remaining", { n: `${n(h.remaining)} ${u}` }) : t("rh.overBy", { n: `${n(-Number(h.remaining))} ${u}` }))}
              {h?.projected_date && !h.due && <> · {t("rh.projected", { date: date(h.projected_date) })}</>}
            </div>
          </div>
        );
      })}
      {x.readings.length > 0 && (
        <details className="small">
          <summary>{t("rh.lastRead")} ({x.readings.length})</summary>
          <table className="table compact">
            <tbody>{x.readings.map((r) => (
              <tr key={r.id}><td className="nowrap">{dateTime(r.read_at)}</td><td className="num">{n(r.reading)} {u}</td>
                <td>{t(`rh.source.${r.source}` as StringKey)}{r.meter_reset && <> · <Badge tone="warn">{t("rh.reset")}</Badge></>}</td>
                <td className="subtle">{r.entered_by_name}{r.note ? ` · ${r.note}` : ""}</td></tr>
            ))}</tbody>
          </table>
        </details>
      )}
      {adding && <AddReading assetId={assetId} unit={x.meter_unit} last={x.last} onClose={() => setAdding(false)}
        onDone={(out) => { setAdding(false); m.setData(out); }} />}
    </section>
  );
}

function AddReading({ assetId, unit, last, onClose, onDone }: { assetId: string; unit: Meter["meter_unit"]; last: number | null;
  onClose: () => void; onDone: (m: Meter) => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const [f, setF] = useState({ reading: "", unit: unit ?? "hours", reset: false, note: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const go = async () => {
    setBusy(true); setErr(null); setErrs({});
    try {
      const out = await post<Meter>(`/api/assets/${assetId}/meter`, { reading: Number(f.reading.replace(",", ".")), meter_unit: f.unit,
        meter_reset: f.reset, note: f.note || null });
      toast(out.tasks_brought_forward ? t("rh.broughtForward", { n: out.tasks_brought_forward }) : t("rh.saved"));
      onDone(out);
    } catch (e) { setErr(errorText(e, t)); setErrs(fieldErrors(e)); setBusy(false); }
  };
  return (
    <Sheet title={t("rh.add")} onClose={onClose} onSubmit={go} footer={<>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      <Button type="submit" variant="primary" busy={busy} disabled={!f.reading || isNaN(Number(f.reading.replace(",", ".")))}>{t("common.save")}</Button>
    </>}>
      {err && <Notice tone="danger">{err}</Notice>}
      <div className="grid-2">
        <TextField label={t("rh.reading")} value={f.reading} onChange={(v) => setF({ ...f, reading: v })} inputMode="decimal" dir="ltr"
          error={errs.reading} hint={last != null ? `${t("rh.lastRead")}: ${Number(last).toLocaleString()}` : undefined} autoFocus />
        <SelectField label={t("rh.unitLabel")} value={f.unit} onChange={(v) => setF({ ...f, unit: v as "hours" })}
          options={[{ value: "hours", label: t("rh.hours") }, { value: "cycles", label: t("rh.cycles") }]} />
      </div>
      <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={f.reset} onChange={(e) => setF({ ...f, reset: e.target.checked })} />{t("rh.reset")}</label>
      <TextField label={t("rh.note")} value={f.note} onChange={(v) => setF({ ...f, note: v })} maxLength={500} error={errs.note} required={f.reset} />
    </Sheet>
  );
}

