import { useEffect, useMemo, useState } from "react";
import { LuBoxes, LuCalendarClock, LuDownload, LuFileSpreadsheet, LuPlus, LuPrinter, LuSignature, LuWrench, LuX } from "react-icons/lu";
import { CategoryIcon, Programmes, SignStatus, StatusBadge } from "../components/cm";
import { Button, Empty, LoadError, LoadingRows, Menu, MenuItem, PageHead, useLoad } from "../components/ui";
import { get, qs } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link, navigate } from "../lib/router";
import { usePerms, useSession } from "../lib/session";
import type { StringKey } from "../lib/strings";
import { CATEGORIES, STATUSES, nameOf, type AssetRow } from "../lib/types";
import { AssetForm } from "./AssetForm";
import { SchedulePlanner, useDay } from "../components/work";

const FILTER_KEYS = ["site_id", "location_id", "category", "status", "q", "calibration", "pm", "top", "include_retired",
  "class_id", "department_id", "line_id", "unplaced", "page"] as const;
type Filters = Partial<Record<(typeof FILTER_KEYS)[number], string>>;

function readFilters(): Filters {
  const p = new URLSearchParams(window.location.search);
  const f: Filters = {};
  FILTER_KEYS.forEach((k) => { const v = p.get(k); if (v) f[k] = v; });
  return f;
}

export function Assets() {
  const { t, lang, number } = useI18n();
  const { me, siteId } = useSession();
  const { admin, has } = usePerms();
  const [f, setF] = useState<Filters>(readFilters);
  const [q, setQ] = useState(f.q ?? "");
  const [sel, setSel] = useState<Map<string, AssetRow>>(new Map());
  const [signing, setSigning] = useState(false);
  const [planning, setPlanning] = useState<"calibration" | "pm" | null>(null);
  const day = useDay();
  const [creating, setCreating] = useState(() => new URLSearchParams(window.location.search).get("new") === "1");
  const list = useLoad(() => get<{ items: AssetRow[]; total: number; page: number; page_size: number }>(`/api/assets${qs({ ...f, page_size: 100 })}`), [JSON.stringify(f)]);
  useEffect(() => { navigate(`/assets${qs(f)}`, true); }, [JSON.stringify(f)]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const h = setTimeout(() => setF((x) => (x.q ?? "") === q ? x : { ...x, q: q || undefined, page: undefined }), 300);
    return () => clearTimeout(h);
  }, [q]);
  const set = (k: keyof Filters, v: string | undefined) => setF((x) => ({ ...x, [k]: v || undefined, page: undefined }));
  const canEdit = admin || has("engineering_manager") || has("engineering_supervisor") || has("qa_calibration_officer");
  const items = list.data?.items ?? [];
  const page = list.data?.page ?? 1;
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / list.data.page_size)) : 1;
  const allSel = items.length > 0 && items.every((a) => sel.has(a.id));
  const toggle = (a: AssetRow) => setSel((s) => { const x = new Map(s); if (x.has(a.id)) x.delete(a.id); else x.set(a.id, a); return x; });
  const selIds = useMemo(() => Array.from(sel.keys()).join(","), [sel]);
  const labels = (extra: string) => window.open(`/api/assets/labels.pdf?ids=${selIds}${extra}`, "_blank", "noopener");
  const sites = me?.sites ?? [];
  const locName = (a: AssetRow) => a.location_code ? `${a.location_code}` : "";
  return (
    <>
      <PageHead title={t("assets.title")} lead={t("assets.lead")} actions={<>
        {canEdit && <Button icon={<LuFileSpreadsheet />} onClick={() => navigate("/import")}>{t("assets.import")}</Button>}
        <Button icon={<LuDownload />} onClick={() => { window.location.href = `/api/assets/export.xlsx${qs(f)}`; }}>{t("assets.export")}</Button>
        {canEdit && <Button variant="primary" icon={<LuPlus />} onClick={() => setCreating(true)}>{t("assets.new")}</Button>}
      </>} />
      <div className="panel">
        <div className="toolbar cm-filters">
          <input className="input" type="search" placeholder={t("assets.searchPh")} aria-label={t("common.search")} value={q} onChange={(e) => setQ(e.target.value)} />
          {sites.length > 1 && (
            <select className="select" aria-label={t("assets.filter.site")} value={f.site_id ?? ""} onChange={(e) => setF({ ...f, site_id: e.target.value || undefined, location_id: undefined, page: undefined })}>
              <option value="">{t("shell.allSites")}</option>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}
            </select>
          )}
          <select className="select" aria-label={t("assets.filter.category")} value={f.category ?? ""} onChange={(e) => set("category", e.target.value)}>
            <option value="">{t("assets.filter.category")}: {t("common.all")}</option>
            {CATEGORIES.map((c) => <option key={c} value={c}>{t(`cat.${c}` as StringKey)}</option>)}
          </select>
          <select className="select" aria-label={t("assets.filter.status")} value={f.status ?? (f.include_retired ? "*" : "")}
            onChange={(e) => setF({ ...f, status: e.target.value && e.target.value !== "*" ? e.target.value : undefined, include_retired: e.target.value === "*" ? "1" : undefined, page: undefined })}>
            <option value="">{t("assets.filter.current")}</option>
            <option value="*">{t("assets.filter.retired")}</option>
            {STATUSES.map((s) => <option key={s} value={s}>{t(`status.${s}` as StringKey)}</option>)}
          </select>
          <label className="row small"><input type="checkbox" checked={f.calibration === "1"} onChange={(e) => set("calibration", e.target.checked ? "1" : undefined)} />{t("assets.filter.cal")}</label>
          <label className="row small"><input type="checkbox" checked={f.pm === "1"} onChange={(e) => set("pm", e.target.checked ? "1" : undefined)} />{t("assets.filter.pm")}</label>
          <label className="row small"><input type="checkbox" checked={f.top === "1"} onChange={(e) => set("top", e.target.checked ? "1" : undefined)} />{t("assets.filter.top")}</label>
          {(f.location_id || f.unplaced || f.class_id || f.department_id || f.line_id) && (
            <Button size="sm" variant="ghost" icon={<LuX />} onClick={() => setF({ site_id: f.site_id })}>{t("audit.clear")}</Button>
          )}
          <span className="spacer" />
          {list.data && <span className="small muted">{t("assets.count", { n: number(list.data.total) })}</span>}
        </div>
        {sel.size > 0 && (
          <div className="cm-selbar">
            <strong>{t("assets.selected", { n: sel.size })}</strong>
            <Button size="sm" variant="primary" icon={<LuSignature />} onClick={() => setSigning(true)}>{t("assets.changeStatus")}</Button>
            {canEdit && <Button size="sm" icon={<LuCalendarClock />} onClick={() => setPlanning("calibration")}>{t("cw.plan")}</Button>}
            {canEdit && <Button size="sm" icon={<LuWrench />} onClick={() => setPlanning("pm")}>{t("pmw.plan")}</Button>}
            <Menu trigger={(p) => <Button size="sm" icon={<LuPrinter />} {...p}>{t("assets.labels")}</Button>}>
              {(close) => <>
                <MenuItem onClick={() => { close(); labels("&layout=a4&size=large"); }}>{t("assets.labelsA4")}</MenuItem>
                <MenuItem onClick={() => { close(); labels("&layout=a4&size=small"); }}>{t("assets.labelsSmall")}</MenuItem>
                <MenuItem onClick={() => { close(); labels("&layout=roll&size=large"); }}>{t("assets.labelsRoll")}</MenuItem>
              </>}
            </Menu>
            <Button size="sm" variant="ghost" onClick={() => setSel(new Map())}>{t("assets.clearSel")}</Button>
          </div>
        )}
        {list.error ? <LoadError error={list.error} onRetry={list.reload} /> : list.data && items.length === 0 ? (
          <Empty icon={<LuBoxes />} title={t("assets.empty")} body={t("assets.emptyBody")} />
        ) : (
          <div className="table-wrap">
            <table className="table cm-assets">
              <thead><tr>
                <th className="cell-tight"><input type="checkbox" aria-label={t("assets.selectAll")} checked={allSel}
                  onChange={() => setSel((s) => { const x = new Map(s); items.forEach((a) => allSel ? x.delete(a.id) : x.set(a.id, a)); return x; })} /></th>
                <th>{t("assets.col.code")}</th><th>{t("assets.col.name")}</th><th>{t("assets.col.class")}</th>
                <th>{t("assets.col.location")}</th><th>{t("assets.col.prog")}</th><th>{t("assets.col.nextCal")}</th><th>{t("assets.col.status")}</th>
              </tr></thead>
              {!list.data ? <LoadingRows rows={8} cols={8} /> : (
                <tbody>
                  {items.map((a) => (
                    <tr key={a.id} className={sel.has(a.id) ? "selected" : ""}>
                      <td className="cell-tight"><input type="checkbox" aria-label={t("assets.select", { code: a.code })} checked={sel.has(a.id)} onChange={() => toggle(a)} /></td>
                      <td className="nowrap"><Link to={`/assets/${a.id}`} className="mono-id cell-main">{a.code}</Link></td>
                      <td>
                        <div className="cell-main">{nameOf(a, lang)}</div>
                        <div className="small subtle">
                          {[a.manufacturer, a.model].filter(Boolean).join(" · ")}
                          {a.parent_code && <> · {t("assets.partOf", { code: a.parent_code })}</>}
                          {a.child_count > 0 && <> · {t("assets.components", { n: a.child_count })}</>}
                        </div>
                      </td>
                      <td><span className="row nowrap small"><CategoryIcon category={a.category} />{lang === "ar" && a.class_name_ar ? a.class_name_ar : a.class_name_en}</span></td>
                      <td className="small nowrap">{a.site_code}{locName(a) ? ` / ${locName(a)}` : ""}{a.line_code ? ` · ${a.line_code}` : ""}</td>
                      <td><Programmes cal={a.calibration_required} pm={a.pm_required} /></td>
                      <td className={`nowrap small num ${a.cal_overdue ? "text-danger" : ""}`}>{a.cal_due ? day(a.cal_due) : a.calibration_required ? <span className="subtle">—</span> : ""}</td>
                      <td><StatusBadge status={a.status} /></td>
                    </tr>
                  ))}
                </tbody>
              )}
            </table>
          </div>
        )}
        {pages > 1 && (
          <div className="pager">
            <Button size="sm" disabled={page <= 1} onClick={() => setF({ ...f, page: String(page - 1) })}>{t("common.previous")}</Button>
            <span className="small muted">{t("common.pageOf", { page, pages })}</span>
            <Button size="sm" disabled={page >= pages} onClick={() => setF({ ...f, page: String(page + 1) })}>{t("common.next")}</Button>
          </div>
        )}
      </div>
      {signing && <SignStatus assets={Array.from(sel.values())} onClose={() => setSigning(false)} onDone={() => { setSel(new Map()); list.reload(); }} />}
      {planning && <SchedulePlanner kind={planning} assets={Array.from(sel.values())} onClose={() => setPlanning(null)} onDone={() => { setSel(new Map()); list.reload(); }} />}
      {creating && <AssetForm siteId={f.site_id ?? siteId} onClose={() => { setCreating(false); }}
        onSaved={(a) => navigate(`/assets/${a.id}`)} />}
    </>
  );
}
