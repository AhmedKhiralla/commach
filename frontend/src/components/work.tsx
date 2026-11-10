// Calibration work building blocks: task state and verdict badges, due-date wording, the generic
// e-signature dialog and the schedule sheet (one asset or many).
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { LuCalendarClock, LuSignature } from "react-icons/lu";
import { get, patch, post } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import type { StringKey } from "../lib/strings";
import type { AssetStatus, Category, Criticality, Vendor } from "../lib/types";
import { Badge, Button, Notice, PasswordField, SelectField, Sheet, Switch, TextField, errorText, fieldErrors, useToast } from "./ui";

export type TaskStatus = "open" | "in_progress" | "at_vendor" | "completed" | "cancelled";
export type Verdict = "pass" | "adjusted" | "fail";
export type TolUnit = "any" | "day" | "week" | "month" | "percent";

export interface Rule {
  up_to_days?: number | null; before_value: number | null; before_unit: TolUnit; after_value: number | null; after_unit: TolUnit;
}

export interface TaskRow {
  id: string; number: string; kind: "calibration" | "pm"; site_id: string; asset_id: string; schedule_id: string | null;
  due_date: string; window_start: string | null; window_end: string; status: TaskStatus; performer: "internal" | "external";
  vendor_id: string | null; assigned_user_id: string | null; accepted_at: string | null; performed_date: string | null;
  verdict: Verdict | null; completed_at: string | null; completed_by_name: string | null; reviewed_at: string | null;
  source: "normal" | "demo"; certificate_required: boolean; overdue: boolean; asset_code: string; asset_name_en: string;
  asset_name_ar: string | null; asset_status: AssetStatus; gmp_criticality: Criticality; site_code: string;
  location_code: string | null; category: Category; vendor_name: string | null; assigned_name: string | null;
  responsible_name: string | null;
}

export interface Schedule {
  id: string; asset_id: string; site_id: string; kind: "calibration" | "pm"; template_id: string | null; title: string | null;
  interval_value: number; interval_unit: "day" | "week" | "month" | "year"; basis: "floating" | "fixed";
  tol_before_value: number | null; tol_before_unit: TolUnit | null; tol_after_value: number | null; tol_after_unit: TolUnit | null;
  performer: "internal" | "external"; vendor_id: string | null; responsible_user_id: string | null;
  assignee_user_ids: string[]; assignee_roles: string[]; certificate_required: boolean; last_done: string | null;
  next_due: string | null; active: boolean; notes: string | null; template_code: string | null; template_version: number | null;
  template_title: string | null; template_status: string | null; vendor_name: string | null; responsible_name: string | null;
  assignee_names: string[]; rule?: Rule;
}

export interface Person { id: string; full_name: string; email: string; roles: { site_id: string; role: string }[] }

export const PERFORMER_ROLES = ["qa_calibration_officer", "engineering_supervisor", "engineering_manager", "maintenance_technician"] as const;

/** Date-only values are calendar days: format them without a time-zone shift. */
export function useDay() {
  const { date } = useI18n();
  return (iso: string | null | undefined) => (iso ? date(`${iso.slice(0, 10)}T00:00:00Z`, "UTC") : "");
}

export function daysBetween(a: string, b: string) {
  return Math.round((Date.parse(`${b.slice(0, 10)}T00:00:00Z`) - Date.parse(`${a.slice(0, 10)}T00:00:00Z`)) / 86400000);
}

export function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function TaskStateBadge({ task }: { task: Pick<TaskRow, "status" | "overdue"> }) {
  const { t } = useI18n();
  if (task.overdue) return <Badge tone="danger" dot>{t("ts.overdue")}</Badge>;
  const tone = ({ open: undefined, in_progress: "brand", at_vendor: "warn", completed: "ok", cancelled: undefined } as const)[task.status];
  return <Badge tone={tone} dot>{t(`ts.${task.status}` as StringKey)}</Badge>;
}

export function VerdictBadge({ verdict }: { verdict: Verdict | null | undefined }) {
  const { t } = useI18n();
  if (!verdict) return null;
  const tone = verdict === "pass" ? "ok" : verdict === "adjusted" ? "warn" : "danger";
  return <Badge tone={tone}>{t(`verdict.${verdict}` as StringKey)}</Badge>;
}

export function useRuleText() {
  const { t, qty } = useI18n();
  const part = (v: number | null, u: TolUnit) => (u === "any" ? t("unit.any") : `${qty(v ?? 0)} ${t(`unit.${u}` as StringKey)}`);
  return (r: Rule | null | undefined) => (r ? t("sch.ruleText", { before: part(r.before_value, r.before_unit), after: part(r.after_value, r.after_unit) }) : "");
}

/** "Every year", "Every 6 months". */
export function useEvery() {
  const { t } = useI18n();
  return (n: number, unit: string) => (n === 1 ? t(`every.${unit}1` as StringKey) : t("every.n", { n, unit: t(`unit.${unit}` as StringKey) }));
}

/** "12 days late", "in 5 days", "today", or the window end for closed work. */
export function DueWhen({ task, today }: { task: Pick<TaskRow, "due_date" | "window_end" | "status" | "overdue">; today: string }) {
  const { t } = useI18n();
  if (task.status === "completed" || task.status === "cancelled") return null;
  if (task.overdue) return <span className="small text-danger">{t("cw.daysLate", { n: daysBetween(task.window_end, today) })}</span>;
  const n = daysBetween(today, task.due_date);
  if (n === 0) return <span className="small text-warn">{t("cw.dueToday")}</span>;
  if (n < 0) return <span className="small text-warn">{t("cw.daysLate", { n: -n })}</span>;
  return <span className="small subtle">{t("cw.dueIn", { n })}</span>;
}

// ------------------------------------------------------------------ generic e-signature
export function SignDialog({ title, lead, meaning, onSign, onClose, submitLabel, reasonLabel, reasonRequired, danger, children, disabled }: {
  title: string; lead?: ReactNode; meaning: string; onSign: (v: { email: string; password: string; reason: string }) => Promise<void>;
  onClose: () => void; submitLabel?: string; reasonLabel?: string; reasonRequired?: boolean; danger?: boolean; children?: ReactNode;
  disabled?: boolean;
}) {
  const { t, tx } = useI18n();
  const { me } = useSession();
  const [email, setEmail] = useState(me?.user.email ?? "");
  const [password, setPassword] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ReactNode>(null);
  const submit = async () => {
    setBusy(true); setErr(null);
    try { await onSign({ email, password, reason: reason.trim() }); onClose(); }
    catch (e) {
      setPassword("");
      const probs = (e as { extra?: { problems?: string[] } }).extra?.problems;
      setErr(<>{errorText(e, t)}{probs?.length ? <ul className="plain-list small">{probs.map((p) => <li key={p}>{problemText(p, t, tx)}</li>)}</ul> : null}</>);
      setBusy(false);
    }
  };
  return (
    <Sheet title={title} lead={lead} onClose={onClose} onSubmit={submit} footer={<>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      <Button type="submit" variant={danger ? "danger" : "primary"} icon={<LuSignature />} busy={busy}
        disabled={disabled || !email || !password || (reasonRequired && reason.trim().length < 3)}>{submitLabel ?? t("sign.submit")}</Button>
    </>}>
      {err && <Notice tone="danger">{err}</Notice>}
      {children}
      <TextField label={reasonLabel ?? (reasonRequired ? t("sign.reason") : t("sg.reasonOptional"))} value={reason} onChange={setReason}
        maxLength={500} required={reasonRequired} />
      <div className="sign-statement">
        <span className="small muted">{t("sign.statement")}</span>
        <p>“{tx(`stmt.${meaning}`, meaning)}”</p>
      </div>
      <div className="grid-2">
        <TextField label={t("sign.email")} type="email" dir="ltr" autoComplete="username" value={email} onChange={setEmail} />
        <PasswordField label={t("sign.password")} value={password} onChange={setPassword} autoComplete="current-password" />
      </div>
      <p className="small subtle">{t("sg.legal")}</p>
    </Sheet>
  );
}

export function problemText(p: string, t: ReturnType<typeof useI18n>["t"], tx: ReturnType<typeof useI18n>["tx"]) {
  if (p.startsWith("standard_invalid:")) return t("task.p.standard_invalid", { code: p.split(":")[1] });
  return tx(`task.p.${p}`, tx(`ia.${p}`, p));
}

// ------------------------------------------------------------------ schedule sheet
interface TemplateOpt { id: string; code: string; version: number; title: string; status: string; item_count: number }

export function SchedulePlanner({ assets, schedule, onClose, onDone }: {
  assets: { id: string; code: string; site_id?: string }[]; schedule?: Schedule; onClose: () => void; onDone: () => void;
}) {
  const { t } = useI18n();
  const toast = useToast();
  const day = useDay();
  const ruleText = useRuleText();
  const editing = !!schedule;
  const [templates, setTemplates] = useState<TemplateOpt[]>([]);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [templateId, setTemplateId] = useState(schedule?.template_id ?? "");
  const [ivalue, setIvalue] = useState(String(schedule?.interval_value ?? 12));
  const [iunit, setIunit] = useState<Schedule["interval_unit"]>(schedule?.interval_unit ?? "month");
  const [basis, setBasis] = useState<Schedule["basis"]>(schedule?.basis ?? "floating");
  const [own, setOwn] = useState(!!(schedule?.tol_after_unit || schedule?.tol_before_unit));
  const [bv, setBv] = useState(schedule?.tol_before_value != null ? String(schedule.tol_before_value) : "");
  const [bu, setBu] = useState<TolUnit>(schedule?.tol_before_unit ?? "any");
  const [av, setAv] = useState(schedule?.tol_after_value != null ? String(schedule.tol_after_value) : "15");
  const [au, setAu] = useState<TolUnit>(schedule?.tol_after_unit ?? "day");
  const [performer, setPerformer] = useState<Schedule["performer"]>(schedule?.performer ?? "internal");
  const [vendorId, setVendorId] = useState(schedule?.vendor_id ?? "");
  const [responsible, setResponsible] = useState(schedule?.responsible_user_id ?? "");
  const [roles, setRoles] = useState<string[]>(schedule?.assignee_roles ?? ["qa_calibration_officer"]);
  const [users, setUsers] = useState<string[]>(schedule?.assignee_user_ids ?? []);
  const [certReq, setCertReq] = useState<boolean | null>(schedule ? schedule.certificate_required : null);
  const [firstDue, setFirstDue] = useState(schedule?.next_due ?? "");
  const [lastDone, setLastDone] = useState("");
  const [stagger, setStagger] = useState("0");
  const [notes, setNotes] = useState(schedule?.notes ?? "");
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<{ dates: { due: string; window_start: string | null; window_end: string }[]; rule: Rule } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [errs, setErrs] = useState<Record<string, string>>({});

  useEffect(() => {
    get<{ items: TemplateOpt[] }>("/api/templates?kind=calibration").then((r) => setTemplates(r.items)).catch(() => {});
    get<{ items: Vendor[] }>("/api/vendors").then((r) => setVendors(r.items.filter((v) => v.active))).catch(() => {});
    get<{ items: Person[] }>("/api/people").then((r) => setPeople(r.items)).catch(() => {});
  }, []);

  const tol = own ? {
    tol_before_unit: bu, tol_before_value: bu === "any" ? null : Number(bv || 0),
    tol_after_unit: au === "any" ? "day" : au, tol_after_value: Number(av || 0),
  } : { tol_before_unit: null, tol_before_value: null, tol_after_unit: null, tol_after_value: null };
  const iv = Math.max(1, Number(ivalue) || 1);
  const previewKey = JSON.stringify([iv, iunit, basis, tol, firstDue, lastDone]);
  useEffect(() => {
    const h = setTimeout(() => {
      post("/api/schedules/preview", { kind: "calibration", interval_value: iv, interval_unit: iunit, basis, ...tol,
        first_due: firstDue || null, last_done: !firstDue && lastDone ? lastDone : null })
        .then(setPreview).catch(() => setPreview(null));
    }, 250);
    return () => clearTimeout(h);
  }, [previewKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const siteIds = new Set(assets.map((a) => a.site_id).filter(Boolean));
  const candidates = useMemo(() => people.filter((p) => p.roles.some((r) => (PERFORMER_ROLES as readonly string[]).includes(r.role)
    && (siteIds.size === 0 || siteIds.has(r.site_id)))), [people, assets]); // eslint-disable-line react-hooks/exhaustive-deps
  const roleOf = (p: Person) => Array.from(new Set(p.roles.filter((r) => siteIds.size === 0 || siteIds.has(r.site_id)).map((r) => t(`role.${r.role}` as StringKey)))).join(", ");
  const certRequired = certReq ?? performer === "external";

  const submit = async () => {
    setBusy(true); setErr(null); setErrs({});
    const body = {
      template_id: templateId || null, interval_value: iv, interval_unit: iunit, basis, ...tol, performer,
      vendor_id: performer === "external" ? vendorId || null : null, responsible_user_id: responsible || null,
      assignee_roles: roles, assignee_user_ids: users, certificate_required: certRequired, notes: notes || null,
    };
    try {
      if (schedule) {
        await patch(`/api/schedules/${schedule.id}`, { ...body, next_due: firstDue && firstDue !== schedule.next_due ? firstDue : null, reason: reason || null });
        toast(t("list.saved"));
      } else {
        const r = await post<{ created: unknown[]; skipped: unknown[] }>("/api/schedules", {
          ...body, kind: "calibration", asset_ids: assets.map((a) => a.id), first_due: firstDue || null,
          last_done: !firstDue && lastDone ? lastDone : null, stagger_days: Number(stagger) || 0,
        });
        toast(t("sch.created", { n: r.created.length }) + (r.skipped.length ? ` · ${t("sch.skipped", { n: r.skipped.length })}` : ""));
      }
      onDone(); onClose();
    } catch (e) { setErr(errorText(e, t)); setErrs(fieldErrors(e)); setBusy(false); }
  };

  const unitOpts = (["day", "week", "month", "year"] as const).map((u) => ({ value: u, label: t(`unit.${u}` as StringKey) }));
  const tolOpts = (withAny: boolean) => (withAny ? ["any", "day", "week", "month", "percent"] : ["day", "week", "month", "percent"])
    .map((u) => ({ value: u, label: t(`unit.${u}` as StringKey) }));
  const title = editing ? t("sch.titleEdit") : t("sch.title");
  const lead = assets.length === 1 ? assets[0].code : t("sch.assets", { n: assets.length });
  return (
    <Sheet wide title={title} lead={lead} onClose={onClose} onSubmit={submit} footer={<>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      <Button type="submit" variant="primary" busy={busy} icon={<LuCalendarClock />}
        disabled={performer === "external" && !vendorId}>{editing ? t("common.save") : t("sch.title")}</Button>
    </>}>
      {err && <Notice tone="danger">{err}</Notice>}
      {assets.length > 1 && <div className="chip-row">{assets.slice(0, 40).map((a) => <span key={a.id} className="chip mono-id">{a.code}</span>)}
        {assets.length > 40 && <span className="chip">+{assets.length - 40}</span>}</div>}
      <div className="cm-sched">
        <div className="stack">
          <SelectField label={t("sch.template")} value={templateId} onChange={setTemplateId} error={errs.template_id}
            options={[{ value: "", label: t("sch.noTemplate") }, ...templates.map((x) => ({
              value: x.id, label: `${x.code} v${x.version} · ${x.title}${x.status === "draft" ? ` (${t("tpl.status.draft")})` : ""}`,
            }))]} />
          <div className="grid-2">
            <div className="field">
              <label htmlFor="sch-every">{t("sch.every")}</label>
              <div className="row" style={{ gap: 6 }}>
                <input id="sch-every" className="input" type="number" min={1} max={3650} value={ivalue} onChange={(e) => setIvalue(e.target.value)} style={{ width: 90 }} />
                <select className="select" aria-label={t("sch.every")} value={iunit} onChange={(e) => setIunit(e.target.value as Schedule["interval_unit"])}>
                  {unitOpts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
            </div>
            <SelectField label={t("sch.basis")} value={basis} onChange={(v) => setBasis(v as Schedule["basis"])}
              options={[{ value: "floating", label: t("basis.floating") }, { value: "fixed", label: t("basis.fixed") }]} />
          </div>
          <div className="field">
            <span className="field-label">{t("sch.window")}</span>
            {preview && !own && <div className="small muted">{t("sch.companyRule", { rule: ruleText(preview.rule) })}</div>}
            <label className="row small"><Switch checked={own} onChange={setOwn} label={t("sch.ownRule")} />{t("sch.ownRule")}</label>
            {own && (
              <div className="grid-2">
                <div className="field">
                  <label htmlFor="sch-early">{t("sch.early")}</label>
                  <div className="row" style={{ gap: 6 }}>
                    {bu !== "any" && <input id="sch-early" className="input" type="number" min={0} value={bv} onChange={(e) => setBv(e.target.value)} style={{ width: 80 }} />}
                    <select className="select" aria-label={t("sch.early")} value={bu} onChange={(e) => setBu(e.target.value as TolUnit)}>
                      {tolOpts(true).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                </div>
                <div className="field">
                  <label htmlFor="sch-late">{t("sch.late")}</label>
                  <div className="row" style={{ gap: 6 }}>
                    <input id="sch-late" className="input" type="number" min={0} value={av} onChange={(e) => setAv(e.target.value)} style={{ width: 80 }} />
                    <select className="select" aria-label={t("sch.late")} value={au} onChange={(e) => setAu(e.target.value as TolUnit)}>
                      {tolOpts(false).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                </div>
              </div>
            )}
          </div>
          <div className="field">
            <span className="field-label">{t("sch.performer")}</span>
            <div className="seg" role="group" aria-label={t("sch.performer")}>
              {(["internal", "external"] as const).map((p) => (
                <button key={p} type="button" aria-pressed={performer === p} onClick={() => setPerformer(p)}>{t(`perf.${p}` as StringKey)}</button>
              ))}
            </div>
          </div>
          {performer === "external" && (
            <SelectField label={t("sch.vendor")} value={vendorId} onChange={setVendorId} error={errs.vendor_id}
              options={[{ value: "", label: "—" }, ...vendors.slice().sort((a, b) => Number(b.kind === "calibration_lab") - Number(a.kind === "calibration_lab"))
                .map((v) => ({ value: v.id, label: `${v.code} · ${v.name}${v.accreditation_no ? ` (${v.accreditation_no})` : ""}` }))]} />
          )}
          <SelectField label={t("sch.responsible")} hint={performer === "external" ? t("sch.responsibleHint") : undefined}
            value={responsible} onChange={setResponsible}
            options={[{ value: "", label: "—" }, ...candidates.map((p) => ({ value: p.id, label: `${p.full_name} · ${roleOf(p)}` }))]} />
          <div className="field">
            <span className="field-label">{t("sch.roles")}</span>
            <div className="chip-row">
              {PERFORMER_ROLES.map((r) => (
                <button key={r} type="button" className="chip" aria-pressed={roles.includes(r)}
                  onClick={() => setRoles((x) => (x.includes(r) ? x.filter((y) => y !== r) : [...x, r]))}>{t(`role.${r}` as StringKey)}</button>
              ))}
            </div>
          </div>
          {candidates.length > 0 && (
            <div className="field">
              <span className="field-label">{t("sch.users")}</span>
              <div className="chip-row">
                {candidates.map((p) => (
                  <button key={p.id} type="button" className="chip" aria-pressed={users.includes(p.id)} title={roleOf(p)}
                    onClick={() => setUsers((x) => (x.includes(p.id) ? x.filter((y) => y !== p.id) : [...x, p.id]))}>{p.full_name}</button>
                ))}
              </div>
            </div>
          )}
          <label className="row small"><Switch checked={certRequired} onChange={setCertReq} label={t("sch.certRequired")} />{t("sch.certRequired")}</label>
          <div className="grid-2">
            <TextField label={editing ? t("sch.nextDue") : t("sch.firstDue")} type="date" value={firstDue} onChange={setFirstDue} />
            {!editing && <TextField label={t("sch.lastDone")} type="date" value={lastDone} onChange={setLastDone} disabled={!!firstDue} />}
          </div>
          {!editing && assets.length > 1 && <TextField label={t("sch.stagger")} type="number" min={0} max={60} value={stagger} onChange={setStagger} />}
          <Field2 label={t("af.notes")}><textarea className="textarea" aria-label={t("af.notes")} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} /></Field2>
          {editing && <TextField label={t("sign.reason")} hint={t("sch.reasonHint")} value={reason} onChange={setReason} maxLength={500} error={errs.reason} />}
        </div>
        <aside className="cm-preview" aria-live="polite">
          <h4>{t("sch.preview")}</h4>
          {preview ? (
            <>
              <p className="small muted">{ruleText(preview.rule)}</p>
              <ol>
                {preview.dates.slice(0, 8).map((d) => (
                  <li key={d.due}>
                    <strong className="num">{day(d.due)}</strong>
                    <span className="small subtle">{d.window_start ? t("cw.windowRange", { start: day(d.window_start), end: day(d.window_end) })
                      : t("cw.windowAny", { end: day(d.window_end) })}</span>
                  </li>
                ))}
              </ol>
              {basis === "floating" && <p className="small subtle">{t("sch.floatingNote")}</p>}
            </>
          ) : <div className="skeleton" style={{ width: "70%" }} />}
        </aside>
      </div>
    </Sheet>
  );
}

function Field2({ label, children }: { label: string; children: ReactNode }) {
  return <div className="field"><span className="field-label">{label}</span>{children}</div>;
}
