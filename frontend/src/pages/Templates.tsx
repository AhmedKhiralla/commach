// Calibration templates: points, tolerances and method per instrument type, approved by e-signature and
// changed only through a new version.
import { useEffect, useState } from "react";
import { LuCopyPlus, LuListChecks, LuPlus, LuSignature, LuTrash2 } from "react-icons/lu";
import { Badge, Button, Empty, LoadError, LoadingRows, Notice, PageHead, SelectField, Sheet, TextField, errorText, fieldErrors, useLoad, useToast } from "../components/ui";
import { SignDialog } from "../components/work";
import { get, patch, post } from "../lib/api";
import { useI18n } from "../lib/i18n";
import type { StringKey } from "../lib/strings";
import { nameOf, type AssetClass } from "../lib/types";

interface TemplateRow {
  id: string; code: string; version: number; title: string; title_ar: string | null; status: string; class_id: string | null;
  class_code: string | null; class_name_en: string | null; class_name_ar: string | null; method_ref: string | null;
  item_count: number; schedule_count: number; approved_at: string | null; approved_by_name: string | null; source: string;
}
interface Item {
  id?: string; seq?: number; item_type: string; parameter: string; nominal: number | null; unit: string | null;
  tol_mode: "abs" | "pct" | "limits" | null; tol_minus: number | null; tol_plus: number | null; low_limit: number | null;
  high_limit: number | null; required: boolean; notes: string | null; limit_low?: number | null; limit_high?: number | null;
}
interface Template extends TemplateRow {
  description: string | null; standards_note: string | null; environment_note: string | null; items: Item[];
  versions: { id: string; version: number; status: string; approved_at: string | null }[];
  can_edit: boolean; can_approve: boolean;
}

const TONE: Record<string, "ok" | "warn" | undefined> = { approved: "ok", draft: "warn" };

type ItemForm = { [K in "parameter" | "nominal" | "unit" | "tol_minus" | "tol_plus" | "low_limit" | "high_limit" | "notes"]: string } & { tol_mode: "abs" | "pct" | "limits" };
const str = (v: unknown) => (v === null || v === undefined ? "" : String(v));
const num = (v: string) => (v.trim() === "" ? null : Number(v.replace(",", ".")));

function toForm(it: Item): ItemForm {
  return { parameter: it.parameter, nominal: str(it.nominal), unit: str(it.unit), tol_mode: it.tol_mode ?? "abs", tol_minus: str(it.tol_minus),
    tol_plus: str(it.tol_plus), low_limit: str(it.low_limit), high_limit: str(it.high_limit), notes: str(it.notes) };
}

function limitsOf(f: ItemForm): [number | null, number | null] {
  const nom = num(f.nominal);
  if (f.tol_mode === "limits") return [num(f.low_limit), num(f.high_limit)];
  if (nom === null) return [null, null];
  let mi = num(f.tol_minus) ?? num(f.tol_plus) ?? 0, pl = num(f.tol_plus) ?? num(f.tol_minus) ?? 0;
  if (f.tol_mode === "pct") { mi = Math.abs(nom) * mi / 100; pl = Math.abs(nom) * pl / 100; }
  return [nom - mi, nom + pl];
}

export function TemplateEditor({ id, onClose, onChanged }: { id: string | "new"; onClose: () => void; onChanged: (t?: Template) => void }) {
  const { t, lang, qty, dateTime } = useI18n();
  const toast = useToast();
  const [tpl, setTpl] = useState<Template | null>(null);
  const [classes, setClasses] = useState<AssetClass[]>([]);
  const [f, setF] = useState({ code: "", title: "", title_ar: "", class_id: "", method_ref: "", description: "", standards_note: "", environment_note: "" });
  const [items, setItems] = useState<ItemForm[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [signing, setSigning] = useState(false);
  const [curId, setCurId] = useState(id);

  const fill = (x: Template) => {
    setTpl(x);
    setF({ code: x.code, title: x.title, title_ar: str(x.title_ar), class_id: str(x.class_id), method_ref: str(x.method_ref),
      description: str(x.description), standards_note: str(x.standards_note), environment_note: str(x.environment_note) });
    setItems(x.items.map(toForm));
  };
  useEffect(() => {
    get<{ items: AssetClass[] }>("/api/asset-classes").then((r) => setClasses(r.items)).catch(() => {});
    if (curId !== "new") get<Template>(`/api/templates/${curId}`).then(fill).catch((e) => setErr(errorText(e, t)));
    else setItems([{ parameter: "", nominal: "", unit: "", tol_mode: "abs", tol_minus: "", tol_plus: "", low_limit: "", high_limit: "", notes: "" }]);
  }, [curId]); // eslint-disable-line react-hooks/exhaustive-deps

  const editable = curId === "new" || !!tpl?.can_edit;
  const setI = (i: number, k: keyof ItemForm, v: string) => setItems((x) => x.map((y, j) => (j === i ? { ...y, [k]: v } : y)));
  const save = async () => {
    setBusy(true); setErr(null); setErrs({});
    const body = {
      title: f.title, title_ar: f.title_ar || null, class_id: f.class_id || null, method_ref: f.method_ref || null,
      description: f.description || null, standards_note: f.standards_note || null, environment_note: f.environment_note || null,
      items: items.map((it) => ({ item_type: "point", parameter: it.parameter.trim() || "?", nominal: num(it.nominal), unit: it.unit || null,
        tol_mode: it.tol_mode, tol_minus: it.tol_mode === "limits" ? null : num(it.tol_minus), tol_plus: it.tol_mode === "limits" ? null : num(it.tol_plus),
        low_limit: it.tol_mode === "limits" ? num(it.low_limit) : null, high_limit: it.tol_mode === "limits" ? num(it.high_limit) : null,
        required: true, notes: it.notes || null })),
    };
    try {
      const out = curId === "new" ? await post<Template>("/api/templates", { ...body, kind: "calibration", code: f.code })
        : await patch<Template>(`/api/templates/${curId}`, body);
      fill(out); setCurId(out.id); onChanged(out);
      toast(curId === "new" ? t("tpl.created") : t("list.saved"));
    } catch (e) { setErr(errorText(e, t)); setErrs(fieldErrors(e)); } finally { setBusy(false); }
  };
  const newVersion = async () => {
    setBusy(true); setErr(null);
    try { const out = await post<Template>(`/api/templates/${curId}/new-version`); fill(out); setCurId(out.id); onChanged(out); }
    catch (e) { setErr(errorText(e, t)); } finally { setBusy(false); }
  };
  const status = tpl?.status ?? "draft";
  const title = curId === "new" ? t("tpl.new") : `${tpl?.code ?? ""} ${tpl ? t("tpl.version", { n: tpl.version }) : ""}`;
  return (
    <>
      <Sheet wide title={title} lead={tpl ? <Badge tone={TONE[status]}>{t(`tpl.status.${status}` as StringKey)}</Badge> : undefined}
        onClose={onClose} onSubmit={editable ? save : undefined} footer={<>
          <Button onClick={onClose}>{t("common.close")}</Button>
          {tpl && !editable && (status === "approved" || status === "superseded") && <Button icon={<LuCopyPlus />} busy={busy} onClick={newVersion}>{t("tpl.newVersion")}</Button>}
          {tpl?.can_approve && <Button icon={<LuSignature />} disabled={busy} onClick={() => setSigning(true)}>{t("tpl.approve")}</Button>}
          {editable && <Button type="submit" variant="primary" busy={busy}>{t("common.save")}</Button>}
        </>}>
        {err && <Notice tone="danger">{err}</Notice>}
        {tpl && !editable && <Notice>{t("tpl.readOnly", { status: t(`tpl.status.${status}` as StringKey) })}</Notice>}
        {tpl?.approved_by_name && <p className="small muted"><LuSignature aria-hidden /> {t("tpl.approvedBy", { name: tpl.approved_by_name, date: dateTime(tpl.approved_at) })}</p>}
        <div className="grid-2">
          <TextField label={t("tpl.code")} value={f.code} onChange={(v) => setF({ ...f, code: v.toUpperCase() })} disabled={curId !== "new"} error={errs.code} maxLength={40} dir="ltr" />
          <SelectField label={t("tpl.class")} value={f.class_id} onChange={(v) => setF({ ...f, class_id: v })} disabled={!editable}
            options={[{ value: "", label: "—" }, ...classes.filter((c) => c.calibration_default || c.id === f.class_id).map((c) => ({ value: c.id, label: `${c.code} · ${nameOf(c, lang)}` }))]} />
          <TextField label={t("tpl.titleField")} value={f.title} onChange={(v) => setF({ ...f, title: v })} disabled={!editable} error={errs.title} maxLength={200} />
          <TextField label={t("tpl.title_ar")} value={f.title_ar} onChange={(v) => setF({ ...f, title_ar: v })} disabled={!editable} maxLength={200} dir="rtl" lang="ar" />
          <TextField label={t("tpl.method")} value={f.method_ref} onChange={(v) => setF({ ...f, method_ref: v })} disabled={!editable} maxLength={120} />
          <TextField label={t("tpl.environment")} value={f.environment_note} onChange={(v) => setF({ ...f, environment_note: v })} disabled={!editable} maxLength={500} />
        </div>
        <TextField label={t("tpl.standards")} value={f.standards_note} onChange={(v) => setF({ ...f, standards_note: v })} disabled={!editable} maxLength={1000} />
        <h3 className="sheet-section">{t("tpl.items")}</h3>
        <div className="table-wrap">
          <table className="table edit-table cm-tpl-items">
            <thead><tr><th>#</th><th>{t("tpl.parameter")}</th><th>{t("task.r.nominal")}</th><th>{t("tpl.mode")}</th><th />
              <th className="num">{t("task.r.limits")}</th>{editable && <th />}</tr></thead>
            <tbody>
              {items.map((it, i) => {
                const [lo, hi] = limitsOf(it);
                const e = (k: string) => errs[`items.${i}.${k}`];
                return (
                  <tr key={i}>
                    <td className="subtle num">{i + 1}</td>
                    <td><input className={`input cell-input wide ${e("parameter") ? "bad" : ""}`} aria-label={t("tpl.parameter")} value={it.parameter} disabled={!editable}
                      onChange={(ev) => setI(i, "parameter", ev.target.value)} /></td>
                    <td><span className="row" style={{ gap: 4 }}>
                      <input className={`input cell-input ${e("nominal") ? "bad" : ""}`} inputMode="decimal" dir="ltr" aria-label={t("task.r.nominal")} value={it.nominal} disabled={!editable}
                        onChange={(ev) => setI(i, "nominal", ev.target.value)} />
                      <input className="input cell-input narrow" aria-label="unit" placeholder="unit" value={it.unit} disabled={!editable} onChange={(ev) => setI(i, "unit", ev.target.value)} />
                    </span></td>
                    <td><select className="select cell-input" aria-label={t("tpl.mode")} value={it.tol_mode} disabled={!editable}
                      onChange={(ev) => setI(i, "tol_mode", ev.target.value)}>
                      {(["abs", "pct", "limits"] as const).map((m) => <option key={m} value={m}>{t(`tpl.mode.${m}` as StringKey)}</option>)}
                    </select></td>
                    <td><span className="row" style={{ gap: 4 }}>
                      {it.tol_mode === "limits" ? <>
                        <input className={`input cell-input ${e("low_limit") ? "bad" : ""}`} inputMode="decimal" dir="ltr" aria-label={t("task.r.low")} placeholder={t("task.r.low")} value={it.low_limit} disabled={!editable} onChange={(ev) => setI(i, "low_limit", ev.target.value)} />
                        <input className={`input cell-input ${e("high_limit") ? "bad" : ""}`} inputMode="decimal" dir="ltr" aria-label={t("task.r.high")} placeholder={t("task.r.high")} value={it.high_limit} disabled={!editable} onChange={(ev) => setI(i, "high_limit", ev.target.value)} />
                      </> : <>
                        <span className="subtle">{t("tpl.minus")}</span>
                        <input className="input cell-input" inputMode="decimal" dir="ltr" aria-label={`${t("tpl.mode")} ${t("tpl.minus")}`} value={it.tol_minus} disabled={!editable} onChange={(ev) => setI(i, "tol_minus", ev.target.value)} />
                        <span className="subtle">{t("tpl.plus")}</span>
                        <input className={`input cell-input ${e("tol_plus") ? "bad" : ""}`} inputMode="decimal" dir="ltr" aria-label={`${t("tpl.mode")} ${t("tpl.plus")}`} value={it.tol_plus} disabled={!editable} onChange={(ev) => setI(i, "tol_plus", ev.target.value)} />
                        {it.tol_mode === "pct" && <span className="subtle">%</span>}
                      </>}
                    </span></td>
                    <td className="num nowrap small">{lo !== null || hi !== null ? `${lo !== null ? qty(lo, 6) : "–∞"} … ${hi !== null ? qty(hi, 6) : "∞"} ${it.unit}` : "–"}</td>
                    {editable && <td><Button size="sm" variant="ghost" iconOnly aria-label={t("common.delete")} icon={<LuTrash2 />} onClick={() => setItems((x) => x.filter((_, j) => j !== i))} /></td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {editable && <div><Button size="sm" icon={<LuPlus />} onClick={() => setItems((x) => [...x, { ...(x.at(-1) ?? toForm({ item_type: "point", parameter: "", nominal: null, unit: null, tol_mode: "abs", tol_minus: null, tol_plus: null, low_limit: null, high_limit: null, required: true, notes: null })), nominal: "", low_limit: "", high_limit: "" }])}>{t("tpl.addPoint")}</Button></div>}
        {tpl && tpl.versions.length > 1 && (
          <>
            <h3 className="sheet-section">{t("tpl.versions")}</h3>
            <div className="chip-row">
              {tpl.versions.map((v) => (
                <button key={v.id} type="button" className="chip" aria-pressed={v.id === tpl.id} onClick={() => setCurId(v.id)}>
                  {t("tpl.version", { n: v.version })} · {t(`tpl.status.${v.status}` as StringKey)}
                </button>
              ))}
            </div>
          </>
        )}
      </Sheet>
      {signing && tpl && (
        <SignDialog title={`${t("tpl.approve")} · ${tpl.code} ${t("tpl.version", { n: tpl.version })}`} meaning="template_approved" onClose={() => setSigning(false)}
          onSign={async (v) => { const out = await post<Template>(`/api/templates/${tpl.id}/approve`, { ...v, reason: v.reason || null }); fill(out); onChanged(out); }} />
      )}
    </>
  );
}

export function Templates() {
  const { t, lang, date } = useI18n();
  const list = useLoad(() => get<{ items: TemplateRow[]; can_edit: boolean }>("/api/templates?kind=calibration&all=1"));
  const [open, setOpen] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const items = (list.data?.items ?? []).filter((x) => all || x.status === "approved" || x.status === "draft");
  return (
    <>
      <PageHead title={t("tpl.title")} lead={t("tpl.lead")} actions={list.data?.can_edit &&
        <Button variant="primary" icon={<LuPlus />} onClick={() => setOpen("new")}>{t("tpl.new")}</Button>} />
      <div className="panel">
        <div className="toolbar">
          <label className="row small"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />{t("tpl.showOld")}</label>
          <span className="spacer" />
          {list.data && <span className="small muted">{items.length}</span>}
        </div>
        {list.error ? <LoadError error={list.error} onRetry={list.reload} /> : list.data && items.length === 0 ? (
          <Empty icon={<LuListChecks />} title={t("tpl.empty")} body={t("tpl.emptyBody")} />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>{t("tpl.code")}</th><th>{t("tpl.titleField")}</th><th>{t("tpl.class")}</th><th>{t("tpl.items")}</th><th>{t("cw.col.state")}</th></tr></thead>
              {!list.data ? <LoadingRows rows={6} cols={5} /> : (
                <tbody>
                  {items.map((x) => (
                    <tr key={x.id} className="clickable" onClick={() => setOpen(x.id)}>
                      <td className="nowrap"><button type="button" className="link-btn mono-id cell-main" onClick={(e) => { e.stopPropagation(); setOpen(x.id); }}>{x.code}</button>
                        <span className="small subtle"> {t("tpl.version", { n: x.version })}</span></td>
                      <td><div className="cell-main">{lang === "ar" && x.title_ar ? x.title_ar : x.title}</div>
                        {x.method_ref && <div className="small subtle">{x.method_ref}</div>}</td>
                      <td className="small">{x.class_code ? `${x.class_code} · ${lang === "ar" && x.class_name_ar ? x.class_name_ar : x.class_name_en}` : "—"}</td>
                      <td className="small nowrap">{t("tpl.points", { n: x.item_count })} · {t("tpl.inUse", { n: x.schedule_count })}</td>
                      <td><Badge tone={TONE[x.status]}>{t(`tpl.status.${x.status}` as StringKey)}</Badge>
                        {x.approved_at && <div className="small subtle">{date(x.approved_at)}</div>}</td>
                    </tr>
                  ))}
                </tbody>
              )}
            </table>
          </div>
        )}
      </div>
      {open && <TemplateEditor id={open} onClose={() => setOpen(null)} onChanged={() => list.reload()} />}
    </>
  );
}
