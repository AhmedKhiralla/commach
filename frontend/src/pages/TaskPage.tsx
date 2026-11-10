// One calibration task: accept, send to the vendor, record readings and standards, read the certificate with AI,
// complete and sign, review (four eyes), cancel.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  LuArrowLeft, LuCalendarClock, LuCheck, LuCircleCheck, LuCircleX, LuFileText, LuHandshake, LuPlus, LuSave, LuSignature, LuSparkles,
  LuTrash2, LuTruck, LuUpload, LuX,
} from "react-icons/lu";
import { StatusBadge } from "../components/cm";
import { PrintFormButton } from "../components/forms";
import { Badge, Button, LoadError, Notice, TextField, errorText, useLoad, useToast } from "../components/ui";
import {
  DueWhen, SignDialog, TaskStateBadge, VerdictBadge, todayIso, useDay, useEvery, useRuleText,
  type Schedule, type TaskRow, type Verdict,
} from "../components/work";
import { get, post, put, upload } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link, navigate } from "../lib/router";
import { useSession } from "../lib/session";
import type { StringKey } from "../lib/strings";
import { nameOf, type AssetStatus, type Category } from "../lib/types";
import { PmChecklist, PmParts, type Part } from "./PmChecklist";
import { RescheduleHistory, RescheduleSheet } from "./Todo";

interface Result {
  id: string | null; item_id: string | null; seq: number; item_type: string; parameter: string; nominal: number | null;
  unit: string | null; low_limit: number | null; high_limit: number | null; as_found: number | null; as_left: number | null;
  uncertainty: number | null; value_text: string | null; ok_found: boolean | null; ok_left: boolean | null; notes: string | null;
  file_id?: string | null; required?: boolean; page_ref?: string | null; item_notes?: string | null;
}
interface Standard { id?: string; standard_asset_id: string | null; description: string | null; cert_ref: string | null; valid_until: string | null }
interface StdOption { id: string; code: string; name_en: string; name_ar: string | null; status: AssetStatus; valid_until: string | null; last_certificate: string | null; usable: boolean }
interface Sig { id: string; meaning: string; statement: string; user_name: string; user_email: string; signed_at: string; record_hash: string }
interface FileMeta { id: string; filename: string; mime: string; sha256: string; page_count?: number | null }
interface Task extends TaskRow {
  accepted_by_name: string | null; sent_at: string | null; performed_by_name: string | null; certificate_no: string | null;
  certificate_file_id: string | null; env_temperature: string | null; env_humidity: string | null; remarks: string | null;
  cancel_reason: string | null; reviewed_by_name: string | null; template_id: string | null; man_hours: number | null;
  released: boolean; plan_status: string | null; parts: Part[];
  asset: { id: string; code: string; name_en: string; name_ar: string | null; status: AssetStatus; serial_no: string | null;
    manufacturer: string | null; model: string | null; range_min: number | null; range_max: number | null; unit: string | null;
    resolution: string | null; accuracy: string | null; class_name_en: string; class_name_ar: string | null; category: Category };
  schedule: (Schedule & { rule?: unknown }) | null;
  template: { id: string; code: string; version: number; title: string; method_ref: string | null; standards_note: string | null;
    environment_note: string | null; status: string; est_minutes?: number | null;
    spare_parts?: { name: string; part_no: string | null; qty: number | null; unit: string | null }[] } | null;
  results: Result[]; verdict_preview: Verdict | null; standards: Standard[]; standard_options: StdOption[];
  certificate: FileMeta | null; previous: { id: string; number: string; performed_date: string; verdict: Verdict; completed_by_name: string }[];
  signature: Sig | null; review_signature: Sig | null; impact: { id: string; number: string; status: string } | null;
  ai: { id: string; model: string; status: string; changed_fields: Record<string, { ai: unknown; final: unknown }> | null; reviewed_by_name: string | null } | null;
  perms: { accept: boolean; edit: boolean; complete: boolean; send: boolean; review: boolean; cancel: boolean; ai: boolean; reschedule?: boolean };
  reschedules: { id: string; old_due: string; new_due: string; status: string; reason: string; requested_by: string; requested_by_name: string;
    supervisor_name: string | null; qa_name: string | null }[];
  escalations: { level: number; trigger: string; due_date: string; created_at: string }[];
  deviation: { id: string; number: string; status: string } | null;
  needs_acceptance: boolean; next_task?: { id: string; number: string; due_date: string } | null;
  impact_created?: { id: string; number: string } | null;
}
interface CertCheck { key: string; status: "ok" | "warn" | "fail"; values: Record<string, unknown> }
interface CertOut {
  suggestion: { id: string; model: string }; fields: Record<string, string | number | null>;
  extracted: Record<string, any>; mapped: { seq: number; as_found: number | null; as_left: number | null; uncertainty: number | null; missing?: boolean }[];
  extra_readings: unknown[]; checks: CertCheck[];
}

export type Row = { [K in "parameter" | "nominal" | "unit" | "low_limit" | "high_limit" | "as_found" | "as_left" | "uncertainty" | "notes" | "value_text"]: string }
  & { item_id: string | null; item_type: string; seq: number; fixed: boolean; file_id: string | null; required: boolean;
      page_ref: string | null; item_notes: string | null };

const s = (v: unknown) => (v === null || v === undefined ? "" : String(v));
const n = (v: string) => (v.trim() === "" ? null : Number(v.replace(",", ".")));
const okNum = (v: string) => v.trim() === "" || Number.isFinite(Number(v.replace(",", ".")));

export function inLimits(v: string, lo: string, hi: string): boolean | null {
  const x = n(v);
  if (x === null || !Number.isFinite(x)) return null;
  const l = n(lo), h = n(hi);
  return (l === null || x >= l - 1e-12) && (h === null || x <= h + 1e-12);
}

function verdictOf(rows: Row[]): Verdict | null {
  const pts = rows.filter((r) => r.item_type === "point");
  if (!pts.length) return null;
  const found = pts.map((r) => inLimits(r.as_found, r.low_limit, r.high_limit));
  if (found.some((x) => x === null)) return null;
  if (found.every(Boolean)) return "pass";
  const left = pts.map((r, i) => (r.as_left.trim() !== "" ? inLimits(r.as_left, r.low_limit, r.high_limit) : found[i]));
  return left.every(Boolean) ? "adjusted" : "fail";
}

function toRows(results: Result[]): Row[] {
  return results.map((r) => ({
    item_id: r.item_id, item_type: r.item_type, seq: r.seq, fixed: !!r.item_id, parameter: r.parameter, nominal: s(r.nominal), unit: s(r.unit),
    low_limit: s(r.low_limit), high_limit: s(r.high_limit), as_found: s(r.as_found), as_left: s(r.as_left), uncertainty: s(r.uncertainty), notes: s(r.notes),
    value_text: s(r.value_text), file_id: r.file_id ?? null, required: r.required ?? true, page_ref: r.page_ref ?? null, item_notes: r.item_notes ?? null,
  }));
}

export function pmVerdictOf(rows: Row[]): Verdict | null {
  let findings = false;
  for (const r of rows) {
    if (r.item_type === "check") {
      if (!["ok", "not_ok", "na"].includes(r.value_text)) { if (r.required) return null; continue; }
      findings ||= r.value_text === "not_ok";
    } else if (r.item_type === "reading") {
      if (r.as_found.trim() === "") { if (r.required) return null; continue; }
      findings ||= inLimits(r.as_found, r.low_limit, r.high_limit) === false;
    } else if (r.item_type === "text" && r.required && !r.value_text.trim()) return null;
    else if (r.item_type === "photo" && r.required && !r.file_id) return null;
  }
  return rows.length ? (findings ? "fail" : "pass") : null;
}

export function InOut({ ok }: { ok: boolean | null }) {
  const { t } = useI18n();
  if (ok === null) return <span className="subtle">–</span>;
  return ok ? <span className="cm-in"><LuCircleCheck aria-hidden />{t("task.r.in")}</span> : <span className="cm-out"><LuCircleX aria-hidden />{t("task.r.out")}</span>;
}

function SigLine({ sig, label }: { sig: Sig; label: string }) {
  const { dateTime } = useI18n();
  return (
    <div className="sign-record small">
      <strong><LuSignature aria-hidden /> {label}</strong> &lt;{sig.user_email}&gt; · {dateTime(sig.signed_at)}
      <div className="subtle">“{sig.statement}”</div>
      <div className="subtle mono-id">SHA-256 {sig.record_hash}</div>
    </div>
  );
}

export function TaskPage({ taskId }: { taskId: string }) {
  const { t, tx, lang, qty } = useI18n();
  const { me } = useSession();
  const toast = useToast();
  const day = useDay();
  const ruleText = useRuleText();
  const every = useEvery();
  const { data: task, error, reload, setData } = useLoad(() => get<Task>(`/api/tasks/${taskId}`), [taskId]);
  const [rows, setRows] = useState<Row[]>([]);
  const [stds, setStds] = useState<Standard[]>([]);
  const [rec, setRec] = useState({ performed_date: "", performed_by_name: "", certificate_no: "", env_temperature: "", env_humidity: "", remarks: "", man_hours: "" });
  const [parts, setParts] = useState<Part[]>([]);
  const [cert, setCert] = useState<FileMeta | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<ReactNode>(null);
  const [ai, setAi] = useState<CertOut | null>(null);
  const [aiApplied, setAiApplied] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [dialog, setDialog] = useState<null | "complete" | "send" | "review" | "cancel" | "reschedule">(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = (x: Task) => {
    setRows(toRows(x.results));
    setStds(x.standards.map((y) => ({ standard_asset_id: y.standard_asset_id, description: y.description, cert_ref: y.cert_ref, valid_until: y.valid_until })));
    setRec({ performed_date: s(x.performed_date), performed_by_name: s(x.performed_by_name) || (x.performer === "internal" ? me?.user.full_name ?? "" : ""),
      certificate_no: s(x.certificate_no), env_temperature: s(x.env_temperature), env_humidity: s(x.env_humidity), remarks: s(x.remarks),
      man_hours: s(x.man_hours) });
    setParts((x.parts ?? []).map((p) => ({ name: p.name, part_no: s(p.part_no), qty: s(p.qty), unit: s(p.unit), notes: s(p.notes) })));
    setCert(x.certificate);
    setDirty(false);
  };
  useEffect(() => { if (task) load(task); }, [task]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!cert) { setPreview(null); return; }
    let url: string | null = null;
    let live = true;
    fetch(`/api/files/${cert.id}/content?inline=1`, { credentials: "same-origin" }).then((r) => r.blob()).then((b) => {
      if (!live) return;
      url = URL.createObjectURL(b);
      setPreview(url);
    }).catch(() => {});
    return () => { live = false; if (url) URL.revokeObjectURL(url); };
  }, [cert?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const isPm = task?.kind === "pm";
  const verdict = useMemo(() => (isPm ? pmVerdictOf(rows) : verdictOf(rows)), [rows, isPm]);
  if (error) return <div className="panel"><LoadError error={error} onRetry={reload} /></div>;
  if (!task) return <div className="panel"><div className="panel-body"><div className="skeleton" style={{ width: "40%" }} /></div></div>;
  const x = task;
  const edit = x.perms.edit;
  const closed = x.status === "completed" || x.status === "cancelled";
  const today = todayIso();

  const body = () => ({
    performed_date: rec.performed_date || null, performed_by_name: rec.performed_by_name || null, certificate_no: rec.certificate_no || null,
    certificate_file_id: cert?.id ?? null, env_temperature: rec.env_temperature || null, env_humidity: rec.env_humidity || null,
    remarks: rec.remarks || null, man_hours: n(rec.man_hours),
    results: rows.map((r) => ({ item_id: r.item_id, item_type: r.item_type, parameter: r.parameter || "?", nominal: n(r.nominal), unit: r.unit || null,
      low_limit: n(r.low_limit), high_limit: n(r.high_limit), as_found: n(r.as_found), as_left: n(r.as_left), uncertainty: n(r.uncertainty), notes: r.notes || null,
      value_text: r.value_text || null, file_id: r.file_id, required: r.required })),
    standards: stds.filter((y) => y.standard_asset_id || y.description),
    parts: parts.filter((p) => p.name.trim()).map((p) => ({ name: p.name.trim(), part_no: p.part_no || null, qty: n(p.qty) ?? 1, unit: p.unit || null, notes: p.notes || null })),
  });
  const valid = rows.every((r) => okNum(r.as_found) && okNum(r.as_left) && okNum(r.uncertainty) && okNum(r.nominal) && okNum(r.low_limit) && okNum(r.high_limit))
    && okNum(rec.man_hours) && parts.every((p) => okNum(p.qty));
  const save = async (quiet = false) => {
    setSaving(true); setErr(null);
    try { const out = await put<Task>(`/api/tasks/${x.id}/results`, body()); setData(out); if (!quiet) toast(t("task.saved")); return out; }
    catch (e) { setErr(errorText(e, t)); throw e; }
    finally { setSaving(false); }
  };
  const act = async (key: string, fn: () => Promise<Task>) => {
    setBusy(key); setErr(null);
    try { setData(await fn()); } catch (e) { setErr(errorText(e, t)); } finally { setBusy(null); }
  };
  const setRow = (i: number, k: keyof Row, v: string) => { setRows((r) => r.map((y, j) => (j === i ? { ...y, [k]: v } : y))); setDirty(true); };
  const setR = (k: keyof typeof rec, v: string) => { setRec((r) => ({ ...r, [k]: v })); setDirty(true); };

  const uploadCert = async (f: File) => {
    setBusy("upload"); setErr(null);
    try {
      const m = await upload<FileMeta>(f, f.name, "certificate");
      setCert(m); setDirty(true); setAi(null);
    } catch (e) { setErr(errorText(e, t)); } finally { setBusy(null); }
  };
  const readCert = async () => {
    if (!cert) { fileRef.current?.click(); return; }
    setBusy("ai"); setErr(null);
    try { setAi(await post<CertOut>(`/api/tasks/${x.id}/certificate`, { file_id: cert.id })); setAiApplied(null); }
    catch (e) { setErr(errorText(e, t)); } finally { setBusy(null); }
  };
  const applyAi = () => {
    if (!ai) return;
    const f = ai.fields;
    setRec((r) => ({
      ...r, certificate_no: s(f.certificate_no) || r.certificate_no, performed_date: s(f.performed_date) || r.performed_date,
      env_temperature: s(f.env_temperature) || r.env_temperature, env_humidity: s(f.env_humidity) || r.env_humidity,
      performed_by_name: r.performed_by_name || s(ai.extracted?.technician),
    }));
    const extra = (ai.extra_readings as { parameter?: string; nominal?: number | null; unit?: string | null; as_found?: number | null;
      as_left?: number | null; error?: number | null; uncertainty?: number | null }[]);
    if (rows.length === 0 && extra.length) {
      setRows(extra.map((r, i) => ({ item_id: null, item_type: "point", seq: i + 1, fixed: false, parameter: r.parameter || x.asset.class_name_en,
        value_text: "", file_id: null, required: true, page_ref: null, item_notes: null,
        nominal: s(r.nominal), unit: s(r.unit), low_limit: "", high_limit: "",
        as_found: s(r.as_found ?? (r.nominal != null && r.error != null ? r.nominal + r.error : null)), as_left: s(r.as_left),
        uncertainty: s(r.uncertainty), notes: "" })));
    } else setRows((rs) => rs.map((r) => {
      const m = ai.mapped.find((y) => y.seq === r.seq);
      if (!m || m.missing) return r;
      return { ...r, as_found: s(m.as_found), as_left: s(m.as_left), uncertainty: s(m.uncertainty) || r.uncertainty };
    }));
    setAiApplied(ai.suggestion.id); setDirty(true);
    toast(t("cert.applied"));
  };
  const discardAi = async () => {
    if (ai) post(`/api/ai/suggestions/${ai.suggestion.id}/reject`, {}).catch(() => {});
    setAi(null); setAiApplied(null);
  };

  const complete = async (v: { email: string; password: string; reason: string }) => {
    if (dirty) await save(true);
    const out = await post<Task>(`/api/tasks/${x.id}/complete`, { ...v, reason: v.reason || null, ai_suggestion_id: aiApplied });
    setData(out); setAi(null);
    toast(isPm ? t("pmt.signedDone", { name: me?.user.full_name ?? "" }) : t("task.signed", { name: me?.user.full_name ?? "" }));
    if (out.impact_created) toast(t("task.impactNew", { number: out.impact_created.number }));
  };

  const name = nameOf(x.asset, lang);
  const vk = (v: string) => (isPm ? `verdict.pm.${v}` : `verdict.${v}`) as StringKey;
  const L = isPm ? { complete: t("pmt.complete"), review: t("pmt.review"), back: t("pmt.back") } : { complete: t("task.complete"), review: t("task.review"), back: t("task.back") };
  const base = isPm ? "/maintenance" : "/calibration";
  const lo = (r: Row) => r.low_limit, hi = (r: Row) => r.high_limit;
  const usedStd = new Set(stds.map((y) => y.standard_asset_id).filter(Boolean));
  const checkMsg = (c: CertCheck) => {
    const v = c.values as Record<string, any>;
    switch (c.key) {
      case "serial": return t("cert.m.serial", { certificate: s(v.certificate) || "?", register: s(v.register) || "?" });
      case "coverage": return v.no_points ? t("cert.m.noPoints", { n: v.extra }) : t("cert.m.coverage", { found: v.found, expected: v.expected });
      case "tolerance": return (v.out as number[]).length ? t("cert.m.tolerance", { out: (v.out as number[]).join(", ") }) : "";
      case "uncertainty": return (v.low as unknown[]).length ? t("cert.m.uncertainty", { minimum: v.minimum, low: (v.low as { seq: number; ratio: number }[]).map((y) => `${y.seq} (${y.ratio}:1)`).join(", ") }) : "";
      case "date": return v.date ? day(v.date) : "";
      case "accreditation": return s(v.register ?? v.certificate ?? v.expired);
      case "standards": return t("cert.m.standards", { count: v.count });
      case "conformity": return s(v.statement);
      default: return "";
    }
  };

  return (
    <>
      <Link to={base} className="back-link"><LuArrowLeft className="flip-rtl" aria-hidden /> {L.back}</Link>
      <div className="page-head cm-task-head">
        <div className="titles">
          <div className="row wrap" style={{ gap: 8 }}>
            <span className="mono-id cm-code">{x.number}</span>
            <TaskStateBadge task={x} />
            {x.status === "completed" && <VerdictBadge verdict={x.verdict} kind={x.kind} />}
            {x.source === "demo" && <Badge>{t("task.demo")}</Badge>}
          </div>
          <h1><Link to={`/assets/${x.asset_id}`} className="mono-id">{x.asset.code}</Link> · {name}</h1>
          <p className="muted small">
            {lang === "ar" && x.asset.class_name_ar ? x.asset.class_name_ar : x.asset.class_name_en}
            {x.asset.serial_no && <> · S/N <span className="mono-id">{x.asset.serial_no}</span></>}
            {" · "}<StatusBadge status={x.asset.status} />
          </p>
          <p className="small">
            <strong>{t("task.due", { date: day(x.due_date) })}</strong>{" · "}
            {t("task.window", { range: x.window_start ? t("cw.windowRange", { start: day(x.window_start), end: day(x.window_end) }) : t("cw.windowAny", { end: day(x.window_end) }) })}
            {" "}<DueWhen task={x} today={today} />
          </p>
          <p className="small muted">
            {t("task.performer")}: {t(`perf.${x.performer}` as StringKey)}{x.vendor_name ? ` · ${x.vendor_name}` : ""}
            {x.responsible_name && <> · {t("task.responsible")}: {x.responsible_name}</>}
            {x.schedule && <> · {every(x.schedule.interval_value, x.schedule.interval_unit)} · {t(`basis.${x.schedule.basis}` as StringKey)}{x.schedule.rule ? ` · ${ruleText(x.schedule.rule as never)}` : ""}</>}
          </p>
        </div>
        <div className="actions row wrap">
          {x.perms.accept && <Button variant="primary" icon={<LuHandshake />} busy={busy === "accept"}
            onClick={() => act("accept", () => post<Task>(`/api/tasks/${x.id}/accept`))}>{t("task.accept")}</Button>}
          {x.perms.send && x.status !== "at_vendor" && <Button icon={<LuTruck />} onClick={() => setDialog("send")}>{t("task.send")}</Button>}
          {edit && x.perms.ai && me?.tenant?.ai.certificate && (
            <Button icon={<LuSparkles />} busy={busy === "ai"} onClick={readCert}>{busy === "ai" ? t("task.readingCert") : t("task.readCert")}</Button>
          )}
          {edit && <Button icon={<LuSave />} busy={saving} disabled={!dirty || !valid} onClick={() => save().catch(() => {})}>{t("task.save")}</Button>}
          {x.perms.complete && <Button variant="primary" icon={<LuSignature />} disabled={!valid} onClick={() => setDialog("complete")}>{L.complete}</Button>}
          {x.perms.review && <Button variant="primary" icon={<LuSignature />} onClick={() => setDialog("review")}>{L.review}</Button>}
          {x.perms.reschedule && <Button variant="ghost" icon={<LuCalendarClock />} onClick={() => setDialog("reschedule")}>{t("rs.title")}</Button>}
          {x.perms.cancel && x.status !== "at_vendor" && <Button variant="ghost" icon={<LuX />} onClick={() => setDialog("cancel")}>{t("task.cancel")}</Button>}
          {!edit && <PrintFormButton source={isPm ? "pm_record" : "calibration_record"} params={{ task_id: x.id }} />}
        </div>
      </div>

      {err && <Notice tone="danger">{err}</Notice>}
      {!x.released && !closed && <Notice tone="warn">{t(isPm ? "pmt.notReleased" : "pmt.notReleasedCal", { year: x.due_date.slice(0, 4) })}{" "}
        <Link to={`/plans/${x.kind}?year=${x.due_date.slice(0, 4)}&site_id=${x.site_id}`}>{t("pmt.openPlan")}</Link></Notice>}
      {x.released && x.needs_acceptance && <Notice>{t("task.acceptHint")}</Notice>}
      {x.overdue && !isPm && <Notice tone="danger">{t("task.overdueNotice", { date: day(x.window_end) })}</Notice>}
      {x.accepted_by_name && !closed && <p className="small muted">{t("task.accepted", { name: x.accepted_by_name })}</p>}
      {x.status === "cancelled" && <Notice>{t("task.cancelled", { reason: x.cancel_reason ?? "" })}</Notice>}
      {x.impact && <Notice tone={x.impact.status === "approved" ? "ok" : "warn"}>
        <Link to={`/calibration/impacts/${x.impact.id}`}>{t("task.impact", { number: x.impact.number })}</Link> · {t(`ia.status.${x.impact.status}` as StringKey)}
      </Notice>}
      {x.deviation && <Notice tone={x.deviation.status === "open" ? "danger" : "ok"}><Link to={`/deviations/${x.deviation.id}`}>{x.deviation.number}</Link> · {t(`dev.status.${x.deviation.status}` as StringKey)}</Notice>}
      {!closed && x.escalations.length > 0 && <div className="chip-row cm-esc">{x.escalations.map((e, i) => (
        <Badge key={i} tone={e.level >= 2 ? "danger" : "warn"}>{t("nt.level", { n: e.level })} · {t(`esc.${e.trigger}` as StringKey)} · {day(e.created_at.slice(0, 10))}</Badge>))}</div>}
      {x.next_task && <Notice tone="ok">{t(isPm ? "pmt.nextCreated" : "task.nextCreated", { date: day(x.next_task.due_date), number: x.next_task.number })}</Notice>}

      <div className="cm-task-grid">
        <div className="stack">
          <section className="panel">
            <div className="panel-head"><h3>{t("task.record")}</h3></div>
            <div className="panel-body">
              <div className="grid-3">
                <TextField label={t("task.performed_date")} type="date" max={today} value={rec.performed_date} onChange={(v) => setR("performed_date", v)} disabled={!edit} />
                <TextField label={t("task.performed_by")} value={rec.performed_by_name} onChange={(v) => setR("performed_by_name", v)} disabled={!edit} maxLength={160} />
                {isPm && <TextField label={t("pmt.manHours")} inputMode="decimal" dir="ltr" value={rec.man_hours} onChange={(v) => setR("man_hours", v)} disabled={!edit}
                  error={okNum(rec.man_hours) ? undefined : " "} hint={x.template?.est_minutes ? t("pmt.est", { n: x.template.est_minutes }) : undefined} />}
                {!isPm && <><TextField label={t("task.certificate_no")} value={rec.certificate_no} onChange={(v) => setR("certificate_no", v)} disabled={!edit} maxLength={120} />
                <TextField label={t("task.env_temperature")} value={rec.env_temperature} onChange={(v) => setR("env_temperature", v)} disabled={!edit} maxLength={40} placeholder={t("task.envTempPh")} />
                <TextField label={t("task.env_humidity")} value={rec.env_humidity} onChange={(v) => setR("env_humidity", v)} disabled={!edit} maxLength={40} placeholder={t("task.envHumPh")} />
                <div className="field">
                  <span className="field-label">{t("task.certificate")}{x.certificate_required ? " *" : ""}</span>
                  <div className="row wrap" style={{ gap: 6 }}>
                    {cert && <a className="file-chip" href={`/api/files/${cert.id}/content`}><LuFileText aria-hidden />{cert.filename}</a>}
                    {edit && <Button size="sm" icon={<LuUpload />} busy={busy === "upload"} onClick={() => fileRef.current?.click()}>{t("task.uploadCert")}</Button>}
                    {!cert && !edit && <span className="subtle">—</span>}
                  </div>
                  <input ref={fileRef} type="file" hidden accept="application/pdf,image/*" capture="environment"
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadCert(f); e.target.value = ""; }} />
                </div></>}
              </div>
              <div className="field" style={{ marginTop: 14 }}>
                <label htmlFor="task-remarks">{t("task.remarks")}</label>
                <textarea id="task-remarks" className="textarea" value={rec.remarks} disabled={!edit} maxLength={4000} onChange={(e) => setR("remarks", e.target.value)} />
              </div>
            </div>
          </section>

          {isPm ? <>
            <PmChecklist rows={rows} edit={edit} template={x.template} onChange={(r) => { setRows(r); setDirty(true); }} />
            <PmParts parts={parts} edit={edit} suggested={x.template?.spare_parts ?? []} onChange={(p) => { setParts(p); setDirty(true); }} />
          </> : <>
          <section className="panel">
            <div className="panel-head"><h3>{t("task.readings")}</h3>
              {x.template && <span className="small muted">{x.template.code} v{x.template.version}{x.template.method_ref ? ` · ${x.template.method_ref}` : ""}</span>}
              <span className="spacer" />
              {edit && <Button size="sm" icon={<LuPlus />} onClick={() => { setRows((r) => [...r, { item_id: null, item_type: "point", seq: (r.at(-1)?.seq ?? 0) + 1, fixed: false,
                parameter: "", nominal: "", unit: r.at(-1)?.unit ?? x.asset.unit ?? "", low_limit: "", high_limit: "", as_found: "", as_left: "", uncertainty: "", notes: "",
                value_text: "", file_id: null, required: true, page_ref: null, item_notes: null }]); setDirty(true); }}>{t("task.r.add")}</Button>}
            </div>
            {rows.length === 0 ? (
              <div className="panel-body">{edit ? <Notice>{t("task.r.noTemplate")}</Notice> : <span className="muted">—</span>}</div>
            ) : (
              <div className="table-wrap">
                <table className="table edit-table cm-readings">
                  <thead><tr>
                    <th>#</th><th>{t("task.r.point")}</th><th className="num">{t("task.r.nominal")}</th><th className="num">{t("task.r.limits")}</th>
                    <th className="num">{t("task.r.found")}</th><th>{t("task.r.result")}</th><th className="num">{t("task.r.left")}</th><th>{t("task.r.result")}</th>
                    <th className="num">{t("task.r.u")}</th>{edit && <th />}
                  </tr></thead>
                  <tbody>
                    {rows.map((r, i) => {
                      const okF = inLimits(r.as_found, lo(r), hi(r));
                      const okL = r.as_left.trim() ? inLimits(r.as_left, lo(r), hi(r)) : null;
                      const m = ai?.mapped.find((y) => y.seq === r.seq);
                      const cell = (k: "as_found" | "as_left" | "uncertainty", label: string) => edit ? (
                        <input className={`input cell-input ${okNum(r[k]) ? "" : "bad"}`} inputMode="decimal" dir="ltr" aria-label={`${label} ${r.parameter} ${r.nominal}`}
                          value={r[k]} onChange={(e) => setRow(i, k, e.target.value)} />
                      ) : <span className="num">{r[k] ? qty(Number(r[k]), 6) : "–"}</span>;
                      return (
                        <tr key={i} className={okF === false ? (okL ? "cm-row-adj" : "cm-row-bad") : ""}>
                          <td className="subtle num">{i + 1}</td>
                          <td>{r.fixed || !edit ? <><div className="cell-main">{r.parameter}</div>{r.notes && <div className="small subtle">{r.notes}</div>}</> : (
                            <input className="input cell-input wide" aria-label={t("tpl.parameter")} value={r.parameter} onChange={(e) => setRow(i, "parameter", e.target.value)} />
                          )}</td>
                          <td className="num nowrap">{r.fixed || !edit ? <>{qty(Number(r.nominal), 6)} {r.unit}</> : (
                            <span className="row" style={{ gap: 4 }}>
                              <input className="input cell-input" inputMode="decimal" dir="ltr" aria-label={t("task.r.nominal")} value={r.nominal} onChange={(e) => setRow(i, "nominal", e.target.value)} />
                              <input className="input cell-input narrow" aria-label="unit" value={r.unit} onChange={(e) => setRow(i, "unit", e.target.value)} />
                            </span>
                          )}</td>
                          <td className="num nowrap small">{r.fixed || !edit ? <>{r.low_limit !== "" ? qty(Number(r.low_limit), 6) : "–∞"} … {r.high_limit !== "" ? qty(Number(r.high_limit), 6) : "∞"}</> : (
                            <span className="row" style={{ gap: 4 }}>
                              <input className="input cell-input" inputMode="decimal" dir="ltr" aria-label={t("task.r.low")} placeholder={t("task.r.low")} value={r.low_limit} onChange={(e) => setRow(i, "low_limit", e.target.value)} />
                              <input className="input cell-input" inputMode="decimal" dir="ltr" aria-label={t("task.r.high")} placeholder={t("task.r.high")} value={r.high_limit} onChange={(e) => setRow(i, "high_limit", e.target.value)} />
                            </span>
                          )}</td>
                          <td className="num">{cell("as_found", t("task.r.found"))}{m && !m.missing && ai && aiApplied !== ai.suggestion.id && <div className="small cm-ai-val"><LuSparkles aria-hidden />{s(m.as_found)}</div>}</td>
                          <td><InOut ok={okF} /></td>
                          <td className="num">{cell("as_left", t("task.r.left"))}{m && !m.missing && m.as_left !== null && ai && aiApplied !== ai.suggestion.id && <div className="small cm-ai-val"><LuSparkles aria-hidden />{s(m.as_left)}</div>}</td>
                          <td><InOut ok={okL} /></td>
                          <td className="num">{cell("uncertainty", t("task.r.u"))}</td>
                          {edit && <td>{!r.fixed && <Button size="sm" variant="ghost" iconOnly aria-label={t("common.delete")} icon={<LuTrash2 />}
                            onClick={() => { setRows((rr) => rr.filter((_, j) => j !== i)); setDirty(true); }} />}</td>}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-head"><h3>{t("task.standards")}</h3>
              {x.template?.standards_note && <span className="small muted">{x.template.standards_note}</span>}
              <span className="spacer" />
              {edit && <Button size="sm" icon={<LuPlus />} onClick={() => { setStds((y) => [...y, { standard_asset_id: null, description: "", cert_ref: "", valid_until: null }]); setDirty(true); }}>{t("task.std.add")}</Button>}
            </div>
            {stds.length === 0 ? <div className="panel-body subtle">—</div> : (
              <ul className="cm-stds">
                {stds.map((y, i) => {
                  const opt = x.standard_options.find((o) => o.id === y.standard_asset_id);
                  return (
                    <li key={i}>
                      {edit ? (
                        <div className="cm-std-edit">
                          <select className="select" aria-label={t("task.std.pick")} value={y.standard_asset_id ?? ""}
                            onChange={(e) => { const v = e.target.value; setStds((ss) => ss.map((z, j) => (j === i ? { ...z, standard_asset_id: v || null, description: v ? null : z.description, valid_until: null } : z))); setDirty(true); }}>
                            <option value="">{t("task.std.lab")}</option>
                            {x.standard_options.map((o) => (
                              <option key={o.id} value={o.id} disabled={!o.usable || (usedStd.has(o.id) && o.id !== y.standard_asset_id)}>
                                {o.code} · {nameOf(o, lang)}{o.valid_until ? ` · ${t("task.std.validUntil", { date: day(o.valid_until) })}` : ""}{o.usable ? "" : ` · ${t("task.std.notUsable")}`}
                              </option>
                            ))}
                          </select>
                          {!y.standard_asset_id && <input className="input" aria-label={t("tpl.description")} placeholder={t("task.std.lab")} value={y.description ?? ""}
                            onChange={(e) => { const v = e.target.value; setStds((ss) => ss.map((z, j) => (j === i ? { ...z, description: v } : z))); setDirty(true); }} />}
                          <input className="input narrow" aria-label={t("task.std.cert")} placeholder={t("task.std.cert")} value={y.cert_ref ?? ""}
                            onChange={(e) => { const v = e.target.value; setStds((ss) => ss.map((z, j) => (j === i ? { ...z, cert_ref: v } : z))); setDirty(true); }} />
                          <Button size="sm" variant="ghost" iconOnly aria-label={t("common.delete")} icon={<LuTrash2 />} onClick={() => { setStds((ss) => ss.filter((_, j) => j !== i)); setDirty(true); }} />
                        </div>
                      ) : (
                        <div>
                          <div className="cell-main">{y.description ?? opt?.code}</div>
                          <div className="small subtle">{[y.cert_ref, y.valid_until ? t("task.std.validUntil", { date: day(y.valid_until) }) : null].filter(Boolean).join(" · ")}</div>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
          </>}
        </div>

        <aside className="stack">
          <section className={`panel cm-verdict ${isPm && (x.status === "completed" ? x.verdict : verdict) === "fail" ? "adjusted" : (x.status === "completed" ? x.verdict : verdict) ?? "none"}`}>
            <div className="panel-body">
              <span className="small muted">{t("task.verdict")}</span>
              {(x.status === "completed" ? x.verdict : verdict)
                ? <strong className="v">{t(vk((x.status === "completed" ? x.verdict : verdict) as string))}</strong>
                : <span className="subtle">{t("task.verdictPending")}</span>}
              {x.signature && <SigLine sig={x.signature} label={isPm ? t("pmt.signedDone", { name: x.signature.user_name }) : t("task.signed", { name: x.signature.user_name })} />}
              {x.review_signature && <SigLine sig={x.review_signature} label={isPm ? t("pmt.signedChecked", { name: x.review_signature.user_name }) : t("task.reviewed", { name: x.review_signature.user_name })} />}
              {x.ai && <p className="small subtle"><LuSparkles aria-hidden /> {x.ai.model}{x.ai.changed_fields && Object.keys(x.ai.changed_fields).length
                ? ` · ${t("asset.ai.changed", { fields: Object.keys(x.ai.changed_fields).join(", ") })}` : ` · ${t("asset.ai.unchanged")}`}</p>}
            </div>
          </section>

          {ai && (
            <section className="panel cm-cert-ai">
              <div className="panel-head"><h3><LuSparkles aria-hidden /> {t("cert.title")}</h3><span className="spacer" /><span className="small subtle">{ai.suggestion.model}</span></div>
              <div className="panel-body stack">
                <p className="small muted">{t("cert.lead")}</p>
                <ul className="cm-checks">
                  {ai.checks.map((c) => (
                    <li key={c.key} className={c.status}>
                      {c.status === "ok" ? <LuCircleCheck aria-hidden /> : c.status === "warn" ? <LuCircleX aria-hidden className="warn" /> : <LuCircleX aria-hidden />}
                      <div><strong>{tx(`cert.c.${c.key}`, c.key)}</strong><div className="small subtle">{checkMsg(c)}</div></div>
                    </li>
                  ))}
                </ul>
                <dl className="cm-facts compact">
                  {([["cert.field.lab", ai.extracted.lab_name], ["cert.field.acc", [ai.extracted.accreditation_body, ai.extracted.accreditation_no].filter(Boolean).join(" ")],
                    ["task.certificate_no", ai.extracted.certificate_no], ["task.performed_date", ai.extracted.calibration_date ? day(ai.extracted.calibration_date) : ""],
                    ["cert.field.instrument", [ai.extracted.instrument?.description, ai.extracted.instrument?.serial_no].filter(Boolean).join(" · ")],
                    ["cert.field.technician", ai.extracted.technician], ["cert.field.conformity", ai.extracted.conformity], ["cert.field.notes", ai.extracted.notes]] as [StringKey, unknown][])
                    .filter(([, v]) => v).map(([k, v]) => <div className="cm-fact" key={k}><dt>{t(k)}</dt><dd>{String(v)}</dd></div>)}
                </dl>
                {ai.extra_readings.length > 0 && ai.mapped.length > 0 && <Notice tone="warn">{t("cert.extra", { n: ai.extra_readings.length })}</Notice>}
                <div className="row wrap" style={{ gap: 8 }}>
                  <Button variant="primary" icon={<LuCheck />} disabled={aiApplied === ai.suggestion.id} onClick={applyAi}>{t("cert.apply")}</Button>
                  <Button variant="ghost" onClick={discardAi}>{t("cert.discard")}</Button>
                </div>
              </div>
            </section>
          )}

          {preview && cert && (
            <section className="panel cm-cert-view">
              <div className="panel-head"><h3>{t("task.certificate")}</h3><span className="spacer" />
                <a className="small" href={preview} target="_blank" rel="noopener">{t("task.viewCert")}</a></div>
              {cert.mime.startsWith("image/") ? <img src={preview} alt={cert.filename} /> : <iframe src={preview} title={cert.filename} />}
            </section>
          )}

          <RescheduleHistory items={x.reschedules} me={me?.user.id ?? ""} onChanged={reload} />
          <section className="panel">
            <div className="panel-head"><h3>{isPm ? t("pmt.previous") : t("task.previous")}</h3></div>
            {x.previous.length === 0 ? <div className="panel-body small subtle">{t("task.noPrevious")}</div> : (
              <ul className="cm-prev">
                {x.previous.map((p) => (
                  <li key={p.id}>
                    <Link to={`${base}/tasks/${p.id}`} className="mono-id">{p.number}</Link>
                    <span className="num small">{day(p.performed_date)}</span>
                    <VerdictBadge verdict={p.verdict} kind={x.kind} />
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>

      {dialog === "complete" && (
        <SignDialog title={`${L.complete} · ${x.number}`} meaning={isPm ? "pm_completed" : "calibration_completed"} onClose={() => setDialog(null)} onSign={complete}>
          <div className={`cm-verdict-line ${verdict ?? ""}`}>{t("task.verdict")}: <strong>{verdict ? t(vk(verdict)) : t("task.verdictPending")}</strong></div>
          {verdict === "fail" && <Notice tone={isPm ? "warn" : "danger"}>{isPm ? t("pmt.findingsNotice") : t("task.failNotice")}</Notice>}
          {verdict === "adjusted" && <Notice tone="warn">{t("task.adjNotice")}</Notice>}
          {aiApplied && <p className="small muted"><LuSparkles aria-hidden /> {t("task.aiRecorded")}</p>}
        </SignDialog>
      )}
      {dialog === "reschedule" && <RescheduleSheet task={x} onClose={() => setDialog(null)} onDone={reload} />}
      {dialog === "send" && (
        <SignDialog title={`${t("task.send")} · ${x.number}`} lead={x.vendor_name ?? undefined} meaning="sent_to_vendor" onClose={() => setDialog(null)}
          onSign={async (v) => { setData(await post<Task>(`/api/tasks/${x.id}/send`, { ...v, reason: v.reason || null })); toast(t("task.sentToast")); }} />
      )}
      {dialog === "review" && (
        <SignDialog title={`${L.review} · ${x.number}`} meaning={isPm ? "pm_checked" : "calibration_reviewed"} onClose={() => setDialog(null)}
          onSign={async (v) => { setData(await post<Task>(`/api/tasks/${x.id}/review`, { ...v, reason: v.reason || null })); }} />
      )}
      {dialog === "cancel" && (
        <SignDialog title={`${t("task.cancel")} · ${x.number}`} meaning="task_cancelled" reasonRequired danger submitLabel={t("task.cancel")}
          onClose={() => setDialog(null)} onSign={async (v) => {
            const out = await post<Task>(`/api/tasks/${x.id}/cancel`, v);
            if (out.next_task) { toast(t("task.nextCreated", { date: day(out.next_task.due_date), number: out.next_task.number })); navigate(`${base}/tasks/${out.next_task.id}`); }
            else setData(out);
          }} />
      )}
    </>
  );
}
