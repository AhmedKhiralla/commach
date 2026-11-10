// PM checklists: the editor (check / reading / text / photo items with manual page references, spare parts,
// suggested interval) and the AI sheet that drafts checklists from a maintenance manual.
import { useEffect, useState } from "react";
import { LuCopyPlus, LuFileText, LuPlus, LuSignature, LuSparkles, LuTrash2, LuUpload } from "react-icons/lu";
import { Badge, Button, Notice, SelectField, Sheet, Switch, TextField, errorText, fieldErrors, useToast } from "../components/ui";
import { SignDialog } from "../components/work";
import { get, patch, post, upload } from "../lib/api";
import { useI18n } from "../lib/i18n";
import type { StringKey } from "../lib/strings";
import { nameOf, type AssetClass, type AssetRow } from "../lib/types";

type ItemType = "check" | "reading" | "text" | "photo";
interface Item {
  item_type: ItemType; parameter: string; unit: string | null; low_limit: number | null; high_limit: number | null;
  required: boolean; notes: string | null; page_ref: string | null;
}
interface SparePart { name: string; part_no: string | null; qty: number | null; unit: string | null; page_ref: string | null; notes: string | null }
interface PmTemplate {
  id: string; code: string; version: number; title: string; title_ar: string | null; status: string; class_id: string | null;
  method_ref: string | null; description: string | null; items: Item[]; spare_parts: SparePart[];
  suggested_interval_value: number | null; suggested_interval_unit: string | null; est_minutes: number | null;
  source_file_id: string | null; source_filename: string | null; ai_suggestion_id: string | null; approved_at: string | null;
  approved_by_name: string | null; versions: { id: string; version: number; status: string }[]; can_edit: boolean; can_approve: boolean;
}
type IF = { item_type: ItemType; parameter: string; unit: string; low_limit: string; high_limit: string; required: boolean; notes: string; page_ref: string };
type PF = { name: string; part_no: string; qty: string; unit: string; page_ref: string };
const str = (v: unknown) => (v === null || v === undefined ? "" : String(v));
const num = (v: string) => (v.trim() === "" ? null : Number(v.replace(",", ".")));
const TYPES: ItemType[] = ["check", "reading", "text", "photo"];
const UNITS = ["day", "week", "month", "year"] as const;
const toIF = (i: Item): IF => ({ item_type: i.item_type, parameter: i.parameter, unit: str(i.unit), low_limit: str(i.low_limit), high_limit: str(i.high_limit),
  required: i.required ?? true, notes: str(i.notes), page_ref: str(i.page_ref) });
const toPF = (p: SparePart): PF => ({ name: p.name, part_no: str(p.part_no), qty: str(p.qty), unit: str(p.unit), page_ref: str(p.page_ref) });
const fromIF = (i: IF) => ({ item_type: i.item_type, parameter: i.parameter.trim() || "?", unit: i.unit || null,
  low_limit: i.item_type === "reading" ? num(i.low_limit) : null, high_limit: i.item_type === "reading" ? num(i.high_limit) : null,
  required: i.required, notes: i.notes || null, page_ref: i.page_ref || null });
const fromPF = (p: PF) => ({ name: p.name.trim(), part_no: p.part_no || null, qty: num(p.qty), unit: p.unit || null, page_ref: p.page_ref || null });
const blank = (): IF => ({ item_type: "check", parameter: "", unit: "", low_limit: "", high_limit: "", required: true, notes: "", page_ref: "" });

function ItemsEditor({ items, onChange, editable }: { items: IF[]; onChange: (i: IF[]) => void; editable: boolean }) {
  const { t } = useI18n();
  const set = (i: number, p: Partial<IF>) => onChange(items.map((x, j) => (j === i ? { ...x, ...p } : x)));
  return (
    <>
      <ol className="cm-pm-edit">
        {items.map((it, i) => (
          <li key={i}>
            <span className="cm-pm-n num">{i + 1}</span>
            <div className="grow stack" style={{ gap: 6 }}>
              <div className="row wrap" style={{ gap: 6 }}>
                <select className="select cell-input" aria-label={t("pmtpl.type")} value={it.item_type} disabled={!editable}
                  onChange={(e) => set(i, { item_type: e.target.value as ItemType })}>
                  {TYPES.map((x) => <option key={x} value={x}>{t(`pmtpl.type.${x}` as StringKey)}</option>)}
                </select>
                <input className="input grow" aria-label={t("pmtpl.instruction")} placeholder={t("pmtpl.instruction")} value={it.parameter} disabled={!editable}
                  onChange={(e) => set(i, { parameter: e.target.value })} />
              </div>
              <div className="row wrap" style={{ gap: 6 }}>
                {(it.item_type === "reading" || it.item_type === "text") && <input className="input narrow" aria-label={t("pmt.unit")} placeholder={t("pmt.unit")}
                  value={it.unit} disabled={!editable} onChange={(e) => set(i, { unit: e.target.value })} />}
                {it.item_type === "reading" && <>
                  <input className="input narrow" inputMode="decimal" dir="ltr" aria-label={t("task.r.low")} placeholder={t("task.r.low")} value={it.low_limit}
                    disabled={!editable} onChange={(e) => set(i, { low_limit: e.target.value })} />
                  <input className="input narrow" inputMode="decimal" dir="ltr" aria-label={t("task.r.high")} placeholder={t("task.r.high")} value={it.high_limit}
                    disabled={!editable} onChange={(e) => set(i, { high_limit: e.target.value })} />
                </>}
                <input className="input narrow" aria-label={t("pmtpl.page")} placeholder={t("pmtpl.page")} value={it.page_ref} disabled={!editable}
                  onChange={(e) => set(i, { page_ref: e.target.value })} />
                <input className="input grow" aria-label={t("af.notes")} placeholder={t("af.notes")} value={it.notes} disabled={!editable}
                  onChange={(e) => set(i, { notes: e.target.value })} />
                <label className="row small"><Switch checked={it.required} disabled={!editable} onChange={(v) => set(i, { required: v })} label={t("pmtpl.required")} />{t("pmtpl.required")}</label>
                {editable && <Button size="sm" variant="ghost" iconOnly aria-label={t("common.delete")} icon={<LuTrash2 />} onClick={() => onChange(items.filter((_, j) => j !== i))} />}
              </div>
            </div>
          </li>
        ))}
      </ol>
      {editable && <div><Button size="sm" icon={<LuPlus />} onClick={() => onChange([...items, blank()])}>{t("pmtpl.addItem")}</Button></div>}
    </>
  );
}

function PartsEditor({ parts, onChange, editable }: { parts: PF[]; onChange: (p: PF[]) => void; editable: boolean }) {
  const { t } = useI18n();
  const set = (i: number, p: Partial<PF>) => onChange(parts.map((x, j) => (j === i ? { ...x, ...p } : x)));
  return (
    <>
      {parts.length > 0 && (
        <div className="table-wrap"><table className="table edit-table">
          <thead><tr><th>{t("pmt.partName")}</th><th>{t("pmt.partNo")}</th><th>{t("pmt.qty")}</th><th>{t("pmt.unit")}</th><th>{t("pmtpl.page")}</th>{editable && <th />}</tr></thead>
          <tbody>{parts.map((p, i) => (
            <tr key={i}>
              <td><input className="input cell-input wide" aria-label={t("pmt.partName")} value={p.name} disabled={!editable} onChange={(e) => set(i, { name: e.target.value })} /></td>
              <td><input className="input cell-input" style={{ width: 120 }} aria-label={t("pmt.partNo")} value={p.part_no} disabled={!editable} onChange={(e) => set(i, { part_no: e.target.value })} /></td>
              <td><input className="input cell-input narrow" inputMode="decimal" aria-label={t("pmt.qty")} value={p.qty} disabled={!editable} onChange={(e) => set(i, { qty: e.target.value })} /></td>
              <td><input className="input cell-input narrow" aria-label={t("pmt.unit")} value={p.unit} disabled={!editable} onChange={(e) => set(i, { unit: e.target.value })} /></td>
              <td><input className="input cell-input narrow" aria-label={t("pmtpl.page")} value={p.page_ref} disabled={!editable} onChange={(e) => set(i, { page_ref: e.target.value })} /></td>
              {editable && <td><Button size="sm" variant="ghost" iconOnly aria-label={t("common.delete")} icon={<LuTrash2 />} onClick={() => onChange(parts.filter((_, j) => j !== i))} /></td>}
            </tr>))}</tbody>
        </table></div>
      )}
      {editable && <div><Button size="sm" icon={<LuPlus />} onClick={() => onChange([...parts, { name: "", part_no: "", qty: "1", unit: "", page_ref: "" }])}>{t("pmt.addPart")}</Button></div>}
    </>
  );
}

export function PmTemplateEditor({ id, onClose, onChanged }: { id: string | "new"; onClose: () => void; onChanged: () => void }) {
  const { t, lang, dateTime } = useI18n();
  const toast = useToast();
  const [tpl, setTpl] = useState<PmTemplate | null>(null);
  const [classes, setClasses] = useState<AssetClass[]>([]);
  const [f, setF] = useState({ code: "", title: "", title_ar: "", class_id: "", method_ref: "", iv: "1", iu: "month", minutes: "" });
  const [items, setItems] = useState<IF[]>([blank()]);
  const [parts, setParts] = useState<PF[]>([]);
  const [curId, setCurId] = useState(id);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [signing, setSigning] = useState(false);
  const fill = (x: PmTemplate) => {
    setTpl(x);
    setF({ code: x.code, title: x.title, title_ar: str(x.title_ar), class_id: str(x.class_id), method_ref: str(x.method_ref),
      iv: str(x.suggested_interval_value || 1), iu: x.suggested_interval_unit || "month", minutes: str(x.est_minutes) });
    setItems(x.items.map(toIF)); setParts((x.spare_parts ?? []).map(toPF));
  };
  useEffect(() => {
    get<{ items: AssetClass[] }>("/api/asset-classes").then((r) => setClasses(r.items)).catch(() => {});
    if (curId !== "new") get<PmTemplate>(`/api/templates/${curId}`).then(fill).catch((e) => setErr(errorText(e, t)));
  }, [curId]); // eslint-disable-line react-hooks/exhaustive-deps
  const editable = curId === "new" || !!tpl?.can_edit;
  const status = tpl?.status ?? "draft";
  const save = async () => {
    setBusy(true); setErr(null); setErrs({});
    const body = { title: f.title, title_ar: f.title_ar || null, class_id: f.class_id || null, method_ref: f.method_ref || null,
      suggested_interval_value: num(f.iv), suggested_interval_unit: f.iu, est_minutes: num(f.minutes),
      items: items.map(fromIF), spare_parts: parts.filter((p) => p.name.trim()).map(fromPF) };
    try {
      const out = curId === "new" ? await post<PmTemplate>("/api/templates", { ...body, kind: "pm", code: f.code })
        : await patch<PmTemplate>(`/api/templates/${curId}`, body);
      fill(out); setCurId(out.id); onChanged(); toast(t("list.saved"));
    } catch (e) { setErr(errorText(e, t)); setErrs(fieldErrors(e)); } finally { setBusy(false); }
  };
  const newVersion = async () => {
    setBusy(true); setErr(null);
    try { const out = await post<PmTemplate>(`/api/templates/${curId}/new-version`); fill(out); setCurId(out.id); onChanged(); }
    catch (e) { setErr(errorText(e, t)); } finally { setBusy(false); }
  };
  return (
    <>
      <Sheet wide title={curId === "new" ? t("pmtpl.new") : `${tpl?.code ?? ""} ${tpl ? t("tpl.version", { n: tpl.version }) : ""}`}
        lead={tpl ? <Badge tone={status === "approved" ? "ok" : status === "draft" ? "warn" : undefined}>{t(`tpl.status.${status}` as StringKey)}</Badge> : undefined}
        onClose={onClose} onSubmit={editable ? save : undefined} footer={<>
          <Button onClick={onClose}>{t("common.close")}</Button>
          {tpl && !editable && (status === "approved" || status === "superseded") && <Button icon={<LuCopyPlus />} busy={busy} onClick={newVersion}>{t("tpl.newVersion")}</Button>}
          {tpl?.can_approve && <Button icon={<LuSignature />} disabled={busy} onClick={() => setSigning(true)}>{t("tpl.approve")}</Button>}
          {editable && <Button type="submit" variant="primary" busy={busy}>{t("common.save")}</Button>}
        </>}>
        {err && <Notice tone="danger">{err}</Notice>}
        {tpl && !editable && <Notice>{t("tpl.readOnly", { status: t(`tpl.status.${status}` as StringKey) })}</Notice>}
        {tpl?.approved_by_name && <p className="small muted"><LuSignature aria-hidden /> {t("tpl.approvedBy", { name: tpl.approved_by_name, date: dateTime(tpl.approved_at) })}</p>}
        {tpl?.source_file_id && <p className="small muted"><LuFileText aria-hidden /> {t("pmtpl.manual")}: <a href={`/api/files/${tpl.source_file_id}/content`}>{tpl.source_filename}</a>
          {tpl.ai_suggestion_id && <> · <LuSparkles aria-hidden /> AI</>}</p>}
        <div className="grid-2">
          <TextField label={t("tpl.code")} value={f.code} onChange={(v) => setF({ ...f, code: v.toUpperCase() })} disabled={curId !== "new"} error={errs.code} maxLength={40} dir="ltr" />
          <SelectField label={t("tpl.class")} value={f.class_id} onChange={(v) => setF({ ...f, class_id: v })} disabled={!editable}
            options={[{ value: "", label: "—" }, ...classes.filter((c) => c.pm_default || c.id === f.class_id).map((c) => ({ value: c.id, label: `${c.code} · ${nameOf(c, lang)}` }))]} />
          <TextField label={t("tpl.titleField")} value={f.title} onChange={(v) => setF({ ...f, title: v })} disabled={!editable} error={errs.title} maxLength={200} />
          <TextField label={t("tpl.title_ar")} value={f.title_ar} onChange={(v) => setF({ ...f, title_ar: v })} disabled={!editable} maxLength={200} dir="rtl" lang="ar" />
          <TextField label={t("tpl.method")} value={f.method_ref} onChange={(v) => setF({ ...f, method_ref: v })} disabled={!editable} maxLength={120} />
          <div className="grid-2">
            <div className="field">
              <label htmlFor="pm-iv">{t("pmtpl.interval")}</label>
              <div className="row" style={{ gap: 6 }}>
                <input id="pm-iv" className="input" type="number" min={1} style={{ width: 70 }} value={f.iv} disabled={!editable} onChange={(e) => setF({ ...f, iv: e.target.value })} />
                <select className="select" aria-label={t("pmtpl.interval")} value={f.iu} disabled={!editable} onChange={(e) => setF({ ...f, iu: e.target.value })}>
                  {UNITS.map((u) => <option key={u} value={u}>{t(`unit.${u}` as StringKey)}</option>)}
                </select>
              </div>
            </div>
            <TextField label={t("pmtpl.minutes")} type="number" min={1} value={f.minutes} onChange={(v) => setF({ ...f, minutes: v })} disabled={!editable} />
          </div>
        </div>
        <h3 className="sheet-section">{t("pmtpl.items")}</h3>
        <ItemsEditor items={items} onChange={setItems} editable={editable} />
        <h3 className="sheet-section">{t("pmtpl.parts")}</h3>
        <PartsEditor parts={parts} onChange={setParts} editable={editable} />
        {tpl && tpl.versions.length > 1 && (
          <div className="chip-row">
            {tpl.versions.map((v) => <button key={v.id} type="button" className="chip" aria-pressed={v.id === tpl.id} onClick={() => setCurId(v.id)}>
              {t("tpl.version", { n: v.version })} · {t(`tpl.status.${v.status}` as StringKey)}</button>)}
          </div>
        )}
      </Sheet>
      {signing && tpl && (
        <SignDialog title={`${t("tpl.approve")} · ${tpl.code} ${t("tpl.version", { n: tpl.version })}`} meaning="template_approved" onClose={() => setSigning(false)}
          onSign={async (v) => { fill(await post<PmTemplate>(`/api/templates/${tpl.id}/approve`, { ...v, reason: v.reason || null })); onChanged(); }} />
      )}
    </>
  );
}

// ------------------------------------------------------------------ manual -> draft checklists
interface Group {
  suggestion: { id: string; model: string }; title: string; interval_value: number; interval_unit: string; est_minutes: number | null;
  items: Item[]; spare_parts: SparePart[];
}
interface ManualOut { groups: Group[]; equipment: Record<string, string>; notes: string; pages_read: number; pages_total: number; mode: string }

export function ManualSheet({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [fileId, setFileId] = useState<string | null>(null);
  const [assetId, setAssetId] = useState("");
  const [classId, setClassId] = useState("");
  const [assets, setAssets] = useState<AssetRow[]>([]);
  const [classes, setClasses] = useState<AssetClass[]>([]);
  const [out, setOut] = useState<ManualOut | null>(null);
  const [drafts, setDrafts] = useState<{ on: boolean; code: string; title: string; iv: string; iu: string; items: IF[]; parts: PF[] }[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    get<{ items: AssetRow[] }>("/api/assets?pm=1&top=1&page_size=500&sort=code").then((r) => setAssets(r.items)).catch(() => {});
    get<{ items: AssetClass[] }>("/api/asset-classes").then((r) => setClasses(r.items.filter((c) => c.pm_default))).catch(() => {});
  }, []);
  const read = async () => {
    if (!file) return;
    setBusy("read"); setErr(null);
    try {
      const fid = fileId ?? (await upload<{ id: string }>(file, file.name, "manual")).id;
      setFileId(fid);
      const r = await post<ManualOut>("/api/ai/manual", { file_id: fid, asset_id: assetId || null });
      setOut(r);
      const a = assets.find((x) => x.id === assetId);
      const stem = (a?.class_code ?? (r.equipment.model || "PM")).toUpperCase().replace(/[^A-Z0-9]+/g, "-").slice(0, 20);
      setDrafts(r.groups.map((g) => ({ on: true, code: `PM-${stem}-${g.interval_value}${g.interval_unit[0].toUpperCase()}`, title: g.title,
        iv: String(g.interval_value), iu: g.interval_unit, items: g.items.map(toIF), parts: g.spare_parts.map(toPF) })));
    } catch (e) { setErr(errorText(e, t)); } finally { setBusy(null); }
  };
  const create = async () => {
    if (!out) return;
    setBusy("create"); setErr(null);
    let n = 0;
    try {
      for (let i = 0; i < drafts.length; i++) {
        const d = drafts[i];
        if (!d.on) { post(`/api/ai/suggestions/${out.groups[i].suggestion.id}/reject`, {}).catch(() => {}); continue; }
        await post("/api/templates", { kind: "pm", code: d.code, title: d.title, class_id: classId || null,
          method_ref: null, items: d.items.map(fromIF), spare_parts: d.parts.filter((p) => p.name.trim()).map(fromPF),
          suggested_interval_value: num(d.iv), suggested_interval_unit: d.iu, est_minutes: out.groups[i].est_minutes,
          source_file_id: fileId, ai_suggestion_id: out.groups[i].suggestion.id });
        n++;
        setDrafts((x) => x.map((y, j) => (j === i ? { ...y, on: false } : y)));
      }
      toast(t("man.created", { n })); onDone(); onClose();
    } catch (e) { setErr(errorText(e, t)); onDone(); } finally { setBusy(null); }
  };
  const set = (i: number, p: Partial<(typeof drafts)[number]>) => setDrafts((x) => x.map((y, j) => (j === i ? { ...y, ...p } : y)));
  const chosen = drafts.filter((d) => d.on).length;
  return (
    <Sheet wide title={t("man.title")} lead={t("man.lead")} onClose={onClose} footer={<>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      {!out ? <Button variant="primary" icon={<LuSparkles />} busy={busy === "read"} disabled={!file} onClick={read}>{busy === "read" ? t("man.reading") : t("man.read")}</Button>
        : <Button variant="primary" busy={busy === "create"} disabled={!chosen} onClick={create}>{t("man.create", { n: chosen })}</Button>}
    </>}>
      {err && <Notice tone="danger">{err}</Notice>}
      {!out && (
        <div className="stack">
          <div className="field">
            <span className="field-label">{t("man.file")}</span>
            <label className="dropzone cm-drop">
              <LuUpload aria-hidden /><span>{file ? file.name : t("man.file")}</span>
              <input type="file" accept="application/pdf,image/*" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setFileId(null); }} />
            </label>
          </div>
          <div className="grid-2">
            <SelectField label={t("man.asset")} value={assetId} onChange={setAssetId}
              options={[{ value: "", label: "—" }, ...assets.map((a) => ({ value: a.id, label: `${a.code} · ${nameOf(a, lang)}` }))]} />
            <SelectField label={t("tpl.class")} value={classId} onChange={setClassId}
              options={[{ value: "", label: "—" }, ...classes.map((c) => ({ value: c.id, label: `${c.code} · ${nameOf(c, lang)}` }))]} />
          </div>
        </div>
      )}
      {out && (
        <div className="stack">
          <Notice tone="ok">{t(out.mode === "vision" ? "man.foundScan" : "man.found", { n: out.groups.length, pages: out.pages_read })}
            {out.equipment?.model ? ` · ${[out.equipment.manufacturer, out.equipment.model].filter(Boolean).join(" ")}` : ""}</Notice>
          {out.notes && <p className="small muted"><LuSparkles aria-hidden /> {t("man.notes")}: {out.notes}</p>}
          {drafts.map((d, i) => (
            <section key={i} className={`panel cm-man-group ${d.on ? "" : "off"}`}>
              <div className="panel-head">
                <label className="row"><Switch checked={d.on} onChange={(v) => set(i, { on: v })} label={t("man.include")} /><strong>{d.title}</strong></label>
                <span className="spacer" />
                <span className="small muted">{t("man.items", { n: d.items.length })} · {t("man.parts", { n: d.parts.length })}</span>
              </div>
              {d.on && (
                <div className="panel-body stack">
                  <div className="grid-3">
                    <TextField label={t("man.codePrefix")} value={d.code} onChange={(v) => set(i, { code: v.toUpperCase() })} dir="ltr" maxLength={40} />
                    <TextField label={t("tpl.titleField")} value={d.title} onChange={(v) => set(i, { title: v })} maxLength={200} />
                    <div className="field">
                      <label htmlFor={`man-iv-${i}`}>{t("pmtpl.interval")}</label>
                      <div className="row" style={{ gap: 6 }}>
                        <input id={`man-iv-${i}`} className="input" type="number" min={1} style={{ width: 70 }} value={d.iv} onChange={(e) => set(i, { iv: e.target.value })} />
                        <select className="select" aria-label={t("pmtpl.interval")} value={d.iu} onChange={(e) => set(i, { iu: e.target.value })}>
                          {UNITS.map((u) => <option key={u} value={u}>{t(`unit.${u}` as StringKey)}</option>)}
                        </select>
                      </div>
                    </div>
                  </div>
                  <ItemsEditor items={d.items} onChange={(x) => set(i, { items: x })} editable />
                  {(d.parts.length > 0) && <><h4 className="sheet-section">{t("pmtpl.parts")}</h4><PartsEditor parts={d.parts} onChange={(x) => set(i, { parts: x })} editable /></>}
                </div>
              )}
            </section>
          ))}
        </div>
      )}
    </Sheet>
  );
}
