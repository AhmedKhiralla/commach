// The PM checklist as the technician works it on a phone or tablet: OK / not OK / N/A with remarks, readings with
// limits, values and photos; and the spare parts used.
import { useRef, useState } from "react";
import { LuCamera, LuCircleCheck, LuCircleX, LuPlus, LuTrash2 } from "react-icons/lu";
import { Button, Notice, errorText } from "../components/ui";
import { upload } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { InOut, inLimits, type Row } from "./TaskPage";

export interface Part { name: string; part_no: string; qty: string; unit: string; notes: string }

function answered(r: Row) {
  if (r.item_type === "check") return ["ok", "not_ok", "na"].includes(r.value_text);
  if (r.item_type === "reading") return r.as_found.trim() !== "";
  if (r.item_type === "photo") return !!r.file_id;
  return r.value_text.trim() !== "";
}

function Photo({ row, edit, onFile }: { row: Row; edit: boolean; onFile: (id: string) => void }) {
  const { t } = useI18n();
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="cm-pm-photo">
      {row.file_id && <img src={`/api/files/${row.file_id}/content?inline=1`} alt={row.parameter} />}
      {edit && <Button size="sm" icon={<LuCamera />} busy={busy} onClick={() => ref.current?.click()}>{row.file_id ? t("pmt.photoReplace") : t("pmt.photo")}</Button>}
      <input ref={ref} type="file" hidden accept="image/*" capture="environment" onChange={async (e) => {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (!f) return;
        setBusy(true); setErr(null);
        try { onFile((await upload<{ id: string }>(f, f.name, "photo")).id); } catch (x) { setErr(errorText(x, t)); } finally { setBusy(false); }
      }} />
      {err && <Notice tone="danger">{err}</Notice>}
    </div>
  );
}

export function PmChecklist({ rows, edit, template, onChange }: {
  rows: Row[]; edit: boolean; template: { code: string; version: number; title: string; method_ref: string | null } | null;
  onChange: (rows: Row[]) => void;
}) {
  const { t, qty } = useI18n();
  const set = (i: number, patch: Partial<Row>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const done = rows.filter(answered).length;
  const [remarks, setRemarks] = useState<Set<number>>(new Set());
  return (
    <section className="panel">
      <div className="panel-head">
        <h3>{t("pmt.checklist")}</h3>
        {template && <span className="small muted">{template.code} v{template.version}{template.method_ref ? ` · ${template.method_ref}` : ""}</span>}
        <span className="spacer" />
        <span className="small muted">{t("pmt.progress", { done, total: rows.length })}</span>
      </div>
      <div className="cm-pm-progress" aria-hidden><span style={{ width: `${rows.length ? (done / rows.length) * 100 : 0}%` }} /></div>
      <ol className="cm-pm-list">
        {rows.map((r, i) => {
          const ok = r.item_type === "reading" ? inLimits(r.as_found, r.low_limit, r.high_limit) : null;
          const bad = (r.item_type === "check" && r.value_text === "not_ok") || ok === false;
          return (
            <li key={i} className={`${bad ? "bad" : ""} ${answered(r) ? "done" : ""}`}>
              <div className="cm-pm-q">
                <span className="cm-pm-n num">{i + 1}</span>
                <div className="grow">
                  <div className="cell-main">{r.parameter}{!r.required && <span className="small subtle"> · {t("pmt.optional")}</span>}</div>
                  <div className="small subtle">
                    {r.page_ref && <span className="cm-page">{t("pmt.page", { page: r.page_ref })}</span>}
                    {r.item_notes && <span> {r.item_notes}</span>}
                    {r.item_type === "reading" && (r.low_limit || r.high_limit) && (
                      <span className="num"> {r.low_limit ? qty(Number(r.low_limit), 6) : "–∞"} … {r.high_limit ? qty(Number(r.high_limit), 6) : "∞"} {r.unit}</span>
                    )}
                  </div>
                </div>
              </div>
              <div className="cm-pm-a">
                {r.item_type === "check" && (
                  <div className="cm-pm-seg" role="radiogroup" aria-label={r.parameter}>
                    {([["ok", t("pmt.ok"), <LuCircleCheck key="i" aria-hidden />], ["not_ok", t("pmt.notOk"), <LuCircleX key="i" aria-hidden />], ["na", t("pmt.na"), null]] as const).map(([v, label, icon]) => (
                      <button key={v} type="button" role="radio" aria-checked={r.value_text === v} disabled={!edit} className={`v-${v}`}
                        onClick={() => set(i, { value_text: r.value_text === v ? "" : v })}>{icon}{label}</button>
                    ))}
                  </div>
                )}
                {r.item_type === "reading" && (
                  <div className="row" style={{ gap: 8 }}>
                    <input className="input cm-pm-num" inputMode="decimal" dir="ltr" aria-label={r.parameter} disabled={!edit}
                      value={r.as_found} onChange={(e) => set(i, { as_found: e.target.value })} />
                    <span className="small muted">{r.unit}</span>
                    <InOut ok={ok} />
                  </div>
                )}
                {r.item_type === "text" && (
                  <input className="input" aria-label={r.parameter} disabled={!edit} value={r.value_text} maxLength={2000}
                    placeholder={r.unit ? `${t("pmt.value")} (${r.unit})` : t("pmt.value")} onChange={(e) => set(i, { value_text: e.target.value })} />
                )}
                {r.item_type === "photo" && <Photo row={r} edit={edit} onFile={(id) => set(i, { file_id: id })} />}
                {edit && !bad && !r.notes && !remarks.has(i) && (
                  <button type="button" className="link-btn small" onClick={() => setRemarks((x) => new Set(x).add(i))}>+ {t("pmt.remark")}</button>
                )}
                {(bad || r.notes || remarks.has(i)) && (
                  <input className={`input cm-pm-remark ${bad && !r.notes ? "need" : ""}`} aria-label={`${t("pmt.remark")} ${i + 1}`} disabled={!edit}
                    value={r.notes} maxLength={500} placeholder={t("pmt.remarkPh")} onChange={(e) => set(i, { notes: e.target.value })} />
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function PmParts({ parts, edit, suggested, onChange }: {
  parts: Part[]; edit: boolean; suggested: { name: string; part_no: string | null; qty: number | null; unit: string | null }[];
  onChange: (p: Part[]) => void;
}) {
  const { t } = useI18n();
  const set = (i: number, patch: Partial<Part>) => onChange(parts.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  const unused = suggested.filter((sp) => !parts.some((p) => p.name === sp.name));
  if (!edit && parts.length === 0) return null;
  return (
    <section className="panel">
      <div className="panel-head"><h3>{t("pmt.parts")}</h3><span className="spacer" />
        {edit && <Button size="sm" icon={<LuPlus />} onClick={() => onChange([...parts, { name: "", part_no: "", qty: "1", unit: "", notes: "" }])}>{t("pmt.addPart")}</Button>}
      </div>
      {edit && unused.length > 0 && (
        <div className="panel-body chip-row">
          {unused.map((sp) => (
            <button key={sp.name} type="button" className="chip" onClick={() => onChange([...parts, { name: sp.name, part_no: sp.part_no ?? "",
              qty: String(sp.qty ?? 1), unit: sp.unit ?? "", notes: "" }])}><LuPlus aria-hidden /> {sp.name}{sp.part_no ? ` · ${sp.part_no}` : ""}</button>
          ))}
        </div>
      )}
      {parts.length > 0 && (
        <div className="table-wrap">
          <table className="table edit-table">
            <thead><tr><th>{t("pmt.partName")}</th><th>{t("pmt.partNo")}</th><th className="num">{t("pmt.qty")}</th><th>{t("pmt.unit")}</th>{edit && <th />}</tr></thead>
            <tbody>
              {parts.map((p, i) => (
                <tr key={i}>
                  <td>{edit ? <input className="input cell-input wide" aria-label={t("pmt.partName")} value={p.name} onChange={(e) => set(i, { name: e.target.value })} /> : p.name}</td>
                  <td>{edit ? <input className="input cell-input" style={{ width: 130 }} aria-label={t("pmt.partNo")} value={p.part_no} onChange={(e) => set(i, { part_no: e.target.value })} />
                    : <span className="mono-id">{p.part_no}</span>}</td>
                  <td className="num">{edit ? <input className="input cell-input" inputMode="decimal" dir="ltr" aria-label={t("pmt.qty")} value={p.qty} onChange={(e) => set(i, { qty: e.target.value })} /> : p.qty}</td>
                  <td>{edit ? <input className="input cell-input narrow" aria-label={t("pmt.unit")} value={p.unit} onChange={(e) => set(i, { unit: e.target.value })} /> : p.unit}</td>
                  {edit && <td><Button size="sm" variant="ghost" iconOnly aria-label={t("common.delete")} icon={<LuTrash2 />} onClick={() => onChange(parts.filter((_, j) => j !== i))} /></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
