import { useEffect, useMemo, useRef, useState } from "react";
import { LuCamera, LuSparkles, LuX } from "react-icons/lu";
import { locationOptions, useLookups } from "../components/cm";
import { Button, Notice, SelectField, Sheet, Switch, TextField, errorText, fieldErrors, useToast } from "../components/ui";
import { patch, post, upload } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import type { StringKey } from "../lib/strings";
import { CRITICALITY, type Asset, type Criticality } from "../lib/types";

type Form = {
  site_id: string; code: string; name_en: string; name_ar: string; class_id: string; parent_id: string; location_id: string;
  current_location_id: string; line_id: string; department_id: string; manufacturer: string; model: string; serial_no: string;
  asset_tag: string; capacity: string; range_min: string; range_max: string; unit: string; resolution: string; accuracy: string;
  gmp_criticality: Criticality; calibration_required: boolean | null; pm_required: boolean | null; is_portable: boolean;
  install_date: string; warranty_until: string; supplier_vendor_id: string; service_vendor_id: string; external_ref: string;
  notes: string; nameplate_file_id: string; photo_file_id: string;
};

const TEXT_FIELDS = ["name_ar", "manufacturer", "model", "serial_no", "asset_tag", "capacity", "unit", "resolution", "accuracy",
  "external_ref", "notes"] as const;
const REFS = ["parent_id", "location_id", "current_location_id", "line_id", "department_id", "supplier_vendor_id",
  "service_vendor_id", "nameplate_file_id", "photo_file_id"] as const;

function fromAsset(a: Asset | null, siteId: string | null, parent?: Asset | null): Form {
  const s = (v: unknown) => (v === null || v === undefined ? "" : String(v));
  return {
    site_id: a?.site_id ?? parent?.site_id ?? siteId ?? "", code: a?.code ?? (parent ? `${parent.code}-` : ""), name_en: s(a?.name_en),
    name_ar: s(a?.name_ar), class_id: s(a?.class_id), parent_id: s(a?.parent_id ?? parent?.id), location_id: s(a?.location_id ?? parent?.location_id),
    current_location_id: s(a?.current_location_id), line_id: s(a?.line_id ?? parent?.line_id), department_id: s(a?.department_id ?? parent?.department_id),
    manufacturer: s(a?.manufacturer), model: s(a?.model), serial_no: s(a?.serial_no), asset_tag: s(a?.asset_tag), capacity: s(a?.capacity),
    range_min: s(a?.range_min), range_max: s(a?.range_max), unit: s(a?.unit), resolution: s(a?.resolution), accuracy: s(a?.accuracy),
    gmp_criticality: a?.gmp_criticality ?? parent?.gmp_criticality ?? "major", calibration_required: a ? a.calibration_required : null,
    pm_required: a ? a.pm_required : null, is_portable: a?.is_portable ?? false, install_date: s(a?.install_date),
    warranty_until: s(a?.warranty_until), supplier_vendor_id: s(a?.supplier_vendor_id), service_vendor_id: s(a?.service_vendor_id),
    external_ref: s(a?.external_ref), notes: s(a?.notes), nameplate_file_id: s(a?.nameplate_file_id), photo_file_id: s(a?.photo_file_id),
  };
}

interface NameplateResult {
  suggestion: { id: string; model: string };
  fields: Record<string, string | number>; confidence: Record<string, string>; notes: string; class_id?: string;
}

export function AssetForm({ asset, parent, siteId, onClose, onSaved }: {
  asset?: Asset | null; parent?: Asset | null; siteId: string | null; onClose: () => void; onSaved: (a: Asset) => void;
}) {
  const { t, lang } = useI18n();
  const { me } = useSession();
  const toast = useToast();
  const [f, setF] = useState<Form>(() => fromAsset(asset ?? null, siteId, parent));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [general, setGeneral] = useState<string | null>(null);
  const [np, setNp] = useState<NameplateResult | null>(null);
  const [npBusy, setNpBusy] = useState(false);
  const [npErr, setNpErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const look = useLookups(f.site_id || null);
  const editing = !!asset;
  const set = <K extends keyof Form>(k: K) => (v: Form[K]) => setF((x) => ({ ...x, [k]: v }));
  const ai = me?.tenant?.ai;
  const cls = look.classes.find((c) => c.id === f.class_id);
  const calOn = f.calibration_required ?? cls?.calibration_default ?? false;
  const pmOn = f.pm_required ?? cls?.pm_default ?? false;
  useEffect(() => { if (!editing && !f.site_id && me?.sites[0]) set("site_id")(me.sites[0].id); }, [me]); // eslint-disable-line react-hooks/exhaustive-deps

  const locOpts = useMemo(() => locationOptions(look.locations, lang), [look.locations, lang]);
  const nm = (x: { name_en: string; name_ar?: string | null }) => (lang === "ar" && x.name_ar ? x.name_ar : x.name_en);
  const none = { value: "", label: t("form.none") };

  const readNameplate = async (file: File) => {
    setNpBusy(true); setNpErr(null);
    try {
      const up = await upload<{ id: string }>(file, file.name || "nameplate.jpg", "nameplate");
      const r = await post<NameplateResult>("/api/ai/nameplate", { file_id: up.id, hint: cls?.name_en ?? null });
      setNp(r);
      const v = r.fields;
      setF((x) => ({
        ...x, nameplate_file_id: up.id,
        name_en: x.name_en || String(v.name_en ?? ""), name_ar: x.name_ar || String(v.name_ar ?? ""),
        manufacturer: String(v.manufacturer ?? x.manufacturer), model: String(v.model ?? x.model), serial_no: String(v.serial_no ?? x.serial_no),
        capacity: String(v.capacity ?? x.capacity), range_min: v.range_min !== undefined ? String(v.range_min) : x.range_min,
        range_max: v.range_max !== undefined ? String(v.range_max) : x.range_max, unit: String(v.unit ?? x.unit),
        resolution: String(v.resolution ?? x.resolution), accuracy: String(v.accuracy ?? x.accuracy),
        class_id: x.class_id || r.class_id || "",
      }));
    } catch (e) { setNpErr(errorText(e, t)); }
    finally { setNpBusy(false); }
  };

  const aiMark = (k: string) => np && np.fields[k] !== undefined
    ? <span className={`cm-ai-mark ${np.confidence[k] === "low" ? "low" : ""}`} title={np.confidence[k] === "low" ? t("np.low") : undefined}>
        <LuSparkles aria-hidden />{t("np.aiBadge")}</span> : null;
  const label = (k: StringKey, field?: string) => <>{t(k)} {field && aiMark(field)}</>;

  const submit = async () => {
    setBusy(true); setErrs({}); setGeneral(null);
    const body: Record<string, unknown> = {
      name_en: f.name_en.trim(), gmp_criticality: f.gmp_criticality, is_portable: f.is_portable,
      calibration_required: calOn, pm_required: pmOn,
      range_min: f.range_min === "" ? null : Number(f.range_min), range_max: f.range_max === "" ? null : Number(f.range_max),
      install_date: f.install_date || null, warranty_until: f.warranty_until || null,
    };
    TEXT_FIELDS.forEach((k) => { body[k] = f[k].trim() || null; });
    REFS.forEach((k) => { body[k] = f[k] || null; });
    if (f.class_id) body.class_id = f.class_id;
    try {
      let saved: Asset;
      if (editing) {
        saved = await patch<Asset>(`/api/assets/${asset!.id}`, { ...body, reason });
        toast(t("form.saved"));
      } else {
        saved = await post<Asset>("/api/assets", { ...body, site_id: f.site_id, code: f.code.trim(), ai_suggestion_id: np?.suggestion.id ?? null });
        toast(t("form.created"));
      }
      onSaved(saved); onClose();
    } catch (e) { setErrs(fieldErrors(e)); setGeneral(errorText(e, t)); }
    finally { setBusy(false); }
  };

  const title = editing ? t("form.editAsset", { code: asset!.code }) : parent ? t("form.newComponent", { code: parent.code }) : t("form.newAsset");
  const sites = me?.sites.filter((s) => me.user.is_tenant_admin || s.roles.some((r) => ["engineering_manager", "engineering_supervisor", "qa_calibration_officer"].includes(r))) ?? [];
  return (
    <Sheet wide title={title} onClose={onClose} onSubmit={submit}
      footer={<><Button onClick={onClose}>{t("common.cancel")}</Button>
        <Button type="submit" variant="primary" busy={busy} disabled={!f.name_en.trim() || !f.class_id || (!editing && (!f.code.trim() || !f.site_id)) || (editing && me?.tenant?.features.reason_for_change !== false && reason.trim().length < 3)}>
          {editing ? t("common.save") : t("common.create")}</Button></>}>
      {general && <Notice tone="danger">{general}</Notice>}
      {!editing && ai?.configured && ai.nameplate && (
        <div className="cm-np">
          <div className="text"><strong><LuSparkles aria-hidden /> {t("np.title")}</strong><span className="small muted">{t("np.body")}</span></div>
          <input ref={fileRef} type="file" accept="image/*,application/pdf" capture="environment" hidden
            onChange={(e) => { const file = e.target.files?.[0]; if (file) readNameplate(file); e.target.value = ""; }} />
          <Button icon={<LuCamera />} busy={npBusy} onClick={() => fileRef.current?.click()}>{npBusy ? t("np.reading") : t("np.take")}</Button>
          {npErr && <Notice tone="danger">{npErr}</Notice>}
          {np && <Notice tone="ok">
            <div className="stack" style={{ gap: 6 }}>
              <span>{t("np.done", { n: Object.keys(np.fields).length })}</span>
              {np.notes && <span className="small"><strong>{t("np.notes")}:</strong> {np.notes}</span>}
              <div><Button size="sm" variant="ghost" icon={<LuX />} onClick={() => { setNp(null); setF(fromAsset(null, f.site_id)); }}>{t("np.discard")}</Button></div>
            </div>
          </Notice>}
        </div>
      )}
      <div className="sheet-section"><h3>{t("form.sec.identity")}</h3></div>
      <div className="grid-2">
        {!editing && sites.length > 1 && !parent
          ? <SelectField label={t("af.site")} value={f.site_id} onChange={(v) => setF((x) => ({ ...x, site_id: v, location_id: "", line_id: "", parent_id: "", current_location_id: "" }))}
              options={sites.map((s) => ({ value: s.id, label: `${s.code} · ${s.name}` }))} error={errs.site_id} />
          : null}
        <TextField label={t("af.code")} hint={editing ? undefined : t("af.codeHint")} value={f.code} dir="ltr" disabled={editing}
          onChange={(v) => set("code")(v.toUpperCase())} error={errs.code} maxLength={40} required />
        <SelectField label={t("af.class")} value={f.class_id} onChange={(v) => setF((x) => ({ ...x, class_id: v, calibration_required: editing ? x.calibration_required : null, pm_required: editing ? x.pm_required : null }))}
          error={errs.class_id} options={[{ value: "", label: t("sign.choose") }, ...look.classes.filter((c) => c.active || c.id === f.class_id)
            .map((c) => ({ value: c.id, label: `${c.code} · ${nm(c)}` }))]} />
        <TextField label={label("af.name_en", "name_en")} value={f.name_en} onChange={set("name_en")} error={errs.name_en} maxLength={200} required />
        <TextField label={label("af.name_ar", "name_ar")} value={f.name_ar} onChange={set("name_ar")} dir="rtl" lang="ar" maxLength={200} />
      </div>
      <div className="sheet-section"><h3>{t("form.sec.place")}</h3></div>
      <div className="grid-2">
        <SelectField label={t("af.parent")} hint={t("af.parentHint")} value={f.parent_id} onChange={set("parent_id")} error={errs.parent_id}
          options={[none, ...look.assets.filter((a) => a.id !== asset?.id).map((a) => ({ value: a.id, label: `${a.code} · ${lang === "ar" && a.name_ar ? a.name_ar : a.name_en}` }))]} />
        <SelectField label={t("af.location")} value={f.location_id} onChange={set("location_id")} error={errs.location_id} options={[none, ...locOpts]} />
        <SelectField label={t("af.line")} value={f.line_id} onChange={set("line_id")} error={errs.line_id}
          options={[none, ...look.lines.map((l) => ({ value: l.id, label: `${l.code} · ${nm(l)}` }))]} />
        <SelectField label={t("af.department")} value={f.department_id} onChange={set("department_id")}
          options={[none, ...look.departments.filter((d) => d.active).map((d) => ({ value: d.id, label: `${d.code} · ${nm(d)}` }))]} />
        <div className="feature" style={{ padding: "6px 0" }}>
          <div className="text"><strong>{t("af.is_portable")}</strong><span>{t("af.is_portableHint")}</span></div>
          <Switch label={t("af.is_portable")} checked={f.is_portable} onChange={set("is_portable")} />
        </div>
        {f.is_portable && <SelectField label={t("af.current_location")} hint={t("af.current_locationHint")} value={f.current_location_id}
          onChange={set("current_location_id")} options={[none, ...locOpts]} />}
      </div>
      <div className="sheet-section"><h3>{t("form.sec.tech")}</h3></div>
      <div className="grid-2">
        <TextField label={label("af.manufacturer", "manufacturer")} value={f.manufacturer} onChange={set("manufacturer")} maxLength={120} />
        <TextField label={label("af.model", "model")} value={f.model} onChange={set("model")} maxLength={120} />
        <TextField label={label("af.serial_no", "serial_no")} value={f.serial_no} onChange={set("serial_no")} dir="ltr" maxLength={120} />
        <TextField label={t("af.asset_tag")} value={f.asset_tag} onChange={set("asset_tag")} dir="ltr" maxLength={60} />
        <TextField label={label("af.capacity", "capacity")} value={f.capacity} onChange={set("capacity")} maxLength={120} />
        <div className="grid-3" style={{ gap: 8 }}>
          <TextField label={label("af.range_min", "range_min")} type="number" inputMode="decimal" value={f.range_min} onChange={set("range_min")} dir="ltr" />
          <TextField label={label("af.range_max", "range_max")} type="number" inputMode="decimal" value={f.range_max} onChange={set("range_max")} dir="ltr" error={errs.range_max} />
          <TextField label={label("af.unit", "unit")} value={f.unit} onChange={set("unit")} dir="ltr" maxLength={30} />
        </div>
        <TextField label={label("af.resolution", "resolution")} value={f.resolution} onChange={set("resolution")} dir="ltr" maxLength={60} />
        <TextField label={label("af.accuracy", "accuracy")} value={f.accuracy} onChange={set("accuracy")} dir="ltr" maxLength={60} />
      </div>
      <div className="sheet-section"><h3>{t("form.sec.gmp")}</h3></div>
      <div className="grid-2">
        <SelectField label={t("af.gmp_criticality")} value={f.gmp_criticality} onChange={(v) => set("gmp_criticality")(v as Criticality)}
          options={CRITICALITY.map((c) => ({ value: c, label: t(`crit.${c}` as StringKey) }))} />
        <div />
        <div className="feature" style={{ padding: "6px 0" }}>
          <div className="text"><strong>{t("af.calibration_required")}</strong><span>{t("af.calibration_requiredHint")}</span></div>
          <Switch label={t("af.calibration_required")} checked={calOn} onChange={set("calibration_required")} />
        </div>
        <div className="feature" style={{ padding: "6px 0" }}>
          <div className="text"><strong>{t("af.pm_required")}</strong><span>{t("af.pm_requiredHint")}</span></div>
          <Switch label={t("af.pm_required")} checked={pmOn} onChange={set("pm_required")} />
        </div>
        <TextField label={t("af.install_date")} type="date" value={f.install_date} onChange={set("install_date")} />
        <TextField label={t("af.warranty_until")} type="date" value={f.warranty_until} onChange={set("warranty_until")} />
      </div>
      <div className="sheet-section"><h3>{t("form.sec.vendors")}</h3></div>
      <div className="grid-2">
        <SelectField label={t("af.service_vendor")} value={f.service_vendor_id} onChange={set("service_vendor_id")}
          options={[none, ...look.vendors.filter((v) => v.active || v.id === f.service_vendor_id).map((v) => ({ value: v.id, label: `${v.code} · ${v.name}` }))]} />
        <SelectField label={t("af.supplier")} value={f.supplier_vendor_id} onChange={set("supplier_vendor_id")}
          options={[none, ...look.vendors.filter((v) => v.active || v.id === f.supplier_vendor_id).map((v) => ({ value: v.id, label: `${v.code} · ${v.name}` }))]} />
        <TextField label={t("af.external_ref")} hint={t("af.external_refHint")} value={f.external_ref} onChange={set("external_ref")} dir="ltr" maxLength={60} />
      </div>
      <div className="field">
        <label htmlFor="af-notes">{t("af.notes")}</label>
        <textarea id="af-notes" className="textarea" rows={3} value={f.notes} onChange={(e) => set("notes")(e.target.value)} maxLength={4000} />
      </div>
      {editing && (
        <TextField label={t("form.reason")} hint={t("form.reasonHint")} value={reason} onChange={setReason} error={errs.reason} maxLength={500} required />
      )}
    </Sheet>
  );
}
