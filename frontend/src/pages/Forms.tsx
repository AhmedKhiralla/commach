// Phase 5: forms and records. The list of controlled formats, the block designer with a live PDF preview,
// Word templates with merge fields, approval with an electronic signature, versions and the issued-copy log.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  LuArrowDown, LuArrowLeft, LuArrowUp, LuBan, LuCopyPlus, LuDownload, LuEye, LuFileText, LuFileType, LuImage, LuPlus,
  LuPrinter, LuRefreshCw, LuSignature, LuStar, LuTrash2, LuUpload,
} from "react-icons/lu";
import { fetchForm, type FormSource } from "../components/forms";
import {
  Badge, Button, Empty, LoadError, LoadingRows, Notice, PageHead, SelectField, Sheet, Switch, TextField, errorText, fieldErrors, useLoad, useToast,
} from "../components/ui";
import { SignDialog } from "../components/work";
import { get, patch, post, upload } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link, navigate } from "../lib/router";
import type { StringKey } from "../lib/strings";

const SOURCES: FormSource[] = ["calibration_plan", "pm_plan", "calibration_record", "pm_record", "history_card", "status_label"];
const LISTS_FOR: Record<FormSource, string[]> = {
  calibration_plan: ["se_rows", "rows"], pm_plan: ["se_rows", "rows"], calibration_record: ["results", "standards"],
  pm_record: ["results", "parts"], history_card: ["history"], status_label: [],
};
const TONE: Record<string, "ok" | "warn" | undefined> = { approved: "ok", draft: "warn" };

interface Cell { label?: string; value?: string; date?: string; header?: string; width?: number }
interface Block { type: "fields" | "table" | "text" | "signatures" | "spacer"; columns?: number | Cell[]; items?: Cell[]; list?: string;
  title?: string; font?: number; text?: string; style?: string; mm?: number }
interface Layout {
  page: { size?: string; orientation?: string; margin_mm?: number };
  header: { logo?: boolean; logo_file_id?: string | null; show_company?: boolean; title?: string; subtitle?: string; annexure?: string };
  blocks: Block[];
  footer: { text?: string; page_numbers?: boolean };
}
interface FormRow {
  id: string; code: string; version: number; title: string; source: FormSource; engine: "blocks" | "docx"; format_no: string | null;
  revision: string | null; is_default: boolean; status: string; approved_at: string | null; approved_by_name: string | null;
  source_kind: string; filename: string | null; issued: number; updated_at: string;
}
interface Form extends FormRow {
  layout: Layout; file_id: string | null; versions: { id: string; version: number; status: string }[];
  signature: { user_name: string; signed_at: string; statement: string; record_hash: string } | null;
  can_edit: boolean; can_approve: boolean; can_manage: boolean; placeholders?: string[]; unknown?: string[];
}
interface Meta { sources: FormSource[]; fields: Record<string, Record<string, string[]>>; lists: string[]; defaults: Record<FormSource, Layout> }
interface Issue {
  id: string; number: string; template_code: string; template_version: number; template_status: string; record_ref: string;
  format: string; sha256: string; issued_by_name: string; issued_at: string;
}

const srcLabel = (t: ReturnType<typeof useI18n>["t"], s: string) => t(`fm.src.${s}` as StringKey);

// ------------------------------------------------------------------ list
export function FormsPage() {
  const { t, date } = useI18n();
  const [all, setAll] = useState(false);
  const [creating, setCreating] = useState(false);
  const list = useLoad(() => get<{ items: FormRow[]; can_edit: boolean }>(`/api/forms${all ? "?all=1" : ""}`), [all]);
  const items = list.data?.items ?? [];
  return (
    <>
      <PageHead title={t("fm.title")} lead={t("fm.lead")} actions={list.data?.can_edit &&
        <Button variant="primary" icon={<LuPlus />} onClick={() => setCreating(true)}>{t("fm.new")}</Button>} />
      <div className="panel">
        <div className="toolbar">
          <label className="row small"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />{t("tpl.showOld")}</label>
          <span className="spacer" />
          {list.data && <span className="small muted">{items.length}</span>}
        </div>
        {list.error ? <LoadError error={list.error} onRetry={list.reload} /> : list.data && items.length === 0 ? (
          <Empty icon={<LuFileText />} title={t("fm.empty")} body={t("fm.emptyBody")} />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>{t("fm.code")}</th><th>{t("fm.titleField")}</th><th>{t("fm.source")}</th><th>{t("fm.formatNo")}</th>
                <th>{t("fm.engine")}</th><th>{t("cw.col.state")}</th><th className="num">{t("fm.issued")}</th></tr></thead>
              {!list.data ? <LoadingRows rows={6} cols={7} /> : (
                <tbody>
                  {items.map((x) => (
                    <tr key={x.id} className="clickable" onClick={() => navigate(`/forms/${x.id}`)}>
                      <td className="nowrap"><button type="button" className="link-btn mono-id cell-main"
                        onClick={(e) => { e.stopPropagation(); navigate(`/forms/${x.id}`); }}>{x.code}</button>
                        <span className="small subtle"> {t("tpl.version", { n: x.version })}</span></td>
                      <td><div className="cell-main">{x.title}</div></td>
                      <td className="small">{srcLabel(t, x.source)}</td>
                      <td className="small nowrap mono-id">{x.format_no ?? "—"}{x.revision ? ` · ${t("fm.revision")} ${x.revision}` : ""}</td>
                      <td className="small nowrap">{x.engine === "docx" ? <><LuFileType aria-hidden /> {t("fm.engine.docx")}</> : t("fm.engine.blocks")}</td>
                      <td className="nowrap"><Badge tone={TONE[x.status]}>{t(`tpl.status.${x.status}` as StringKey)}</Badge>
                        {x.is_default && x.status === "approved" && <> <Badge tone="brand"><LuStar aria-hidden /> {t("fm.default")}</Badge></>}
                        {x.approved_at && <div className="small subtle">{date(x.approved_at)}</div>}</td>
                      <td className="num small">{x.issued}</td>
                    </tr>
                  ))}
                </tbody>
              )}
            </table>
          </div>
        )}
      </div>
      {creating && <NewForm onClose={() => setCreating(false)} />}
    </>
  );
}

function NewForm({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const [f, setF] = useState({ code: "", title: "", source: "calibration_record" as FormSource, engine: "blocks", format_no: "", revision: "00" });
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const save = async () => {
    setBusy(true); setErr(null); setErrs({});
    try {
      let file_id: string | undefined;
      if (f.engine === "docx") {
        if (!file) { setErrs({ file_id: t("fm.word") }); setBusy(false); return; }
        file_id = (await upload<{ id: string }>(file, file.name, "form")).id;
      }
      const out = await post<Form>("/api/forms", { ...f, format_no: f.format_no || null, revision: f.revision || null, file_id });
      navigate(`/forms/${out.id}`);
    } catch (e) { setErr(errorText(e, t)); setErrs(fieldErrors(e)); setBusy(false); }
  };
  return (
    <Sheet title={t("fm.new")} onClose={onClose} onSubmit={save} footer={<>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      <Button type="submit" variant="primary" busy={busy} disabled={!f.code || !f.title}>{t("common.create")}</Button>
    </>}>
      {err && <Notice tone="danger">{err}</Notice>}
      <SelectField label={t("fm.source")} value={f.source} onChange={(v) => setF({ ...f, source: v as FormSource })}
        options={SOURCES.map((s) => ({ value: s, label: srcLabel(t, s) }))} />
      <div className="grid-2">
        <TextField label={t("fm.code")} value={f.code} onChange={(v) => setF({ ...f, code: v.toUpperCase() })} error={errs.code} maxLength={40} dir="ltr" />
        <TextField label={t("fm.titleField")} value={f.title} onChange={(v) => setF({ ...f, title: v })} error={errs.title} maxLength={200} />
        <TextField label={t("fm.formatNo")} value={f.format_no} onChange={(v) => setF({ ...f, format_no: v })} maxLength={60} dir="ltr" />
        <TextField label={t("fm.revision")} value={f.revision} onChange={(v) => setF({ ...f, revision: v })} maxLength={20} dir="ltr" />
      </div>
      <div className="chip-row" role="radiogroup" aria-label={t("fm.engine")}>
        {(["blocks", "docx"] as const).map((e) => (
          <button key={e} type="button" className="chip" role="radio" aria-checked={f.engine === e} aria-pressed={f.engine === e}
            onClick={() => setF({ ...f, engine: e })}>{e === "docx" ? <LuFileType aria-hidden /> : <LuFileText aria-hidden />} {t(`fm.engine.${e}`)}</button>
        ))}
      </div>
      {f.engine === "docx" && (
        <div className="stack" style={{ gap: 8 }}>
          <label className="field-label" htmlFor="fm-file">{t("fm.word")}</label>
          <input id="fm-file" type="file" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          {errs.file_id && <span className="small cm-print-err">{errs.file_id}</span>}
          <p className="small muted">{t("fm.wordHint")}</p>
          <div><Button size="sm" variant="ghost" icon={<LuDownload />}
            onClick={() => fetchForm(`/api/forms/starter.docx?source=${f.source}`)}>{t("fm.starter")}</Button></div>
        </div>
      )}
    </Sheet>
  );
}

// ------------------------------------------------------------------ one form
export function FormPage({ id }: { id: string }) {
  const { t, dateTime } = useI18n();
  const toast = useToast();
  const meta = useLoad(() => get<Meta>("/api/forms/meta"), []);
  const [curId, setCurId] = useState(id);
  const form = useLoad(() => get<Form>(`/api/forms/${curId}`), [curId]);
  const issues = useLoad(() => get<{ items: Issue[] }>(`/api/forms/issues?template_id=${curId}`), [curId]);
  const [hdr, setHdr] = useState({ title: "", format_no: "", revision: "" });
  const [layout, setLayout] = useState<Layout | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ReactNode>(null);
  const [signing, setSigning] = useState(false);
  const [retiring, setRetiring] = useState(false);
  const f = form.data;

  useEffect(() => {
    if (!f) return;
    setHdr({ title: f.title, format_no: f.format_no ?? "", revision: f.revision ?? "" });
    setLayout(f.engine === "blocks" ? normalise(f.layout) : null);
    setDirty(false);
  }, [f]);

  if (form.error) return <div className="panel"><LoadError error={form.error} onRetry={form.reload} /></div>;
  if (!f || !meta.data) return <div className="panel"><LoadingRows rows={8} cols={3} /></div>;
  const editable = f.can_edit;
  const run = async (fn: () => Promise<unknown>, msg?: string) => {
    setBusy(true); setErr(null);
    try { await fn(); if (msg) toast(msg); } catch (e) { setErr(errorText(e, t)); } finally { setBusy(false); }
  };
  const save = () => run(async () => {
    const out = await patch<Form>(`/api/forms/${f.id}`, { title: hdr.title, format_no: hdr.format_no || null, revision: hdr.revision || null,
      ...(layout ? { layout } : {}) });
    form.setData(out); setDirty(false);
  }, t("fm.saved"));
  const replaceFile = (file: File) => run(async () => {
    const up = await upload<{ id: string }>(file, file.name, "form");
    form.setData(await patch<Form>(`/api/forms/${f.id}`, { file_id: up.id }));
  }, t("fm.saved"));
  const newVersion = () => run(async () => {
    const nv = await post<Form>(`/api/forms/${f.id}/new-version`);
    navigate(`/forms/${nv.id}`, true); setCurId(nv.id);
  });
  const makeDefault = () => run(async () => form.setData(await post<Form>(`/api/forms/${f.id}/default`)), t("fm.saved"));
  const printCopy = () => run(async () => {
    const win = window.open("", "_blank");
    try { await fetchForm(`/api/forms/${f.id}/print`, undefined, win); } catch (e) { win?.close(); throw e; }
    issues.reload();
  });
  const preview = () => run(async () => {
    const win = window.open("", "_blank");
    try { await fetchForm(`/api/forms/${f.id}/preview`, undefined, win); } catch (e) { win?.close(); throw e; }
  });

  return (
    <>
      <Link to="/forms" className="back-link"><LuArrowLeft className="flip-rtl" aria-hidden /> {t("fm.title")}</Link>
      <PageHead title={`${f.code} · ${f.title}`} lead={<span className="row wrap" style={{ gap: 6 }}>
        <Badge tone={TONE[f.status]}>{t(`tpl.status.${f.status}` as StringKey)}</Badge>
        {f.is_default && f.status === "approved" && <Badge tone="brand"><LuStar aria-hidden /> {t("fm.default")}</Badge>}
        <span className="small muted">{t("tpl.version", { n: f.version })} · {srcLabel(t, f.source)} · {t(`fm.engine.${f.engine}`)}</span>
      </span>} actions={<>
        <Button icon={<LuEye />} disabled={busy || dirty} onClick={preview}>{t("fm.preview")}</Button>
        {(f.status === "approved" || f.status === "draft") && <Button icon={<LuPrinter />} disabled={busy || dirty} onClick={printCopy}>{t("fm.printTest")}</Button>}
        {f.can_manage && f.status === "approved" && !f.is_default && <Button icon={<LuStar />} disabled={busy} onClick={makeDefault}>{t("fm.makeDefault")}</Button>}
        {f.can_manage && f.status !== "draft" && f.versions[0]?.id === f.id && f.status !== "retired" &&
          <Button icon={<LuCopyPlus />} disabled={busy} onClick={newVersion}>{t("fm.newVersion")}</Button>}
        {f.can_manage && f.status === "approved" && <Button variant="ghost" icon={<LuBan />} disabled={busy} onClick={() => setRetiring(true)}>{t("fm.retire")}</Button>}
        {f.can_approve && <Button icon={<LuSignature />} disabled={busy || dirty || (f.unknown?.length ?? 0) > 0} onClick={() => setSigning(true)}>{t("fm.approve")}</Button>}
        {editable && <Button variant="primary" busy={busy} disabled={!dirty} onClick={save}>{t("common.save")}</Button>}
      </>} />
      {err && <Notice tone="danger">{err}</Notice>}
      {!editable && <Notice>{t("fm.readOnly", { status: t(`tpl.status.${f.status}` as StringKey) })}</Notice>}
      {f.signature && <p className="small muted"><LuSignature aria-hidden /> {t("fm.approvedBy", { name: f.signature.user_name, date: dateTime(f.signature.signed_at) })}</p>}
      {!f.signature && f.approved_by_name && <p className="small muted"><LuSignature aria-hidden /> {t("fm.approvedBy", { name: f.approved_by_name, date: dateTime(f.approved_at) })}</p>}

      <div className="panel cm-pad">
        <div className="grid-3">
          <TextField label={t("fm.titleField")} value={hdr.title} disabled={!editable} maxLength={200}
            onChange={(v) => { setHdr({ ...hdr, title: v }); setDirty(true); }} />
          <TextField label={t("fm.formatNo")} value={hdr.format_no} disabled={!editable} maxLength={60} dir="ltr"
            onChange={(v) => { setHdr({ ...hdr, format_no: v }); setDirty(true); }} />
          <TextField label={t("fm.revision")} value={hdr.revision} disabled={!editable} maxLength={20} dir="ltr"
            onChange={(v) => { setHdr({ ...hdr, revision: v }); setDirty(true); }} />
        </div>
        {f.versions.length > 1 && (
          <div className="chip-row" style={{ marginTop: 12 }}>
            {f.versions.map((v) => (
              <button key={v.id} type="button" className="chip" aria-pressed={v.id === f.id}
                onClick={() => { navigate(`/forms/${v.id}`, true); setCurId(v.id); }}>
                {t("tpl.version", { n: v.version })} · {t(`tpl.status.${v.status}` as StringKey)}</button>
            ))}
          </div>
        )}
      </div>

      {f.engine === "blocks" && layout ? (
        <Designer source={f.source} layout={layout} meta={meta.data} editable={editable} hdr={hdr}
          onChange={(l) => { setLayout(l); setDirty(true); }} />
      ) : (
        <WordPanel form={f} meta={meta.data} busy={busy} onReplace={replaceFile} />
      )}

      <div className="panel">
        <div className="panel-head"><h3>{t("fm.issued")}</h3></div>
        {issues.error ? <LoadError error={issues.error} onRetry={issues.reload} /> : !issues.data ? <LoadingRows rows={3} cols={5} /> :
          issues.data.items.length === 0 ? <p className="small muted panel-body">{t("fm.noIssues")}</p> : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>{t("fm.is.number")}</th><th>{t("fm.is.record")}</th><th>{t("fm.is.form")}</th><th>{t("fm.is.by")}</th><th>{t("fm.is.when")}</th><th>SHA-256</th></tr></thead>
                <tbody>
                  {issues.data.items.map((i) => (
                    <tr key={i.id}>
                      <td className="mono-id nowrap">{i.number}</td>
                      <td className="small">{i.record_ref}</td>
                      <td className="small nowrap">{i.template_code} v{i.template_version}{i.template_status !== "approved" && <> · <Badge tone="warn">{t("fm.is.draft")}</Badge></>} · {i.format.toUpperCase()}</td>
                      <td className="small">{i.issued_by_name}</td>
                      <td className="small nowrap">{dateTime(i.issued_at)}</td>
                      <td className="small mono-id" title={i.sha256}>{i.sha256.slice(0, 12)}…</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>

      {signing && <SignDialog title={t("fm.approve")} lead={`${f.code} v${f.version} · ${f.title}`} meaning="form_approved"
        onClose={() => setSigning(false)} onSign={async (s) => { form.setData(await post<Form>(`/api/forms/${f.id}/approve`, s)); }} />}
      {retiring && <RetireSheet onClose={() => setRetiring(false)}
        onRetire={async (reason) => { form.setData(await post<Form>(`/api/forms/${f.id}/retire`, { reason })); }} />}
    </>
  );
}

function RetireSheet({ onClose, onRetire }: { onClose: () => void; onRetire: (reason: string) => Promise<void> }) {
  const { t } = useI18n();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    setBusy(true);
    try { await onRetire(reason.trim()); onClose(); } catch (e) { setErr(errorText(e, t)); setBusy(false); }
  };
  return (
    <Sheet title={t("fm.retire")} onClose={onClose} onSubmit={go} footer={<>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      <Button type="submit" variant="danger" busy={busy} disabled={reason.trim().length < 3}>{t("fm.retire")}</Button>
    </>}>
      {err && <Notice tone="danger">{err}</Notice>}
      <TextField label={t("fm.retireReason")} value={reason} onChange={setReason} maxLength={500} required />
    </Sheet>
  );
}

// ------------------------------------------------------------------ merge fields
function FieldList({ meta, source }: { meta: Meta; source: FormSource }) {
  const { t } = useI18n();
  const toast = useToast();
  const groups = { ...meta.fields[source], ...meta.fields._common };
  const copy = (f: string) => {
    const v = `{{${f}}}`;
    navigator.clipboard?.writeText(v).then(() => toast(t("fm.copied", { f: v })), () => toast(v));
  };
  return (
    <details className="cm-fields">
      <summary>{t("fm.fields")} <span className="small subtle">· {t("fm.fieldsHint")}</span></summary>
      {Object.entries(groups).map(([g, list]) => (
        <div key={g} className="cm-field-group">
          <span className="small subtle mono-id">{g}</span>
          <div className="cm-field-chips">
            {list.map((x) => <button key={x} type="button" className="cm-field-chip" onClick={() => copy(`${g}.${x}`)}>{x}</button>)}
          </div>
        </div>
      ))}
    </details>
  );
}

function WordPanel({ form: f, meta, busy, onReplace }: { form: Form; meta: Meta; busy: boolean; onReplace: (file: File) => void }) {
  const { t } = useI18n();
  const ref = useRef<HTMLInputElement>(null);
  return (
    <div className="panel stack cm-sec">
      <div className="panel-head"><h3><LuFileType aria-hidden /> {t("fm.word")}</h3></div>
      <p className="row wrap" style={{ gap: 8 }}>
        <span className="mono-id">{f.filename}</span>
        {f.can_edit && <>
          <input ref={ref} type="file" hidden accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(e) => { const x = e.target.files?.[0]; if (x) onReplace(x); e.target.value = ""; }} />
          <Button size="sm" icon={<LuUpload />} busy={busy} onClick={() => ref.current?.click()}>{t("fm.replaceFile")}</Button>
        </>}
        <Button size="sm" variant="ghost" icon={<LuDownload />} onClick={() => fetchForm(`/api/forms/starter.docx?source=${f.source}`)}>{t("fm.starter")}</Button>
      </p>
      <p className="small muted">{t("fm.wordHint")}</p>
      {(f.unknown?.length ?? 0) > 0 && <Notice tone="danger">{t("fm.unknown", { f: (f.unknown ?? []).join(", ") })}</Notice>}
      <div>
        <span className="small muted">{t("fm.used", { n: f.placeholders?.length ?? 0 })}</span>
        <div className="cm-field-chips">
          {(f.placeholders ?? []).map((p) => <span key={p} className={`cm-field-chip static ${f.unknown?.includes(p) ? "bad" : ""}`}>{p}</span>)}
        </div>
      </div>
      <FieldList meta={meta} source={f.source} />
    </div>
  );
}

// ------------------------------------------------------------------ block designer
function normalise(l: Layout | null | undefined): Layout {
  return { page: { size: "A4", orientation: "portrait", margin_mm: 10, ...(l?.page ?? {}) },
    header: { logo: true, show_company: true, title: "", subtitle: "", annexure: "", ...(l?.header ?? {}) },
    blocks: l?.blocks ?? [], footer: { text: "", page_numbers: true, ...(l?.footer ?? {}) } };
}

function newBlock(type: Block["type"], source: FormSource): Block {
  switch (type) {
    case "fields": return { type, columns: 2, items: [{ label: "", value: "" }] };
    case "table": {
      const list = LISTS_FOR[source][0] ?? "rows";
      return { type, list, font: 8, title: "", columns: [{ header: "No.", value: `{{${list}.n}}`, width: 0.6 }, { header: "", value: "", width: 3 }] };
    }
    case "text": return { type, text: "", style: "normal" };
    case "signatures": return { type, items: [{ label: "Prepared by", value: "", date: "" }, { label: "Approved by", value: "", date: "" },
      { label: "Authorized by", value: "", date: "" }] };
    default: return { type: "spacer", mm: 4 };
  }
}

function usePreview(source: FormSource, layout: Layout, hdr: { title: string; format_no: string; revision: string }) {
  const [pages, setPages] = useState<{ pages: string[]; count: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const body = useMemo(() => ({ source, layout, title: hdr.title || "Preview", format_no: hdr.format_no || null,
    revision: hdr.revision || null }), [source, layout, hdr]);
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(async () => {
      setBusy(true);
      try {
        const out = await post<{ pages: string[]; count: number }>("/api/forms/preview?as=png", body);
        if (!cancelled) { setPages(out); setErr(null); }
      } catch (e) { if (!cancelled) setErr((e as Error).message); } finally { if (!cancelled) setBusy(false); }
    }, 700);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [body, tick]);
  return { pages, err, busy, refresh: () => setTick((x) => x + 1) };
}

function Designer({ source, layout, meta, editable, hdr, onChange }: {
  source: FormSource; layout: Layout; meta: Meta; editable: boolean; hdr: { title: string; format_no: string; revision: string };
  onChange: (l: Layout) => void;
}) {
  const { t } = useI18n();
  const pv = usePreview(source, layout, hdr);
  const logoRef = useRef<HTMLInputElement>(null);
  const [logoBusy, setLogoBusy] = useState(false);
  const set = (p: Partial<Layout>) => onChange({ ...layout, ...p });
  const setBlock = (i: number, b: Block) => set({ blocks: layout.blocks.map((x, j) => (j === i ? b : x)) });
  const move = (i: number, d: number) => {
    const b = [...layout.blocks];
    const [x] = b.splice(i, 1);
    b.splice(i + d, 0, x);
    set({ blocks: b });
  };
  const uploadLogo = async (file: File) => {
    setLogoBusy(true);
    try {
      const up = await upload<{ id: string }>(file, file.name, "logo");
      set({ header: { ...layout.header, logo: true, logo_file_id: up.id } });
    } finally { setLogoBusy(false); }
  };
  const lists = LISTS_FOR[source];
  return (
    <div className="cm-fd">
      <div className="cm-fd-edit stack">
        <section className="panel stack cm-sec">
          <div className="panel-head"><h3>{t("fd.page")}</h3></div>
          <div className="grid-3">
            <SelectField label={t("fd.size")} value={layout.page.size ?? "A4"} disabled={!editable}
              onChange={(v) => set({ page: { ...layout.page, size: v } })} options={["A4", "A5", "A6"].map((x) => ({ value: x, label: x }))} />
            <SelectField label={t("fd.orientation")} value={layout.page.orientation ?? "portrait"} disabled={!editable}
              onChange={(v) => set({ page: { ...layout.page, orientation: v } })}
              options={[{ value: "portrait", label: t("fd.portrait") }, { value: "landscape", label: t("fd.landscape") }]} />
            <TextField label={t("fd.margin")} value={String(layout.page.margin_mm ?? 10)} disabled={!editable} inputMode="numeric" dir="ltr"
              onChange={(v) => set({ page: { ...layout.page, margin_mm: Math.max(3, Math.min(30, Number(v) || 0)) } })} />
          </div>
        </section>

        <section className="panel stack cm-sec">
          <div className="panel-head"><h3>{t("fd.header")}</h3></div>
          <TextField label={t("fm.titleField")} value={layout.header.title ?? ""} disabled={!editable} maxLength={300}
            onChange={(v) => set({ header: { ...layout.header, title: v } })} />
          <div className="grid-2">
            <TextField label={t("fd.subtitle")} value={layout.header.subtitle ?? ""} disabled={!editable} maxLength={300}
              onChange={(v) => set({ header: { ...layout.header, subtitle: v } })} />
            <TextField label={t("fd.annexure")} value={layout.header.annexure ?? ""} disabled={!editable} maxLength={120}
              onChange={(v) => set({ header: { ...layout.header, annexure: v } })} />
          </div>
          <div className="row wrap" style={{ gap: 16 }}>
            <label className="row small"><Switch checked={!!layout.header.logo} disabled={!editable} label={t("fd.logo")}
              onChange={(v) => set({ header: { ...layout.header, logo: v } })} />{t("fd.logo")}</label>
            <label className="row small"><Switch checked={layout.header.show_company !== false} disabled={!editable} label={t("fd.company")}
              onChange={(v) => set({ header: { ...layout.header, show_company: v } })} />{t("fd.company")}</label>
            {editable && <>
              <input ref={logoRef} type="file" hidden accept="image/png,image/jpeg"
                onChange={(e) => { const x = e.target.files?.[0]; if (x) uploadLogo(x); e.target.value = ""; }} />
              <Button size="sm" icon={<LuImage />} busy={logoBusy} onClick={() => logoRef.current?.click()}>{t("fd.logoUpload")}</Button>
            </>}
          </div>
        </section>

        <section className="panel stack cm-sec">
          <div className="panel-head"><h3>{t("fd.blocks")}</h3></div>
          {layout.blocks.map((b, i) => (
            <div key={i} className="cm-block">
              <div className="cm-block-head">
                <span className="cm-block-n num">{i + 1}</span>
                <strong>{t(`fd.b.${b.type}` as StringKey)}</strong>
                <span className="spacer" />
                {editable && <>
                  <Button size="sm" variant="ghost" iconOnly aria-label={t("fd.moveUp")} icon={<LuArrowUp />} disabled={i === 0} onClick={() => move(i, -1)} />
                  <Button size="sm" variant="ghost" iconOnly aria-label={t("fd.moveDown")} icon={<LuArrowDown />} disabled={i === layout.blocks.length - 1} onClick={() => move(i, 1)} />
                  <Button size="sm" variant="ghost" iconOnly aria-label={t("fd.remove")} icon={<LuTrash2 />}
                    onClick={() => set({ blocks: layout.blocks.filter((_, j) => j !== i) })} />
                </>}
              </div>
              <BlockEditor block={b} lists={lists} editable={editable} onChange={(nb) => setBlock(i, nb)} />
            </div>
          ))}
          {editable && (
            <div className="row wrap" style={{ gap: 6 }}>
              <span className="small muted">{t("fd.add")}:</span>
              {(["fields", "table", "text", "signatures", "spacer"] as const).filter((x) => x !== "table" || lists.length > 0).map((x) => (
                <Button key={x} size="sm" icon={<LuPlus />} onClick={() => set({ blocks: [...layout.blocks, newBlock(x, source)] })}>{t(`fd.b.${x}` as StringKey)}</Button>
              ))}
            </div>
          )}
        </section>

        <section className="panel stack cm-sec">
          <div className="panel-head"><h3>{t("fd.footer")}</h3></div>
          <TextField label={t("fd.footerText")} value={layout.footer.text ?? ""} disabled={!editable} maxLength={300}
            onChange={(v) => set({ footer: { ...layout.footer, text: v } })} />
          <label className="row small"><Switch checked={layout.footer.page_numbers !== false} disabled={!editable} label={t("fd.pageNumbers")}
            onChange={(v) => set({ footer: { ...layout.footer, page_numbers: v } })} />{t("fd.pageNumbers")}</label>
        </section>
        <div className="panel cm-pad"><FieldList meta={meta} source={source} /></div>
      </div>

      <aside className="cm-fd-preview panel cm-sec">
        <div className="panel-head">
          <h3>{t("fd.livePreview")}</h3>
          <span className="spacer" />
          <Button size="sm" variant="ghost" iconOnly aria-label={t("fd.refresh")} icon={<LuRefreshCw />} busy={pv.busy} onClick={pv.refresh} />
        </div>
        {pv.err && <Notice tone="danger">{pv.err}</Notice>}
        <div className={`cm-fd-frame ${pv.busy ? "busy" : ""}`} aria-busy={pv.busy}>
          {pv.pages ? pv.pages.pages.map((src, i) => (
            <img key={i} src={src} alt={t("fd.pageOf", { n: i + 1, total: pv.pages?.count ?? 1 })} className="cm-fd-page" />
          )) : <div className="cm-fd-wait" />}
          {pv.pages && pv.pages.count > pv.pages.pages.length && <p className="small muted">{t("fd.morePages", { n: pv.pages.count - pv.pages.pages.length })}</p>}
        </div>
      </aside>
    </div>
  );
}

function BlockEditor({ block: b, lists, editable, onChange }: { block: Block; lists: string[]; editable: boolean; onChange: (b: Block) => void }) {
  const { t } = useI18n();
  const items = (b.items ?? []) as Cell[];
  const setItem = (i: number, p: Partial<Cell>) => onChange({ ...b, items: items.map((x, j) => (j === i ? { ...x, ...p } : x)) });
  const cell = (label: string, value: string, fn: (v: string) => void, cls = "grow") => (
    <input className={`input cell-input ${cls}`} aria-label={label} placeholder={label} value={value} disabled={!editable} dir="auto"
      onChange={(e) => fn(e.target.value)} />
  );
  const rm = (fn: () => void) => editable && <Button size="sm" variant="ghost" iconOnly aria-label={t("fd.remove")} icon={<LuTrash2 />} onClick={fn} />;

  if (b.type === "fields" || b.type === "signatures") return (
    <div className="stack" style={{ gap: 6 }}>
      {b.type === "fields" && (
        <label className="row small" style={{ gap: 6 }}>{t("fd.columns")}
          <select className="select cell-input narrow" value={Number(b.columns ?? 2)} disabled={!editable}
            onChange={(e) => onChange({ ...b, columns: Number(e.target.value) })}>
            {[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      )}
      {items.map((it, i) => (
        <div key={i} className="row" style={{ gap: 6 }}>
          {cell(t("fd.label"), it.label ?? "", (v) => setItem(i, { label: v }), "cm-fd-label")}
          {cell(t("fd.value"), it.value ?? "", (v) => setItem(i, { value: v }))}
          {b.type === "signatures" && cell(t("fd.date"), it.date ?? "", (v) => setItem(i, { date: v }), "cm-fd-label")}
          {rm(() => onChange({ ...b, items: items.filter((_, j) => j !== i) }))}
        </div>
      ))}
      {editable && <div><Button size="sm" variant="ghost" icon={<LuPlus />}
        onClick={() => onChange({ ...b, items: [...items, { label: "", value: "", ...(b.type === "signatures" ? { date: "" } : {}) }] })}>{t("fd.addItem")}</Button></div>}
    </div>
  );
  if (b.type === "table") {
    const cols = (Array.isArray(b.columns) ? b.columns : []) as Cell[];
    const setCol = (i: number, p: Partial<Cell>) => onChange({ ...b, columns: cols.map((x, j) => (j === i ? { ...x, ...p } : x)) });
    return (
      <div className="stack" style={{ gap: 6 }}>
        <div className="cm-fd-tset">
          <label className="row small" style={{ gap: 6 }}>{t("fd.list")}
            <select className="select cell-input" value={b.list ?? lists[0]} disabled={!editable} onChange={(e) => onChange({ ...b, list: e.target.value })}>
              {lists.map((l) => <option key={l} value={l}>{t(`fd.list.${l}` as StringKey)}</option>)}
            </select>
          </label>
          <label className="row small" style={{ gap: 6 }}>{t("fd.font")}
            <select className="select cell-input narrow" value={Number(b.font ?? 8)} disabled={!editable} onChange={(e) => onChange({ ...b, font: Number(e.target.value) })}>
              {[6, 7, 8, 9, 10, 11].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        </div>
        {cell(t("fd.header_"), b.title ?? "", (v) => onChange({ ...b, title: v }), "")}
        <div className="cm-fd-cols">
          {cols.map((c, i) => (
            <div key={i} className="row" style={{ gap: 6 }}>
              {cell(t("fd.header_"), c.header ?? "", (v) => setCol(i, { header: v }), "cm-fd-label")}
              {cell(t("fd.value"), c.value ?? "", (v) => setCol(i, { value: v }))}
              <input className="input cell-input narrow" aria-label={t("fd.width")} title={t("fd.width")} inputMode="decimal" dir="ltr" disabled={!editable}
                value={String(c.width ?? 1)} onChange={(e) => setCol(i, { width: Math.max(0.2, Number(e.target.value.replace(",", ".")) || 1) })} />
              {rm(() => onChange({ ...b, columns: cols.filter((_, j) => j !== i) }))}
            </div>
          ))}
        </div>
        {editable && <div><Button size="sm" variant="ghost" icon={<LuPlus />}
          onClick={() => onChange({ ...b, columns: [...cols, { header: "", value: `{{${b.list ?? lists[0]}.}}`, width: 1 }] })}>{t("fd.addColumn")}</Button></div>}
      </div>
    );
  }
  if (b.type === "text") return (
    <div className="stack" style={{ gap: 6 }}>
      <textarea className="input" rows={2} aria-label={t("fd.text")} value={b.text ?? ""} disabled={!editable} dir="auto"
        onChange={(e) => onChange({ ...b, text: e.target.value })} />
      <label className="row small" style={{ gap: 6 }}>{t("fd.style")}
        <select className="select cell-input" value={b.style ?? "normal"} disabled={!editable} onChange={(e) => onChange({ ...b, style: e.target.value })}>
          {["normal", "small", "heading"].map((s) => <option key={s} value={s}>{t(`fd.style.${s}` as StringKey)}</option>)}
        </select>
      </label>
    </div>
  );
  return (
    <label className="row small" style={{ gap: 6 }}>{t("fd.mm")}
      <input className="input cell-input narrow" inputMode="numeric" dir="ltr" value={String(b.mm ?? 4)} disabled={!editable}
        onChange={(e) => onChange({ ...b, mm: Math.max(1, Math.min(60, Number(e.target.value) || 1)) })} />
    </label>
  );
}

