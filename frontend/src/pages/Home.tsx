import { useMemo, useState } from "react";
import { LuBuilding2, LuChevronDown, LuChevronRight, LuFactory, LuFileSpreadsheet, LuPlus, LuSparkles } from "react-icons/lu";
import { CategoryIcon, Kpi } from "../components/cm";
import { Button, Dialog, Empty, LoadError, Notice, errorText, useLoad, useToast } from "../components/ui";
import { get, post } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link, navigate } from "../lib/router";
import { useSession } from "../lib/session";
import type { StringKey } from "../lib/strings";
import { CATEGORIES, type Counts, type LocationNode, type Tree, type TreeSite } from "../lib/types";

function firstName(name: string) {
  return name.trim().split(/\s+/)[0] ?? name;
}

function PlatformHome() {
  const { t, number } = useI18n();
  const { data, error, reload } = useLoad(() => get<{ items: { status: string; user_count: number; site_count: number }[] }>("/api/platform/tenants"));
  const items = data?.items ?? [];
  return (
    <section className="panel" aria-labelledby="co-h">
      <div className="panel-head">
        <h2 id="co-h">{t("home.companies")}</h2><span className="spacer" />
        <Button size="sm" variant="primary" icon={<LuPlus />} onClick={() => navigate("/companies?new=1")}>{t("home.newCompany")}</Button>
      </div>
      {error ? <LoadError error={error} onRetry={reload} /> : (
        <div className="stat-line">
          <div><strong>{data ? number(items.filter((x) => x.status === "active").length) : "–"}</strong><span>{t("home.activeCompanies")}</span></div>
          <div><strong>{data ? number(items.reduce((a, x) => a + x.site_count, 0)) : "–"}</strong><span>{t("home.totalSites")}</span></div>
          <div><strong>{data ? number(items.reduce((a, x) => a + x.user_count, 0)) : "–"}</strong><span>{t("home.totalUsers")}</span></div>
        </div>
      )}
      <div className="panel-body" style={{ borderTop: "1px solid var(--line)" }}>
        <Link to="/companies" className="row"><LuBuilding2 aria-hidden />{t("companies.title")}</Link>
      </div>
    </section>
  );
}

type View = "all" | "cal" | "pm";

function shown(c: Counts, view: View) {
  return view === "cal" ? c.calibration : view === "pm" ? c.pm : c.total;
}

function NodeRow({ node, depth, view, open, toggle, siteId }: {
  node: LocationNode; depth: number; view: View; open: Set<string>; toggle: (id: string) => void; siteId: string;
}) {
  const { t, lang, number } = useI18n();
  const n = shown(node.counts, view);
  if (view !== "all" && n === 0) return null;
  const hasKids = node.children.some((c) => view === "all" || shown(c.counts, view) > 0);
  const isOpen = open.has(node.id);
  const q = `/assets?site_id=${siteId}&location_id=${node.id}${view === "cal" ? "&calibration=1" : view === "pm" ? "&pm=1" : ""}`;
  return (
    <>
      <div className="cm-tree-row" style={{ paddingInlineStart: 12 + depth * 22 }}>
        {hasKids
          ? <button type="button" className="icon-btn" aria-expanded={isOpen} onClick={() => toggle(node.id)}
              aria-label={node.code}>{isOpen ? <LuChevronDown /> : <LuChevronRight className="flip-rtl" />}</button>
          : <span className="cm-tree-pad" />}
        <span className={`cm-kind k-${node.kind}`}>{t(`kind.${node.kind}` as StringKey)}</span>
        <Link to={q} className="cm-tree-name"><span className="mono-id">{node.code}</span> {lang === "ar" && node.name_ar ? node.name_ar : node.name_en}</Link>
        {node.cleanroom_grade && node.cleanroom_grade !== "none" && <span className="chip small">{node.cleanroom_grade}</span>}
        <span className="spacer" />
        <CountCells c={node.counts} view={view} />
        <strong className="cm-tree-total num">{number(n)}</strong>
      </div>
      {isOpen && node.children.map((c) => <NodeRow key={c.id} node={c} depth={depth + 1} view={view} open={open} toggle={toggle} siteId={siteId} />)}
    </>
  );
}

function CountCells({ c, view }: { c: Counts; view: View }) {
  const { t, number } = useI18n();
  return (
    <span className="cm-tree-cells small">
      {view !== "pm" && <span title={t("ov.calibrated")}><b>{number(c.calibration)}</b> {t("assets.cal")}</span>}
      {view !== "cal" && <span title={t("ov.maintained")}><b>{number(c.pm)}</b> {t("assets.pm")}</span>}
      {c.unavailable > 0 && <span className="text-danger" title={t("ov.unavailable")}>● {number(c.unavailable)}</span>}
      {c.draft > 0 && <span className="subtle" title={t("ov.drafts")}>○ {number(c.draft)}</span>}
    </span>
  );
}

function SiteCard({ site, view, groupName }: { site: TreeSite; view: View; groupName?: string }) {
  const { t, number } = useI18n();
  const all = useMemo(() => {
    const ids: string[] = [];
    const walk = (ns: LocationNode[]) => ns.forEach((n) => { if (n.children.length) ids.push(n.id); walk(n.children); });
    walk(site.nodes);
    return ids;
  }, [site]);
  const [open, setOpen] = useState<Set<string>>(() => new Set(site.nodes.map((n) => n.id)));
  const toggle = (id: string) => setOpen((s) => { const x = new Set(s); if (x.has(id)) x.delete(id); else x.add(id); return x; });
  const c = site.counts;
  return (
    <section className="panel cm-site">
      <div className="panel-head">
        <div>
          <h2><span className="mono-id">{site.code}</span> {site.name}</h2>
          {groupName && <span className="small muted">{groupName}</span>}
        </div>
        <span className="spacer" />
        <Button size="sm" variant="ghost" onClick={() => setOpen(open.size >= all.length ? new Set() : new Set(all))}>
          {open.size >= all.length ? t("ov.collapse") : t("ov.expand")}</Button>
        <Link className="btn sm" to={`/assets?site_id=${site.id}${view === "cal" ? "&calibration=1" : view === "pm" ? "&pm=1" : ""}`}>{t("ov.openList")}</Link>
      </div>
      <div className="cm-kpis">
        <Kpi label={view === "cal" ? t("ov.calibrated") : view === "pm" ? t("ov.maintained") : t("ov.total")} value={number(shown(c, view))} tone="brand" />
        {view === "all" && <Kpi label={t("ov.calibrated")} value={number(c.calibration)} />}
        {view === "all" && <Kpi label={t("ov.maintained")} value={number(c.pm)} />}
        <Kpi label={t("ov.critical")} value={number(c.critical)} />
        <Kpi label={t("ov.unavailable")} value={number(c.unavailable)} tone={c.unavailable ? "danger" : undefined} />
        <Kpi label={t("ov.drafts")} value={number(c.draft)} tone={c.draft ? "warn" : undefined} />
        <Kpi label={t("ov.nextDue")} value={<span className="small">{t("ov.nextDueSoon")}</span>} hint={t("ov.dueHint")} />
      </div>
      <div className="cm-cats">
        {CATEGORIES.filter((k) => c.categories[k]).map((k) => (
          <Link key={k} to={`/assets?site_id=${site.id}&category=${k}`} className="cm-cat-pill">
            <CategoryIcon category={k} /><span>{t(`cat.${k}` as StringKey)}</span><b>{number(c.categories[k])}</b>
          </Link>
        ))}
      </div>
      <div className="cm-tree" role="tree">
        {site.nodes.length === 0 ? <div className="panel-body small muted">{t("ov.noLocations")}</div>
          : site.nodes.map((n) => <NodeRow key={n.id} node={n} depth={0} view={view} open={open} toggle={toggle} siteId={site.id} />)}
        {site.unplaced > 0 && (
          <div className="cm-tree-row small" style={{ paddingInlineStart: 12 }}>
            <span className="cm-tree-pad" />
            <Link to={`/assets?site_id=${site.id}&unplaced=1`} className="muted">{t("ov.unplaced", { n: number(site.unplaced) })}</Link>
          </div>
        )}
      </div>
    </section>
  );
}

function DemoDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { me, refresh } = useSession();
  const toast = useToast();
  const [roles, setRoles] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Dialog title={t("demo.title")} onClose={onClose} footer={<>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      <Button variant="primary" busy={busy} icon={<LuSparkles />} onClick={async () => {
        setBusy(true); setErr(null);
        try {
          const r = await post<{ assets: number }>("/api/sample-data", { give_me_roles: roles });
          toast(t("demo.done", { n: r.assets })); await refresh(); onDone(); onClose();
        } catch (e) { setErr(errorText(e, t)); setBusy(false); }
      }}>{t("demo.load")}</Button>
    </>}>
      <p>{t("demo.body", { company: me?.tenant?.name ?? "" })}</p>
      <label className="row small" style={{ alignItems: "flex-start", gap: 8 }}>
        <input type="checkbox" checked={roles} onChange={(e) => setRoles(e.target.checked)} />
        <span>{t("demo.roles")}</span>
      </label>
      {err && <Notice tone="danger">{err}</Notice>}
    </Dialog>
  );
}

export function Overview() {
  const { t } = useI18n();
  const { me } = useSession();
  const { data, error, reload } = useLoad(() => get<Tree>("/api/tree"));
  const [view, setView] = useState<View>(() => {
    try { const v = localStorage.getItem("cm.view"); if (v === "cal" || v === "pm" || v === "all") return v; } catch { /* ignore */ }
    // People who work only on calibration (or only on maintenance) open on their own work.
    const roles = new Set(me?.sites.flatMap((s) => s.roles));
    const broad = me?.user.is_tenant_admin || roles.has("engineering_manager") || roles.has("qa_approver") || roles.has("engineering_supervisor");
    if (!broad && roles.has("qa_calibration_officer") && !roles.has("maintenance_technician")) return "cal";
    if (!broad && roles.has("maintenance_technician") && !roles.has("qa_calibration_officer")) return "pm";
    return "all";
  });
  const [demo, setDemo] = useState(() => new URLSearchParams(window.location.search).get("demo") === "1" && !!me?.user.is_tenant_admin);
  const choose = (v: View) => { setView(v); try { localStorage.setItem("cm.view", v); } catch { /* ignore */ } };
  const total = data?.sites.reduce((a, s) => a + s.counts.total, 0) ?? 0;
  const admin = !!me?.user.is_tenant_admin;
  const canEdit = admin || !!data?.sites.some((s) => s.can_edit);
  const groups = new Map((data?.groups ?? []).map((g) => [g.id, g.name]));
  return (
    <>
      <div className="home-hello">
        <h1>{t("home.hello", { name: firstName(me?.user.full_name ?? "") })}</h1>
        <p className="muted">{t("ov.lead", { company: me?.tenant?.name ?? "" })}</p>
      </div>
      {error ? <div className="panel"><LoadError error={error} onRetry={reload} /></div> : !data ? (
        <div className="panel"><div className="panel-body"><div className="skeleton" style={{ width: "50%" }} /></div></div>
      ) : total === 0 ? (
        <div className="panel">
          <Empty icon={<LuFactory />} title={t("ov.empty")} body={t("ov.emptyBody")} action={
            <div className="row wrap" style={{ justifyContent: "center" }}>
              {canEdit && <Button variant="primary" icon={<LuPlus />} onClick={() => navigate("/assets?new=1")}>{t("ov.addAsset")}</Button>}
              {canEdit && <Button icon={<LuFileSpreadsheet />} onClick={() => navigate("/import")}>{t("ov.import")}</Button>}
              {admin && <Button icon={<LuSparkles />} onClick={() => setDemo(true)}>{t("ov.loadDemo")}</Button>}
            </div>} />
        </div>
      ) : (
        <div className="stack">
          <div className="row wrap">
            <span className="small muted">{t("ov.show")}</span>
            <div className="seg" role="group" aria-label={t("ov.show")}>
              <button type="button" aria-pressed={view === "all"} onClick={() => choose("all")}>{t("ov.all")}</button>
              <button type="button" aria-pressed={view === "cal"} onClick={() => choose("cal")}>{t("ov.cal")}</button>
              <button type="button" aria-pressed={view === "pm"} onClick={() => choose("pm")}>{t("ov.pm")}</button>
            </div>
            <span className="spacer" />
            {canEdit && <Button size="sm" variant="primary" icon={<LuPlus />} onClick={() => navigate("/assets?new=1")}>{t("ov.addAsset")}</Button>}
          </div>
          {data.sites.filter((s) => s.counts.total > 0 || data.sites.length === 1).map((s) => (
            <SiteCard key={s.id} site={s} view={view} groupName={s.group_id ? groups.get(s.group_id) : undefined} />
          ))}
        </div>
      )}
      {demo && <DemoDialog onClose={() => setDemo(false)} onDone={reload} />}
    </>
  );
}

export function Home() {
  const { me } = useSession();
  const { t } = useI18n();
  if (!me) return null;
  if (me.user.is_platform_admin) {
    return (
      <>
        <div className="home-hello">
          <h1>{t("home.hello", { name: firstName(me.user.full_name) })}</h1>
          <p className="muted">{t("home.leadPlatform")}</p>
        </div>
        <PlatformHome />
      </>
    );
  }
  return <Overview />;
}
