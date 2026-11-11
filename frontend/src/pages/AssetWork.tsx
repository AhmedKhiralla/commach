// The asset page's Calibration tab: its schedule, every calibration task and the impact assessments.
import { DriftPanel, RunHoursPanel } from "./Analysis";
import { useState } from "react";
import { LuCalendarClock, LuPencil, LuBan } from "react-icons/lu";
import { Badge, Button, Dialog, Empty, LoadError, Notice, TextField, errorText, useLoad } from "../components/ui";
import {
  DueWhen, SchedulePlanner, TaskStateBadge, VerdictBadge, todayIso, useDay, useEvery, useRuleText, type Schedule, type TaskRow,
} from "../components/work";
import { get, post } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link } from "../lib/router";
import type { StringKey } from "../lib/strings";

interface WorkOut {
  schedules: Schedule[]; tasks: TaskRow[];
  impacts: { id: string; number: string; status: string; period_from: string | null; period_to: string; risk: string | null }[];
  can_plan: boolean;
}

function EndSchedule({ schedule, onClose, onDone }: { schedule: Schedule; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Dialog title={t("sch.end")} onClose={onClose} footer={<>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      <Button variant="danger" busy={busy} disabled={reason.trim().length < 3} onClick={async () => {
        setBusy(true); setErr(null);
        try { await post(`/api/schedules/${schedule.id}/end`, { reason }); onDone(); onClose(); } catch (e) { setErr(errorText(e, t)); setBusy(false); }
      }}>{t("sch.end")}</Button>
    </>}>
      <p>{t("sch.endBody")}</p>
      <TextField label={t("sch.endReason")} value={reason} onChange={setReason} maxLength={500} />
      {err && <Notice tone="danger">{err}</Notice>}
    </Dialog>
  );
}

export function AssetCalibration({ asset, onChanged, kind = "calibration" }: {
  asset: { id: string; code: string; site_id: string }; onChanged?: () => void; kind?: "calibration" | "pm";
}) {
  const pm = kind === "pm";
  const { t } = useI18n();
  const day = useDay();
  const ruleText = useRuleText();
  const every = useEvery();
  const w = useLoad(() => get<WorkOut>(`/api/assets/${asset.id}/work`), [asset.id]);
  const [planning, setPlanning] = useState<Schedule | "new" | null>(null);
  const [ending, setEnding] = useState<Schedule | null>(null);
  if (w.error) return <section className="panel"><LoadError error={w.error} onRetry={w.reload} /></section>;
  if (!w.data) return <section className="panel"><div className="panel-body"><div className="skeleton" style={{ width: "50%" }} /></div></section>;
  const cal = w.data.schedules.filter((s) => s.kind === kind);
  const active = cal.filter((s) => s.active);
  const tasks = w.data.tasks.filter((x) => x.kind === kind);
  const today = todayIso();
  const done = () => { w.reload(); onChanged?.(); };
  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-head"><h3>{pm ? t("asset.tab.maintenance") : t("asset.tab.calibration")}</h3><span className="spacer" />
          {w.data.can_plan && (pm || active.length === 0) && <Button size="sm" variant="primary" icon={<LuCalendarClock />}
            onClick={() => setPlanning("new")}>{pm ? (active.length ? t("sch.addPm") : t("pmw.plan")) : t("cw.plan")}</Button>}
        </div>
        {cal.length === 0 ? <Empty icon={<LuCalendarClock />} title={pm ? t("sch.nonePm") : t("sch.none")} body={pm ? t("sch.nonePmBody") : t("sch.noneBody")} /> : (
          <ul className="cm-scheds">
            {cal.map((s) => (
              <li key={s.id} className={s.active ? "" : "ended"}>
                <div className="grow">
                  <div className="row wrap" style={{ gap: 8 }}>
                    <strong>{every(s.interval_value, s.interval_unit)}</strong>
                    <Badge tone={s.active ? "ok" : undefined}>{s.active ? t("sch.active") : t("sch.ended")}</Badge>
                    <span className="small muted">{t(`basis.${s.basis}` as StringKey)}</span>
                  </div>
                  <div className="small">{ruleText(s.rule)}</div>
                  <div className="small muted">
                    {s.template_code ? `${s.template_code} v${s.template_version} · ${s.template_title}` : t("sch.noTemplate")}
                  </div>
                  <div className="small muted">
                    {t(`perf.${s.performer}` as StringKey)}{s.vendor_name ? ` · ${s.vendor_name}` : ""}
                    {s.responsible_name ? ` · ${t("task.responsible")}: ${s.responsible_name}` : ""}
                    {s.assignee_roles.length > 0 && ` · ${s.assignee_roles.map((r) => t(`role.${r}` as StringKey)).join(", ")}`}
                    {s.assignee_names.length > 0 && ` · ${s.assignee_names.join(", ")}`}
                  </div>
                  <div className="small">
                    {s.next_due && s.active && <strong>{t("sch.next", { date: day(s.next_due) })}</strong>}
                    {s.last_done && <span className="muted"> · {t("sch.last", { date: day(s.last_done) })}</span>}
                    {s.certificate_required && <span className="muted"> · {t("sch.certRequired")}</span>}
                  </div>
                </div>
                {s.active && w.data!.can_plan && (
                  <div className="row" style={{ gap: 6 }}>
                    <Button size="sm" icon={<LuPencil />} onClick={() => setPlanning(s)}>{t("common.edit")}</Button>
                    <Button size="sm" variant="ghost" icon={<LuBan />} onClick={() => setEnding(s)}>{t("sch.end")}</Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {tasks.length > 0 && (
        <section className="panel">
          <div className="panel-head"><h3>{pm ? t("asset.pmTasks") : t("asset.tasks")}</h3></div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>{t("cw.col.task")}</th><th>{t("cw.col.due")}</th><th>{t("cw.col.done")}</th><th>{t("cw.col.by")}</th><th>{t("cw.col.state")}</th></tr></thead>
              <tbody>
                {tasks.map((x) => (
                  <tr key={x.id} className={x.overdue ? "cm-row-late" : ""}>
                    <td><Link to={`/${pm ? "maintenance" : "calibration"}/tasks/${x.id}`} className="mono-id cell-main">{x.number}</Link></td>
                    <td className="nowrap"><span className="num">{day(x.due_date)}</span> <DueWhen task={x} today={today} /></td>
                    <td className="num nowrap">{day(x.performed_date)}</td>
                    <td className="small">{x.completed_by_name ?? x.vendor_name ?? x.assigned_name ?? ""}</td>
                    <td>{x.status === "completed" ? <VerdictBadge verdict={x.verdict} kind={kind} /> : <TaskStateBadge task={x} />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {!pm && w.data.impacts.length > 0 && (
        <section className="panel">
          <div className="panel-head"><h3>{t("ia.title")}</h3></div>
          <ul className="cm-prev">
            {w.data.impacts.map((i) => (
              <li key={i.id}>
                <Link to={`/calibration/impacts/${i.id}`} className="mono-id">{i.number}</Link>
                <span className="small num">{i.period_from ? day(i.period_from) : t("ia.unknown")} – {day(i.period_to)}</span>
                <Badge tone={i.status === "approved" ? "ok" : "warn"}>{t(`ia.status.${i.status}` as StringKey)}</Badge>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!pm && <DriftPanel assetId={asset.id} />}
      {pm && <RunHoursPanel key={w.data.schedules?.length} assetId={asset.id} />}

      {planning && <SchedulePlanner kind={kind} assets={[asset]} schedule={planning === "new" ? undefined : planning} onClose={() => setPlanning(null)} onDone={done} />}
      {ending && <EndSchedule schedule={ending} onClose={() => setEnding(null)} onDone={done} />}
    </div>
  );
}
