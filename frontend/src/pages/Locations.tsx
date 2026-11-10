import { useState } from "react";
import { LuChevronDown, LuChevronRight, LuMapPin, LuPencil, LuPlus } from "react-icons/lu";
import { locationOptions } from "../components/cm";
import { Badge, Button, Empty, LoadError, Notice, PageHead, SelectField, Sheet, Switch, TextField, errorText, fieldErrors, useLoad, useToast } from "../components/ui";
import { get, patch, post } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link } from "../lib/router";
import { useSession } from "../lib/session";
import type { StringKey } from "../lib/strings";
import type { Line, Location, LocationKind, LocationNode, Tree, TreeSite } from "../lib/types";

const GRADES = ["A", "B", "C", "D", "CNC", "none"];
const CHILD: Record<LocationKind, LocationKind[]> = { building: ["area", "room"], area: ["room"], room: [] };

function LocationSheet({ site, kind, loc, parentId, locations, onClose, onSaved }: {
  site: TreeSite; kind: LocationKind; loc?: Location | null; parentId?: string | null; locations: Location[];
  onClose: () => void; onSaved: () => void;
}) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [f, setF] = useState({
    code: loc?.code ?? "", name_en: loc?.name_en ?? "", name_ar: loc?.name_ar ?? "", parent_id: loc?.parent_id ?? parentId ?? "",
    cleanroom_grade: loc?.cleanroom_grade ?? "", notes: loc?.notes ?? "", active: loc?.active ?? true, reason: "",
  });
  const [busy, setBusy] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [general, setGeneral] = useState<string | null>(null);
  const rank = { building: 1, area: 2, room: 3 };
  const parents = locationOptions(locations.filter((l) => rank[l.kind] < rank[kind]), lang, loc?.id);
  const submit = async () => {
    setBusy(true); setErrs({}); setGeneral(null);
    try {
      const body = { name_en: f.name_en.trim(), name_ar: f.name_ar.trim() || null, parent_id: f.parent_id || null,
        cleanroom_grade: f.cleanroom_grade || null, notes: f.notes.trim() || null };
      if (loc) await patch(`/api/locations/${loc.id}`, { ...body, active: f.active, reason: f.reason });
      else await post(`/api/sites/${site.id}/locations`, { ...body, kind, code: f.code.trim() });
      toast(t("loc.saved")); onSaved(); onClose();
    } catch (e) { setErrs(fieldErrors(e)); setGeneral(errorText(e, t)); setBusy(false); }
  };
  return (
    <Sheet title={loc ? t("loc.editTitle", { code: loc.code }) : t("loc.new", { kind: t(`kind.${kind}` as StringKey) })} lead={`${site.code} · ${site.name}`}
      onClose={onClose} onSubmit={submit}
      footer={<><Button onClick={onClose}>{t("common.cancel")}</Button><Button type="submit" variant="primary" busy={busy}>{loc ? t("common.save") : t("common.create")}</Button></>}>
      {general && <Notice tone="danger">{general}</Notice>}
      {!loc && <TextField label={t("common.code")} value={f.code} dir="ltr" maxLength={30} onChange={(v) => setF({ ...f, code: v.toUpperCase() })} error={errs.code} required />}
      <TextField label={t("af.name_en")} value={f.name_en} onChange={(v) => setF({ ...f, name_en: v })} error={errs.name_en} maxLength={160} required />
      <TextField label={t("af.name_ar")} value={f.name_ar} onChange={(v) => setF({ ...f, name_ar: v })} dir="rtl" lang="ar" maxLength={160} />
      {kind !== "building" && <SelectField label={t("loc.parent")} value={f.parent_id} onChange={(v) => setF({ ...f, parent_id: v })} error={errs.parent_id}
        options={[{ value: "", label: t("loc.siteRoot") }, ...parents]} />}
      <SelectField label={t("loc.grade")} value={f.cleanroom_grade} onChange={(v) => setF({ ...f, cleanroom_grade: v })}
        options={[{ value: "", label: t("form.none") }, ...GRADES.map((g) => ({ value: g, label: g === "none" ? t("grade.none") : g === "CNC" ? t("grade.CNC") : `${t("loc.grade")} ${g}` }))]} />
      <div className="field"><label htmlFor="loc-notes">{t("af.notes")}</label>
        <textarea id="loc-notes" className="textarea" rows={2} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} maxLength={1000} /></div>
      {loc && <>
        <div className="feature" style={{ padding: "6px 0" }}>
          <div className="text"><strong>{t("common.active")}</strong><span>{t("loc.deactivate")}</span></div>
          <Switch label={t("common.active")} checked={f.active} onChange={(v) => setF({ ...f, active: v })} />
        </div>
        <TextField label={t("form.reason")} hint={t("form.reasonHint")} value={f.reason} onChange={(v) => setF({ ...f, reason: v })} error={errs.reason} maxLength={500} />
      </>}
    </Sheet>
  );
}

function LineSheet({ site, line, locations, onClose, onSaved }: { site: TreeSite; line?: Line | null; locations: Location[]; onClose: () => void; onSaved: () => void }) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [f, setF] = useState({ code: line?.code ?? "", name_en: line?.name_en ?? "", name_ar: line?.name_ar ?? "", location_id: line?.location_id ?? "", active: line?.active ?? true, reason: "" });
  const [busy, setBusy] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [general, setGeneral] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true); setErrs({}); setGeneral(null);
    try {
      const body = { name_en: f.name_en.trim(), name_ar: f.name_ar.trim() || null, location_id: f.location_id || null };
      if (line) await patch(`/api/lines/${line.id}`, { ...body, active: f.active, reason: f.reason });
      else await post(`/api/sites/${site.id}/lines`, { ...body, code: f.code.trim() });
      toast(t("loc.saved")); onSaved(); onClose();
    } catch (e) { setErrs(fieldErrors(e)); setGeneral(errorText(e, t)); setBusy(false); }
  };
  return (
    <Sheet title={line ? t("loc.editTitle", { code: line.code }) : t("loc.newLine")} lead={`${site.code} · ${site.name}`} onClose={onClose} onSubmit={submit}
      footer={<><Button onClick={onClose}>{t("common.cancel")}</Button><Button type="submit" variant="primary" busy={busy}>{line ? t("common.save") : t("common.create")}</Button></>}>
      {general && <Notice tone="danger">{general}</Notice>}
      {!line && <TextField label={t("common.code")} value={f.code} dir="ltr" maxLength={30} onChange={(v) => setF({ ...f, code: v.toUpperCase() })} error={errs.code} required />}
      <TextField label={t("af.name_en")} value={f.name_en} onChange={(v) => setF({ ...f, name_en: v })} error={errs.name_en} required />
      <TextField label={t("af.name_ar")} value={f.name_ar} onChange={(v) => setF({ ...f, name_ar: v })} dir="rtl" lang="ar" />
      <SelectField label={t("loc.lineLocation")} value={f.location_id} onChange={(v) => setF({ ...f, location_id: v })}
        options={[{ value: "", label: t("form.none") }, ...locationOptions(locations.filter((l) => l.kind !== "room"), lang)]} />
      {line && <>
        <div className="feature" style={{ padding: "6px 0" }}>
          <div className="text"><strong>{t("common.active")}</strong><span>{t("loc.deactivate")}</span></div>
          <Switch label={t("common.active")} checked={f.active} onChange={(v) => setF({ ...f, active: v })} />
        </div>
        <TextField label={t("form.reason")} hint={t("form.reasonHint")} value={f.reason} onChange={(v) => setF({ ...f, reason: v })} error={errs.reason} />
      </>}
    </Sheet>
  );
}

type Editing = { kind: "loc"; site: TreeSite; locKind: LocationKind; loc?: Location | null; parentId?: string | null }
  | { kind: "line"; site: TreeSite; line?: Line | null };

function NodeRows({ nodes, depth, site, edit }: { nodes: LocationNode[]; depth: number; site: TreeSite; edit: (e: Editing) => void }) {
  const { t, lang, number } = useI18n();
  const [closed, setClosed] = useState<Set<string>>(new Set());
  return (
    <>
      {nodes.map((n) => {
        const isClosed = closed.has(n.id);
        return (
          <div key={n.id}>
            <div className={`cm-tree-row ${n.active ? "" : "inactive"}`} style={{ paddingInlineStart: 12 + depth * 22 }}>
              {n.children.length ? <button type="button" className="icon-btn" aria-expanded={!isClosed} aria-label={n.code}
                onClick={() => setClosed((s) => { const x = new Set(s); if (x.has(n.id)) x.delete(n.id); else x.add(n.id); return x; })}>
                {isClosed ? <LuChevronRight className="flip-rtl" /> : <LuChevronDown />}</button> : <span className="cm-tree-pad" />}
              <span className={`cm-kind k-${n.kind}`}>{t(`kind.${n.kind}` as StringKey)}</span>
              <span className="cm-tree-name"><span className="mono-id">{n.code}</span> {lang === "ar" && n.name_ar ? n.name_ar : n.name_en}</span>
              {n.cleanroom_grade && n.cleanroom_grade !== "none" && <span className="chip small">{n.cleanroom_grade}</span>}
              {!n.active && <Badge>{t("loc.inactive")}</Badge>}
              <span className="spacer" />
              <Link className="small muted" to={`/assets?site_id=${site.id}&location_id=${n.id}`}>{t("loc.assets", { n: number(n.counts.total) })}</Link>
              {site.can_edit && <>
                {CHILD[n.kind].map((k) => (
                  <Button key={k} size="sm" variant="ghost" icon={<LuPlus />} onClick={() => edit({ kind: "loc", site, locKind: k, parentId: n.id })}>
                    {t(`kind.${k}` as StringKey)}</Button>))}
                <Button size="sm" variant="ghost" iconOnly icon={<LuPencil />} aria-label={t("loc.edit")}
                  onClick={() => edit({ kind: "loc", site, locKind: n.kind, loc: n })} />
              </>}
            </div>
            {!isClosed && <NodeRows nodes={n.children} depth={depth + 1} site={site} edit={edit} />}
          </div>
        );
      })}
    </>
  );
}

function Groups({ onChanged }: { onChanged: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const { data, reload } = useLoad(() => get<{ items: { id: string; code: string; name: string; active: boolean; site_count: number }[] }>("/api/site-groups"));
  const sites = useLoad(() => get<{ items: { id: string; code: string; name: string; group_id: string | null }[] }>("/api/sites"));
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <section className="panel">
      <div className="panel-head"><div><h2>{t("loc.groups")}</h2><p className="small muted">{t("loc.groupsLead")}</p></div></div>
      <div className="panel-body stack">
        {err && <Notice tone="danger">{err}</Notice>}
        <form className="row wrap" onSubmit={async (e) => {
          e.preventDefault(); setErr(null);
          try { await post("/api/site-groups", { code, name }); setCode(""); setName(""); toast(t("loc.saved")); reload(); }
          catch (ex) { setErr(errorText(ex, t)); }
        }}>
          <input className="input" style={{ maxWidth: 120 }} placeholder={t("common.code")} aria-label={t("common.code")} dir="ltr" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} />
          <input className="input" style={{ maxWidth: 260 }} placeholder={t("common.name")} aria-label={t("common.name")} value={name} onChange={(e) => setName(e.target.value)} />
          <Button type="submit" size="sm" icon={<LuPlus />} disabled={!code || !name}>{t("loc.addGroup")}</Button>
        </form>
        {(sites.data?.items ?? []).map((s) => (
          <div className="row wrap" key={s.id}>
            <span className="mono-id" style={{ minWidth: 80 }}>{s.code}</span><span className="grow">{s.name}</span>
            <select className="select" style={{ maxWidth: 240 }} aria-label={`${s.code}: ${t("af.group")}`} value={s.group_id ?? ""}
              onChange={async (e) => { await patch(`/api/sites/${s.id}`, { group_id: e.target.value || null }); sites.reload(); onChanged(); toast(t("loc.saved")); }}>
              <option value="">{t("loc.noGroup")}</option>
              {(data?.items ?? []).map((g) => <option key={g.id} value={g.id}>{g.code} · {g.name}</option>)}
            </select>
          </div>
        ))}
      </div>
    </section>
  );
}

export function Locations() {
  const { t, lang } = useI18n();
  const { me } = useSession();
  const tree = useLoad(() => get<Tree>("/api/tree"));
  const [editing, setEditing] = useState<Editing | null>(null);
  const [locs, setLocs] = useState<Location[]>([]);
  const edit = async (e: Editing) => {
    setLocs((await get<{ items: Location[] }>(`/api/sites/${e.site.id}/locations`)).items);
    setEditing(e);
  };
  return (
    <>
      <PageHead title={t("loc.title")} lead={t("loc.lead")} />
      {tree.error ? <div className="panel"><LoadError error={tree.error} onRetry={tree.reload} /></div> : !tree.data ? null : (
        <div className="stack">
          {tree.data.sites.map((s) => (
            <section className="panel" key={s.id}>
              <div className="panel-head">
                <h2><span className="mono-id">{s.code}</span> {s.name}</h2><span className="spacer" />
                {s.can_edit && <Button size="sm" icon={<LuPlus />} onClick={() => edit({ kind: "loc", site: s, locKind: "building" })}>{t("loc.addBuilding")}</Button>}
                {s.can_edit && <Button size="sm" variant="ghost" icon={<LuPlus />} onClick={() => edit({ kind: "loc", site: s, locKind: "room" })}>{t("loc.addRoom")}</Button>}
              </div>
              <div className="cm-tree">
                {s.nodes.length === 0 ? <Empty icon={<LuMapPin />} title={t("loc.empty")} body={t("loc.emptyBody")} />
                  : <NodeRows nodes={s.nodes} depth={0} site={s} edit={edit} />}
              </div>
              <div className="panel-head" style={{ borderTop: "1px solid var(--line)" }}>
                <h3>{t("loc.lines")}</h3><span className="spacer" />
                {s.can_edit && <Button size="sm" icon={<LuPlus />} onClick={() => edit({ kind: "line", site: s })}>{t("loc.addLine")}</Button>}
              </div>
              {s.lines.length === 0 ? <div className="panel-body small muted">{t("loc.noLines")}</div> : (
                <ul className="cm-lines">
                  {s.lines.map((l) => (
                    <li key={l.id}>
                      <span className="mono-id">{l.code}</span> <span className="grow">{lang === "ar" && l.name_ar ? l.name_ar : l.name_en}</span>
                      {!l.active && <Badge>{t("loc.inactive")}</Badge>}
                      <Link className="small muted" to={`/assets?site_id=${s.id}&line_id=${l.id}`}>{t("nav.assets")}</Link>
                      {s.can_edit && <Button size="sm" variant="ghost" iconOnly icon={<LuPencil />} aria-label={t("loc.edit")} onClick={() => edit({ kind: "line", site: s, line: l })} />}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ))}
          {me?.user.is_tenant_admin && <Groups onChanged={tree.reload} />}
        </div>
      )}
      {editing?.kind === "loc" && <LocationSheet site={editing.site} kind={editing.locKind} loc={editing.loc} parentId={editing.parentId}
        locations={locs} onClose={() => setEditing(null)} onSaved={tree.reload} />}
      {editing?.kind === "line" && <LineSheet site={editing.site} line={editing.line} locations={locs} onClose={() => setEditing(null)} onSaved={tree.reload} />}
    </>
  );
}
