import { useState } from "react";
import { LuNetwork, LuPencil, LuPlus, LuTrash2, LuTruck } from "react-icons/lu";
import { CategoryIcon } from "../components/cm";
import {
  Badge, Button, Empty, LoadError, LoadingRows, Notice, PageHead, SelectField, Sheet, Switch, TextField, errorText, fieldErrors, useLoad, useToast,
} from "../components/ui";
import { get, patch, post } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { usePeople } from "../components/work";
import { Link } from "../lib/router";
import { usePerms } from "../lib/session";
import type { StringKey } from "../lib/strings";
import { CATEGORIES, nameOf, type AssetClass, type Category, type Contact, type Department, type Vendor } from "../lib/types";

function useCanEdit() {
  const { admin, has } = usePerms();
  return admin || has("engineering_manager") || has("engineering_supervisor") || has("qa_calibration_officer");
}

function Reason({ value, onChange, error }: { value: string; onChange: (v: string) => void; error?: string }) {
  const { t } = useI18n();
  return <TextField label={t("form.reason")} hint={t("form.reasonHint")} value={value} onChange={onChange} error={error} maxLength={500} />;
}

function ActiveSwitch({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  const { t } = useI18n();
  return (
    <div className="feature" style={{ padding: "6px 0" }}>
      <div className="text"><strong>{t("common.active")}</strong><span>{t("loc.deactivate")}</span></div>
      <Switch label={t("common.active")} checked={value} onChange={onChange} />
    </div>
  );
}

function useSave(onSaved: () => void, onClose: () => void) {
  const { t } = useI18n();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [general, setGeneral] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErrs({}); setGeneral(null);
    try { await fn(); toast(t("list.saved")); onSaved(); onClose(); }
    catch (e) { setErrs(fieldErrors(e)); setGeneral(errorText(e, t)); setBusy(false); }
  };
  return { busy, errs, general, run };
}

// ------------------------------------------------------------------ classes
function ClassSheet({ cls, onClose, onSaved }: { cls: AssetClass | null; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n();
  const [f, setF] = useState({ code: cls?.code ?? "", name_en: cls?.name_en ?? "", name_ar: cls?.name_ar ?? "", category: cls?.category ?? "instrument" as Category,
    calibration_default: cls?.calibration_default ?? false, pm_default: cls?.pm_default ?? false, description: cls?.description ?? "", active: cls?.active ?? true, reason: "" });
  const s = useSave(onSaved, onClose);
  const submit = () => s.run(() => {
    const body = { name_en: f.name_en, name_ar: f.name_ar || null, category: f.category, calibration_default: f.calibration_default,
      pm_default: f.pm_default, description: f.description || null };
    return cls ? patch(`/api/asset-classes/${cls.id}`, { ...body, active: f.active, reason: f.reason })
      : post("/api/asset-classes", { ...body, code: f.code });
  });
  return (
    <Sheet title={cls ? t("cls.edit") : t("cls.new")} lead={cls?.code} onClose={onClose} onSubmit={submit}
      footer={<><Button onClick={onClose}>{t("common.cancel")}</Button><Button type="submit" variant="primary" busy={s.busy}>{cls ? t("common.save") : t("common.create")}</Button></>}>
      {s.general && <Notice tone="danger">{s.general}</Notice>}
      {!cls && <TextField label={t("common.code")} value={f.code} dir="ltr" maxLength={20} onChange={(v) => setF({ ...f, code: v.toUpperCase() })} error={s.errs.code} required />}
      <TextField label={t("af.name_en")} value={f.name_en} onChange={(v) => setF({ ...f, name_en: v })} error={s.errs.name_en} required />
      <TextField label={t("af.name_ar")} value={f.name_ar} onChange={(v) => setF({ ...f, name_ar: v })} dir="rtl" lang="ar" />
      <SelectField label={t("cls.category")} value={f.category} onChange={(v) => setF({ ...f, category: v as Category })}
        options={CATEGORIES.map((c) => ({ value: c, label: t(`cat.${c}` as StringKey) }))} />
      <div className="feature" style={{ padding: "6px 0" }}><div className="text"><strong>{t("cls.calDefault")}</strong></div>
        <Switch label={t("cls.calDefault")} checked={f.calibration_default} onChange={(v) => setF({ ...f, calibration_default: v })} /></div>
      <div className="feature" style={{ padding: "6px 0" }}><div className="text"><strong>{t("cls.pmDefault")}</strong></div>
        <Switch label={t("cls.pmDefault")} checked={f.pm_default} onChange={(v) => setF({ ...f, pm_default: v })} /></div>
      <div className="field"><label htmlFor="cls-d">{t("cls.description")}</label>
        <textarea id="cls-d" className="textarea" rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} maxLength={1000} /></div>
      {cls && <><ActiveSwitch value={f.active} onChange={(v) => setF({ ...f, active: v })} /><Reason value={f.reason} onChange={(v) => setF({ ...f, reason: v })} error={s.errs.reason} /></>}
    </Sheet>
  );
}

export function Classes() {
  const { t, lang, number } = useI18n();
  const canEdit = useCanEdit();
  const list = useLoad(() => get<{ items: AssetClass[] }>("/api/asset-classes"));
  const [editing, setEditing] = useState<AssetClass | "new" | null>(null);
  return (
    <>
      <PageHead title={t("cls.title")} lead={t("cls.lead")}
        actions={canEdit && <Button variant="primary" icon={<LuPlus />} onClick={() => setEditing("new")}>{t("cls.new")}</Button>} />
      <div className="panel">
        {list.error ? <LoadError error={list.error} onRetry={list.reload} /> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>{t("common.code")}</th><th>{t("common.name")}</th><th>{t("cls.category")}</th><th>{t("cls.calDefault")}</th>
              <th>{t("cls.pmDefault")}</th><th className="num">{t("cls.assets")}</th><th /></tr></thead>
            {!list.data ? <LoadingRows rows={8} cols={7} /> : <tbody>{list.data.items.map((c) => (
              <tr key={c.id} className={c.active ? "" : "muted"}>
                <td className="mono-id">{c.code}</td>
                <td className="cell-main">{nameOf(c, lang)}{!c.active && <> <Badge>{t("common.inactive")}</Badge></>}</td>
                <td><span className="row small"><CategoryIcon category={c.category} />{t(`cat.${c.category}` as StringKey)}</span></td>
                <td>{c.calibration_default ? "✓" : ""}</td><td>{c.pm_default ? "✓" : ""}</td>
                <td className="num"><Link to={`/assets?class_id=${c.id}`}>{number(c.asset_count)}</Link></td>
                <td>{canEdit && <Button size="sm" variant="ghost" iconOnly icon={<LuPencil />} aria-label={t("common.edit")} onClick={() => setEditing(c)} />}</td>
              </tr>))}</tbody>}
          </table></div>
        )}
      </div>
      {editing && <ClassSheet cls={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={list.reload} />}
    </>
  );
}

// ------------------------------------------------------------------ departments
function DeptSheet({ dept, onClose, onSaved }: { dept: Department | null; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n();
  const [f, setF] = useState({ code: dept?.code ?? "", name_en: dept?.name_en ?? "", name_ar: dept?.name_ar ?? "", active: dept?.active ?? true, reason: "", head: dept?.head_user_id ?? "" });
  const people = usePeople();
  const s = useSave(onSaved, onClose);
  const submit = () => s.run(() => dept
    ? patch(`/api/departments/${dept.id}`, { name_en: f.name_en, name_ar: f.name_ar || null, active: f.active, reason: f.reason,
        ...((f.head || null) !== (dept.head_user_id ?? null) ? { head_user_id: f.head || null } : {}) })
    : post("/api/departments", { code: f.code, name_en: f.name_en, name_ar: f.name_ar || null }));
  return (
    <Sheet title={dept ? t("dept.edit") : t("dept.new")} lead={dept?.code} onClose={onClose} onSubmit={submit}
      footer={<><Button onClick={onClose}>{t("common.cancel")}</Button><Button type="submit" variant="primary" busy={s.busy}>{dept ? t("common.save") : t("common.create")}</Button></>}>
      {s.general && <Notice tone="danger">{s.general}</Notice>}
      {!dept && <TextField label={t("common.code")} value={f.code} dir="ltr" maxLength={20} onChange={(v) => setF({ ...f, code: v.toUpperCase() })} error={s.errs.code} required />}
      <TextField label={t("af.name_en")} value={f.name_en} onChange={(v) => setF({ ...f, name_en: v })} error={s.errs.name_en} required />
      <TextField label={t("af.name_ar")} value={f.name_ar} onChange={(v) => setF({ ...f, name_ar: v })} dir="rtl" lang="ar" />
      {dept && <SelectField label={t("head.dept")} value={f.head} onChange={(v) => setF({ ...f, head: v })}
        options={[{ value: "", label: "—" }, ...people.map((p) => ({ value: p.id, label: p.full_name }))]} />}
      {dept && <><ActiveSwitch value={f.active} onChange={(v) => setF({ ...f, active: v })} /><Reason value={f.reason} onChange={(v) => setF({ ...f, reason: v })} error={s.errs.reason} /></>}
    </Sheet>
  );
}

export function Departments() {
  const { t, lang, number } = useI18n();
  const canEdit = useCanEdit();
  const list = useLoad(() => get<{ items: Department[] }>("/api/departments"));
  const [editing, setEditing] = useState<Department | "new" | null>(null);
  return (
    <>
      <PageHead title={t("dept.title")} lead={t("dept.lead")}
        actions={canEdit && <Button variant="primary" icon={<LuPlus />} onClick={() => setEditing("new")}>{t("dept.new")}</Button>} />
      <div className="panel">
        {list.error ? <LoadError error={list.error} onRetry={list.reload} /> : list.data && list.data.items.length === 0
          ? <Empty icon={<LuNetwork />} title={t("list.empty")} /> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>{t("common.code")}</th><th>{t("common.name")}</th><th className="num">{t("cls.assets")}</th><th>{t("common.status")}</th><th /></tr></thead>
            {!list.data ? <LoadingRows rows={4} cols={5} /> : <tbody>{list.data.items.map((d) => (
              <tr key={d.id}>
                <td className="mono-id">{d.code}</td><td className="cell-main">{nameOf(d, lang)}</td>
                <td className="num"><Link to={`/assets?department_id=${d.id}`}>{number(d.asset_count)}</Link></td>
                <td>{d.active ? <Badge tone="ok" dot>{t("common.active")}</Badge> : <Badge>{t("common.inactive")}</Badge>}</td>
                <td>{canEdit && <Button size="sm" variant="ghost" iconOnly icon={<LuPencil />} aria-label={t("common.edit")} onClick={() => setEditing(d)} />}</td>
              </tr>))}</tbody>}
          </table></div>
        )}
      </div>
      {editing && <DeptSheet dept={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={list.reload} />}
    </>
  );
}

// ------------------------------------------------------------------ vendors
const KINDS = ["calibration_lab", "service_provider", "manufacturer", "supplier"] as const;

function VendorSheet({ vendor, onClose, onSaved }: { vendor: Vendor | null; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n();
  const v = vendor;
  const [f, setF] = useState({
    code: v?.code ?? "", name: v?.name ?? "", kind: v?.kind ?? "calibration_lab", accreditation_body: v?.accreditation_body ?? "",
    accreditation_no: v?.accreditation_no ?? "", accreditation_expiry: v?.accreditation_expiry ?? "", scope: v?.scope ?? "",
    address: v?.address ?? "", phone: v?.phone ?? "", email: v?.email ?? "", website: v?.website ?? "", notes: v?.notes ?? "",
    active: v?.active ?? true, reason: "",
  });
  const [contacts, setContacts] = useState<Contact[]>(() => (v?.contacts ?? []).filter((c) => c.active));
  const s = useSave(onSaved, onClose);
  const setC = (i: number, k: keyof Contact, val: unknown) => setContacts((cs) => cs.map((c, j) => j === i ? { ...c, [k]: val } : (k === "is_primary" && val ? { ...c, is_primary: false } : c)));
  const submit = () => s.run(() => {
    const n = (x: string) => x.trim() || null;
    const body = { name: f.name, kind: f.kind, accreditation_body: n(f.accreditation_body), accreditation_no: n(f.accreditation_no),
      accreditation_expiry: f.accreditation_expiry || null, scope: n(f.scope), address: n(f.address), phone: n(f.phone), email: n(f.email),
      website: n(f.website), notes: n(f.notes), contacts: contacts.filter((c) => c.name.trim()).map((c) => ({ ...c, position: c.position || null, phone: c.phone || null, email: c.email || null })) };
    return v ? patch(`/api/vendors/${v.id}`, { ...body, active: f.active, reason: f.reason }) : post("/api/vendors", { ...body, code: f.code });
  });
  const tf = (k: keyof typeof f, label: StringKey, extra: Record<string, unknown> = {}) => (
    <TextField label={t(label)} value={String(f[k] ?? "")} onChange={(x) => setF({ ...f, [k]: x })} error={s.errs[k]} {...extra} />
  );
  return (
    <Sheet wide title={v ? t("ven.edit") : t("ven.new")} lead={v?.code} onClose={onClose} onSubmit={submit}
      footer={<><Button onClick={onClose}>{t("common.cancel")}</Button><Button type="submit" variant="primary" busy={s.busy}>{v ? t("common.save") : t("common.create")}</Button></>}>
      {s.general && <Notice tone="danger">{s.general}</Notice>}
      <div className="grid-2">
        {!v && <TextField label={t("common.code")} value={f.code} dir="ltr" maxLength={20} onChange={(x) => setF({ ...f, code: x.toUpperCase() })} error={s.errs.code} required />}
        {tf("name", "common.name", { required: true, maxLength: 160 })}
        <SelectField label={t("ven.kind")} value={f.kind} onChange={(x) => setF({ ...f, kind: x as Vendor["kind"] })}
          options={KINDS.map((k) => ({ value: k, label: t(`ven.kind.${k}` as StringKey) }))} />
        {tf("accreditation_body", "ven.accBody")}
        {tf("accreditation_no", "ven.accNo", { dir: "ltr" })}
        {tf("accreditation_expiry", "ven.accExpiry", { type: "date" })}
        {tf("phone", "ven.phone", { dir: "ltr" })}
        {tf("email", "common.email", { dir: "ltr", type: "email" })}
        {tf("website", "ven.website", { dir: "ltr" })}
        {tf("address", "ven.address")}
      </div>
      <div className="field"><label htmlFor="ven-scope">{t("ven.scope")}</label>
        <textarea id="ven-scope" className="textarea" rows={2} value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })} maxLength={2000} /></div>
      <div className="sheet-section"><h3>{t("ven.contacts")}</h3></div>
      {contacts.map((c, i) => (
        <div className="cm-contact" key={c.id ?? `n${i}`}>
          <input className="input" placeholder={t("ven.contactName")} aria-label={t("ven.contactName")} value={c.name} onChange={(e) => setC(i, "name", e.target.value)} />
          <input className="input" placeholder={t("ven.position")} aria-label={t("ven.position")} value={c.position ?? ""} onChange={(e) => setC(i, "position", e.target.value)} />
          <input className="input" placeholder={t("ven.phone")} aria-label={t("ven.phone")} dir="ltr" value={c.phone ?? ""} onChange={(e) => setC(i, "phone", e.target.value)} />
          <input className="input" placeholder={t("common.email")} aria-label={t("common.email")} dir="ltr" value={c.email ?? ""} onChange={(e) => setC(i, "email", e.target.value)} />
          <label className="row small"><input type="radio" name="primary" checked={c.is_primary} onChange={() => setC(i, "is_primary", true)} />{t("ven.primary")}</label>
          <Button size="sm" variant="ghost" iconOnly icon={<LuTrash2 />} aria-label={t("ven.removeContact")} onClick={() => setContacts((cs) => cs.filter((_, j) => j !== i))} />
        </div>
      ))}
      <div><Button size="sm" icon={<LuPlus />} onClick={() => setContacts((cs) => [...cs, { name: "", position: "", phone: "", email: "", is_primary: cs.length === 0, active: true }])}>{t("ven.addContact")}</Button></div>
      {s.errs.contacts && <div className="error">{s.errs.contacts}</div>}
      {v && <><ActiveSwitch value={f.active} onChange={(x) => setF({ ...f, active: x })} /><Reason value={f.reason} onChange={(x) => setF({ ...f, reason: x })} error={s.errs.reason} /></>}
    </Sheet>
  );
}

export function Vendors() {
  const { t, date, number } = useI18n();
  const canEdit = useCanEdit();
  const list = useLoad(() => get<{ items: Vendor[] }>("/api/vendors"));
  const [editing, setEditing] = useState<Vendor | "new" | null>(null);
  return (
    <>
      <PageHead title={t("ven.title")} lead={t("ven.lead")}
        actions={canEdit && <Button variant="primary" icon={<LuPlus />} onClick={() => setEditing("new")}>{t("ven.new")}</Button>} />
      <div className="panel">
        {list.error ? <LoadError error={list.error} onRetry={list.reload} /> : list.data && list.data.items.length === 0
          ? <Empty icon={<LuTruck />} title={t("list.empty")} /> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>{t("common.name")}</th><th>{t("ven.kind")}</th><th>{t("ven.accBody")}</th><th>{t("ven.primaryContact")}</th><th className="num">{t("cls.assets")}</th><th /></tr></thead>
            {!list.data ? <LoadingRows rows={4} cols={6} /> : <tbody>{list.data.items.map((v) => {
              const p = v.contacts.find((c) => c.is_primary && c.active) ?? v.contacts.find((c) => c.active);
              return (
                <tr key={v.id}>
                  <td><div className="cell-main">{v.name}</div><div className="small subtle mono-id">{v.code}</div></td>
                  <td className="small">{t(`ven.kind.${v.kind}` as StringKey)}</td>
                  <td className="small">{v.accreditation_body ? <>{v.accreditation_body} {v.accreditation_no}<br />
                    {v.accreditation_expired ? <Badge tone="danger" dot>{t("ven.expired")}</Badge>
                      : v.accreditation_expiry && <span className="subtle">{t("ven.validUntil", { date: date(v.accreditation_expiry) })}</span>}</> : ""}</td>
                  <td className="small">{p ? <>{p.name}{p.position ? ` · ${p.position}` : ""}<br /><span className="subtle" dir="ltr">{[p.phone, p.email].filter(Boolean).join(" · ")}</span></> : ""}</td>
                  <td className="num">{number(v.asset_count)}</td>
                  <td>{canEdit && <Button size="sm" variant="ghost" iconOnly icon={<LuPencil />} aria-label={t("common.edit")} onClick={() => setEditing(v)} />}</td>
                </tr>
              );
            })}</tbody>}
          </table></div>
        )}
      </div>
      {editing && <VendorSheet vendor={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={list.reload} />}
    </>
  );
}
