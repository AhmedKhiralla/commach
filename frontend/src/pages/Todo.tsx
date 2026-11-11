// My tasks: the signed-in person's work and approvals, with the calendar feed, e-mail settings and delegation.
import { useEffect, useState, type ReactNode } from "react";
import {
  LuCalendarPlus, LuCheck, LuCopy, LuListTodo, LuMail, LuOctagonAlert, LuSignature, LuUserRoundCheck, LuX,
} from "react-icons/lu";
import { CategoryIcon } from "../components/cm";
import { Badge, Button, Dialog, Empty, LoadError, Notice, PageHead, SelectField, Sheet, Switch, TextField, errorText, useLoad, useToast } from "../components/ui";
import { DueWhen, SignDialog, TaskStateBadge, todayIso, useDay, type Person } from "../components/work";
import { get, post, put } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link, navigate } from "../lib/router";
import { useSession } from "../lib/session";
import type { StringKey } from "../lib/strings";
import type { Category } from "../lib/types";

interface TodoTask {
  id: string; number: string; kind: "calibration" | "pm"; site_id: string; asset_id: string; due_date: string; window_start: string | null;
  window_end: string; status: "open" | "in_progress" | "at_vendor" | "completed" | "cancelled"; accepted_at: string | null; overdue: boolean;
  asset_code: string; asset_name_en: string; asset_name_ar: string | null; site_code: string; template_title: string | null;
  vendor_name: string | null; plan_status: string | null; reschedule_status: string | null; delegated?: boolean;
  completed_by_name?: string | null; category?: Category;
}
interface Approval {
  type: "reschedule" | "plan" | "template" | "impact"; id: string; kind?: "calibration" | "pm"; number?: string; asset_code?: string;
  old_due?: string; new_due?: string; reason?: string; requested_by_name?: string; status?: string; within_tolerance?: boolean;
  year?: number; site_id?: string; site_code?: string; code?: string; version?: number; title?: string; task_id?: string;
}
interface Todo {
  today: string; overdue: TodoTask[]; soon: TodoTask[]; later: TodoTask[]; to_accept: TodoTask[]; to_review: TodoTask[];
  approvals: Approval[]; deviations: { id: string; number: string; title: string; asset_code: string; created_at: string }[];
  counts: Record<string, number>; delegated_from_names: string[];
}

const base = (k: string) => (k === "pm" ? "/maintenance" : "/calibration");

function TaskList({ items, today, review }: { items: TodoTask[]; today: string; review?: boolean }) {
  const { t, lang } = useI18n();
  const day = useDay();
  return (
    <ul className="cm-todo-list">
      {items.map((x) => (
        <li key={x.id} className={x.overdue ? "late" : ""}>
          <Link to={`${base(x.kind)}/tasks/${x.id}`} className="cm-todo-row">
            <span className={`cm-todo-kind k-${x.kind}`}>{x.kind === "pm" ? "PM" : "CAL"}</span>
            <span className="grow">
              <span className="cell-main"><span className="mono-id">{x.asset_code}</span> · {lang === "ar" && x.asset_name_ar ? x.asset_name_ar : x.asset_name_en}</span>
              <span className="small subtle">{x.number}{x.template_title ? ` · ${x.template_title}` : ""}{x.vendor_name ? ` · ${x.vendor_name}` : ""} · {x.site_code}</span>
            </span>
            <span className="cm-todo-when">
              {review ? <span className="small subtle">{x.completed_by_name}</span> : <><span className="num small">{day(x.due_date)}</span><DueWhen task={x} today={today} /></>}
            </span>
            <span className="row wrap cm-todo-tags">
              {!review && <TaskStateBadge task={x} />}
              {!review && !x.accepted_at && x.status === "open" && <Badge tone="warn">{t("todo.toAccept")}</Badge>}
              {x.delegated && <Badge>{t("todo.delegatedTag")}</Badge>}
              {x.reschedule_status && <Badge tone="warn">{t("todo.rescheduleAsk")}</Badge>}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function Section({ title, n, children, tone }: { title: string; n: number; children: ReactNode; tone?: "danger" | "warn" }) {
  if (!n) return null;
  return (
    <section className={`panel cm-todo-sec ${tone ?? ""}`}>
      <div className="panel-head"><h3>{title}</h3><span className="cm-count">{n}</span></div>
      {children}
    </section>
  );
}

// ------------------------------------------------------------------ dialogs
function CalendarDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const [st, setSt] = useState<{ calendar: boolean } | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { get<{ calendar: boolean }>("/api/me/notify").then(setSt).catch(() => {}); }, []);
  const create = async () => { setBusy(true); try { const r = await post<{ url: string }>("/api/me/calendar"); setUrl(r.url); setSt({ calendar: true }); } finally { setBusy(false); } };
  return (
    <Dialog title={t("ic.title")} onClose={onClose} footer={<>
      {st?.calendar && <Button variant="ghost" onClick={async () => { await post("/api/me/calendar/revoke"); setUrl(null); setSt({ calendar: false }); }}>{t("ic.revoke")}</Button>}
      <Button busy={busy} icon={<LuCalendarPlus />} onClick={create}>{st?.calendar ? t("ic.recreate") : t("ic.create")}</Button>
      <Button variant="primary" onClick={onClose}>{t("common.close")}</Button>
    </>}>
      <p>{t("ic.lead")}</p>
      {url ? (
        <div className="stack">
          <div className="secret"><code>{url}</code>
            <Button size="sm" icon={copied ? <LuCheck /> : <LuCopy />} onClick={async () => { try { await navigator.clipboard.writeText(url); setCopied(true); } catch { /* blocked */ } }}>
              {copied ? t("ic.copied") : t("ic.copy")}</Button></div>
          <ul className="plain-list small"><li>{t("ic.google")}</li><li>{t("ic.outlook")}</li></ul>
          <Notice tone="warn">{t("ic.private")}</Notice>
        </div>
      ) : <p className="small muted">{st?.calendar ? t("ic.active") : t("ic.none")}</p>}
    </Dialog>
  );
}

function EmailDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const [n, setN] = useState<{ digest: boolean; escalations: boolean; email_configured: boolean; mail_from: string; digest_hour: number; email: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { get("/api/me/notify").then(setN).catch(() => {}); }, []);
  const set = async (k: "digest" | "escalations", v: boolean) => setN(await put("/api/me/notify", { [k]: v }));
  return (
    <Dialog title={t("em.title")} onClose={onClose} footer={<>
      <Button busy={busy} icon={<LuMail />} onClick={async () => {
        setBusy(true);
        try { const r = await post<{ sent: number; configured: boolean }>("/api/me/notify/test"); toast(r.configured && r.sent ? t("em.testSent") : t("em.testQueued")); }
        finally { setBusy(false); }
      }}>{t("em.test")}</Button>
      <Button variant="primary" onClick={onClose}>{t("common.close")}</Button>
    </>}>
      {n && <>
        <p>{t("em.lead", { hour: n.digest_hour })}</p>
        {!n.email_configured && <Notice tone="warn">{t("em.notConfigured")}</Notice>}
        <label className="row"><Switch checked={n.digest} onChange={(v) => set("digest", v)} label={t("em.digest")} /><span>{t("em.digest")}</span></label>
        <label className="row"><Switch checked={n.escalations} onChange={(v) => set("escalations", v)} label={t("em.escalations")} /><span>{t("em.escalations")}</span></label>
        <p className="small subtle">{t("em.to", { email: n.email, from: n.mail_from })}</p>
      </>}
    </Dialog>
  );
}

interface Delegation { id: string; from_user: string; to_user: string; from_name: string; to_name: string; starts_on: string; ends_on: string; reason: string; active: boolean; current: boolean }

function DelegateSheet({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { me } = useSession();
  const day = useDay();
  const toast = useToast();
  const [people, setPeople] = useState<Person[]>([]);
  const [list, setList] = useState<Delegation[]>([]);
  const [to, setTo] = useState("");
  const [from, setFrom] = useState(todayIso());
  const [until, setUntil] = useState(todayIso());
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = () => get<{ items: Delegation[] }>("/api/delegations").then((r) => setList(r.items)).catch(() => {});
  useEffect(() => { get<{ items: Person[] }>("/api/people").then((r) => setPeople(r.items.filter((p) => p.id !== me?.user.id))).catch(() => {}); load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const submit = async () => {
    setBusy(true); setErr(null);
    try { await post("/api/delegations", { to_user_id: to, starts_on: from, ends_on: until, reason }); toast(t("list.saved")); setReason(""); load(); onDone(); }
    catch (e) { setErr(errorText(e, t)); } finally { setBusy(false); }
  };
  return (
    <Sheet title={t("dg.title")} lead={t("dg.lead")} onClose={onClose} onSubmit={submit} footer={<>
      <Button onClick={onClose}>{t("common.close")}</Button>
      <Button type="submit" variant="primary" busy={busy} disabled={!to || reason.trim().length < 3}>{t("dg.create")}</Button>
    </>}>
      {err && <Notice tone="danger">{err}</Notice>}
      <SelectField label={t("dg.to")} value={to} onChange={setTo} options={[{ value: "", label: "—" }, ...people.map((p) => ({ value: p.id, label: p.full_name }))]} />
      <div className="grid-2">
        <TextField label={t("dg.from")} type="date" value={from} onChange={setFrom} />
        <TextField label={t("dg.until")} type="date" value={until} min={from} onChange={setUntil} />
      </div>
      <TextField label={t("dg.reason")} value={reason} onChange={setReason} maxLength={500} />
      <h3 className="sheet-section">{t("dg.current")}</h3>
      {list.filter((d) => d.active).length === 0 ? <p className="small subtle">{t("dg.none")}</p> : (
        <ul className="cm-prev">
          {list.filter((d) => d.active).map((d) => (
            <li key={d.id}>
              <span className="grow small">{t("dg.row", { from: d.from_name, to: d.to_name, start: day(d.starts_on), end: day(d.ends_on) })}
                <span className="subtle"> · {d.reason}</span></span>
              {d.current && <Badge tone="ok">{t("dg.active")}</Badge>}
              <Button size="sm" variant="ghost" onClick={async () => { await post(`/api/delegations/${d.id}/end`); load(); onDone(); }}>{t("dg.end")}</Button>
            </li>
          ))}
        </ul>
      )}
    </Sheet>
  );
}

function RejectDialog({ onClose, onReject }: { onClose: () => void; onReject: (reason: string) => Promise<void> }) {
  const { t } = useI18n();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Dialog title={t("todo.reject")} onClose={onClose} footer={<>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      <Button variant="danger" busy={busy} disabled={reason.trim().length < 3} onClick={async () => {
        setBusy(true); setErr(null);
        try { await onReject(reason); onClose(); } catch (e) { setErr(errorText(e, t)); setBusy(false); }
      }}>{t("todo.reject")}</Button>
    </>}>
      <TextField label={t("todo.rejectReason")} value={reason} onChange={setReason} maxLength={500} />
      {err && <Notice tone="danger">{err}</Notice>}
    </Dialog>
  );
}

// ------------------------------------------------------------------ page
export function TodoPage() {
  const { t } = useI18n();
  const day = useDay();
  const toast = useToast();
  const { data, error, reload } = useLoad(() => get<Todo>("/api/todo"));
  const [dialog, setDialog] = useState<null | "calendar" | "email" | "delegate">(null);
  const [signing, setSigning] = useState<Approval | null>(null);
  const [rejecting, setRejecting] = useState<Approval | null>(null);
  if (error) return <div className="panel"><LoadError error={error} onRetry={reload} /></div>;
  const c = data?.counts;
  const nothing = data && !["overdue", "soon", "later", "to_review", "approvals", "deviations"].some((k) => data.counts[k]);
  const what = (k?: string) => t(k === "pm" ? "todo.ap.pm" : "todo.ap.calibration");
  const apLine = (a: Approval): { line: ReactNode; sub?: ReactNode; link?: string } => {
    switch (a.type) {
      case "reschedule": return {
        line: t("todo.ap.reschedule", { number: a.number ?? "", asset: a.asset_code ?? "", old: day(a.old_due), new: day(a.new_due) }),
        sub: <>{t("todo.ap.rescheduleBy", { name: a.requested_by_name ?? "", reason: a.reason ?? "" })}
          {!a.within_tolerance && <> · <strong className="text-warn">{a.status === "supervisor_approved" ? t("todo.ap.qaStep") : t("todo.ap.beyond")}</strong></>}</>,
        link: `${base(a.kind ?? "")}/tasks/${a.task_id}` };
      case "plan": return { line: t("todo.ap.plan", { what: what(a.kind), year: a.year ?? "", site: a.site_code ?? "" }), link: `/plans/${a.kind}?year=${a.year}&site_id=${a.site_id}` };
      case "template": return { line: t("todo.ap.template", { what: t(a.kind === "pm" ? "todo.ap.tplPm" : "todo.ap.tplCal"), code: a.code ?? "", version: a.version ?? 1, title: a.title ?? "" }),
        link: `${base(a.kind ?? "")}/templates` };
      default: return { line: t("todo.ap.impact", { number: a.number ?? "", asset: a.asset_code ?? "" }), link: `/calibration/impacts/${a.id}` };
    }
  };
  return (
    <>
      <PageHead title={t("todo.title")} lead={<>{t("todo.lead")}{data?.delegated_from_names.length ? ` ${t("todo.delegated", { names: data.delegated_from_names.join(", ") })}` : ""}</>} actions={<>
        <Button icon={<LuCalendarPlus />} onClick={() => setDialog("calendar")}>{t("todo.calendar")}</Button>
        <Button icon={<LuMail />} onClick={() => setDialog("email")}>{t("todo.email")}</Button>
        <Button icon={<LuUserRoundCheck />} onClick={() => setDialog("delegate")}>{t("todo.delegate")}</Button>
      </>} />
      {!data ? <div className="panel"><div className="panel-body"><div className="skeleton" style={{ width: "50%" }} /></div></div> : nothing ? (
        <div className="panel"><Empty icon={<LuListTodo />} title={t("todo.empty")} body={t("todo.emptyBody")} /></div>
      ) : (
        <div className="cm-todo-grid">
          <div className="stack">
            <Section title={t("todo.overdue")} n={c!.overdue} tone="danger"><TaskList items={data.overdue} today={data.today} /></Section>
            <Section title={t("todo.soon")} n={c!.soon} tone="warn"><TaskList items={data.soon} today={data.today} /></Section>
            <Section title={t("todo.later")} n={c!.later}><TaskList items={data.later} today={data.today} /></Section>
          </div>
          <div className="stack">
            <Section title={t("todo.approvals")} n={c!.approvals}>
              <ul className="cm-todo-ap">
                {data.approvals.map((a) => {
                  const x = apLine(a);
                  return (
                    <li key={`${a.type}-${a.id}`}>
                      <div className="grow">
                        <div className="cell-main">{x.line}</div>
                        {x.sub && <div className="small subtle">{x.sub}</div>}
                      </div>
                      <div className="row wrap" style={{ gap: 6 }}>
                        {a.type === "reschedule" ? <>
                          <Button size="sm" variant="primary" icon={<LuSignature />} onClick={() => setSigning(a)}>{t("todo.approve")}</Button>
                          <Button size="sm" variant="ghost" icon={<LuX />} onClick={() => setRejecting(a)}>{t("todo.reject")}</Button>
                        </> : null}
                        {x.link && <Button size="sm" onClick={() => navigate(x.link!)}>{t("todo.open")}</Button>}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </Section>
            <Section title={t("todo.toReview")} n={c!.to_review}><TaskList items={data.to_review} today={data.today} review /></Section>
            <Section title={t("todo.deviations")} n={c!.deviations} tone="danger">
              <ul className="cm-prev">
                {data.deviations.map((d) => (
                  <li key={d.id}><LuOctagonAlert aria-hidden className="text-danger" />
                    <Link to={`/deviations/${d.id}`} className="grow"><span className="mono-id">{d.number}</span> · {d.title}</Link></li>
                ))}
              </ul>
            </Section>
          </div>
        </div>
      )}
      {dialog === "calendar" && <CalendarDialog onClose={() => setDialog(null)} />}
      {dialog === "email" && <EmailDialog onClose={() => setDialog(null)} />}
      {dialog === "delegate" && <DelegateSheet onClose={() => setDialog(null)} onDone={reload} />}
      {signing && (
        <SignDialog title={`${t("todo.approve")} · ${signing.number}`} lead={apLine(signing).line}
          meaning={signing.status === "supervisor_approved" ? "reschedule_qa_approved" : "reschedule_approved"} onClose={() => setSigning(null)}
          onSign={async (v) => { await post(`/api/reschedules/${signing.id}/approve`, { ...v, reason: v.reason || null }); toast(t("list.saved")); reload(); }} />
      )}
      {rejecting && <RejectDialog onClose={() => setRejecting(null)} onReject={async (reason) => { await post(`/api/reschedules/${rejecting.id}/reject`, { reason }); reload(); }} />}
    </>
  );
}

// ------------------------------------------------------------------ reschedule from the task page
export function RescheduleSheet({ task, onClose, onDone, initialDate, initialReason }: {
  task: { id: string; number: string; due_date: string; window_start: string | null; window_end: string }; onClose: () => void; onDone: () => void;
  initialDate?: string; initialReason?: string;
}) {
  const { t } = useI18n();
  const day = useDay();
  const toast = useToast();
  const [due, setDue] = useState(initialDate ?? "");
  const [reason, setReason] = useState(initialReason ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const window = task.window_start ? t("cw.windowRange", { start: day(task.window_start), end: day(task.window_end) }) : t("cw.windowAny", { end: day(task.window_end) });
  const within = due ? (!task.window_start || due >= task.window_start) && due <= task.window_end : null;
  return (
    <Sheet title={`${t("rs.title")} · ${task.number}`} lead={t("rs.lead", { window })} onClose={onClose} onSubmit={async () => {
      setBusy(true); setErr(null);
      try { await post(`/api/tasks/${task.id}/reschedule`, { new_due: due, reason }); toast(t("rs.sent")); onDone(); onClose(); }
      catch (e) { setErr(errorText(e, t)); setBusy(false); }
    }} footer={<>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      <Button type="submit" variant="primary" busy={busy} disabled={!due || reason.trim().length < 3}>{t("rs.send")}</Button>
    </>}>
      {err && <Notice tone="danger">{err}</Notice>}
      <TextField label={t("rs.newDue")} type="date" min={todayIso()} value={due} onChange={setDue} />
      {within !== null && <Notice tone={within ? "info" : "warn"}>{within ? t("rs.within") : t("rs.beyond")}</Notice>}
      <TextField label={t("rs.reason")} value={reason} onChange={setReason} maxLength={500} />
    </Sheet>
  );
}

export function RescheduleHistory({ items, me, onChanged }: {
  items: { id: string; old_due: string; new_due: string; status: string; reason: string; requested_by: string; requested_by_name: string;
    supervisor_name: string | null; qa_name: string | null }[]; me: string; onChanged: () => void;
}) {
  const { t } = useI18n();
  const day = useDay();
  if (!items.length) return null;
  return (
    <section className="panel">
      <div className="panel-head"><h3>{t("rs.history")}</h3></div>
      <ul className="cm-prev cm-rs">
        {items.map((r) => (
          <li key={r.id}>
            <span className="grow small">{t("rs.row", { old: day(r.old_due), new: day(r.new_due), name: r.requested_by_name, reason: r.reason })}
              {(r.supervisor_name || r.qa_name) && <span className="subtle"> · {[r.supervisor_name, r.qa_name].filter(Boolean).join(", ")}</span>}</span>
            <Badge tone={r.status === "approved" ? "ok" : r.status === "rejected" ? "danger" : r.status === "withdrawn" ? undefined : "warn"}>
              {t(`rs.status.${r.status}` as StringKey)}</Badge>
            {r.requested_by === me && ["pending", "supervisor_approved"].includes(r.status) && (
              <Button size="sm" variant="ghost" onClick={async () => { await post(`/api/reschedules/${r.id}/reject`, { reason: "Withdrawn by the requester" }); onChanged(); }}>{t("rs.withdraw")}</Button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
