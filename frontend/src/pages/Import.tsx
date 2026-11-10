import { useMemo, useRef, useState } from "react";
import { LuDownload, LuFileSpreadsheet, LuRefreshCw, LuSignature, LuSparkles, LuUpload } from "react-icons/lu";
import { SignStatus } from "../components/cm";
import { Button, Notice, PageHead, SelectField, errorText, useLoad } from "../components/ui";
import { ApiError, get, post, upload } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { navigate } from "../lib/router";
import { usePerms, useSession } from "../lib/session";
import type { StringKey } from "../lib/strings";
import type { AssetClass, AssetStatus } from "../lib/types";

interface Preview {
  sheets: string[]; sheet: string; header_row: number; headers: string[]; mapping: Record<string, string | null>;
  classes: Record<string, string>; status: Record<string, string>; criticality: Record<string, string>;
  class_values: string[]; status_values: string[]; missing_required: string[];
  rows: { row: number; values: Record<string, unknown>; errors: string[]; warnings: string[] }[];
  summary: { rows: number; ok: number; errors: number; new_locations: string[] };
  columns: Record<string, { label: string; required: boolean; description: string }>;
  ai: { suggestion_id: string; model: string; notes: string } | null;
  samples?: Record<string, string[]>;
}

const STATUS_CHOICES = ["in_service", "standby", "out_of_service", "retired", "draft"];

export function ImportPage() {
  const { t, number } = useI18n();
  const { me, siteId } = useSession();
  const { admin } = usePerms();
  const sites = (me?.sites ?? []).filter((s) => admin || s.roles.some((r) => ["engineering_manager", "engineering_supervisor", "qa_calibration_officer"].includes(r)));
  const [site, setSite] = useState(siteId && sites.some((s) => s.id === siteId) ? siteId : sites[0]?.id ?? "");
  const [file, setFile] = useState<{ id: string; filename: string } | null>(null);
  const [p, setP] = useState<Preview | null>(null);
  const [busy, setBusy] = useState<"read" | "ai" | "commit" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [createLocs, setCreateLocs] = useState(true);
  const [done, setDone] = useState<{ created: { id: string; code: string; intended_status: string | null }[] } | null>(null);
  const [signing, setSigning] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const classes = useLoad(() => get<{ items: AssetClass[] }>("/api/asset-classes"));
  const ai = me?.tenant?.ai;

  const run = async (fileId: string, over: Partial<Preview> & { use_ai?: boolean } = {}, kind: "read" | "ai" = "read") => {
    setBusy(kind); setErr(null);
    try {
      const body = {
        file_id: fileId, site_id: site, sheet: over.sheet ?? p?.sheet ?? null, header_row: over.header_row ?? p?.header_row ?? null,
        mapping: over.mapping ?? (over.sheet || over.header_row ? null : p?.mapping ?? null),
        classes: over.classes ?? p?.classes ?? null, status: over.status ?? p?.status ?? null, criticality: p?.criticality ?? null,
        use_ai: !!over.use_ai, create_locations: createLocs,
      };
      const r = await post<Preview>("/api/assets/import/preview", body);
      setP((old) => ({ ...r, ai: r.ai ?? old?.ai ?? null }));
    } catch (e) { setErr(errorText(e, t)); }
    finally { setBusy(null); }
  };

  const choose = async (f: File) => {
    setErr(null); setP(null); setDone(null); setBusy("read");
    try {
      const up = await upload<{ id: string; filename: string }>(f, f.name, "import");
      setFile(up);
      await run(up.id);
    } catch (e) { setErr(errorText(e, t)); setBusy(null); }
  };

  const commit = async () => {
    if (!file || !p) return;
    setBusy("commit"); setErr(null);
    try {
      const r = await post<{ created: { id: string; code: string; intended_status: string | null }[] }>("/api/assets/import/commit", {
        file_id: file.id, site_id: site, sheet: p.sheet, header_row: p.header_row, mapping: p.mapping, classes: p.classes,
        status: p.status, criticality: p.criticality, create_locations: createLocs, suggestion_id: p.ai?.suggestion_id ?? null,
      });
      setDone(r);
    } catch (e) {
      setErr(errorText(e, t));
      if (e instanceof ApiError && e.code === "import_errors") run(file.id);
    } finally { setBusy(null); }
  };

  const sample = useMemo(() => {
    const out: Record<string, string> = {};
    if (p) p.headers.forEach((h) => { out[h] = (p.samples?.[h] ?? []).join(" · "); });
    return out;
  }, [p]);

  const keys = p ? Object.keys(p.columns) : [];
  const toApprove = done?.created.filter((c) => c.intended_status === "in_service") ?? [];
  return (
    <>
      <PageHead title={t("imp.title")} lead={t("imp.lead")}
        actions={<Button icon={<LuDownload />} onClick={() => { window.location.href = "/api/assets/template.xlsx"; }}>{t("imp.template")}</Button>} />
      {err && <Notice tone="danger">{err}</Notice>}
      {done ? (
        <section className="panel"><div className="panel-body stack">
          <Notice tone="ok">{t("imp.done", { n: number(done.created.length) })}</Notice>
          <div className="row wrap">
            {toApprove.length > 0 && <Button variant="primary" icon={<LuSignature />} onClick={() => setSigning(true)}>{t("imp.approve")} ({toApprove.length})</Button>}
            <Button onClick={() => navigate(`/assets?site_id=${site}&status=draft`)}>{t("imp.openList")}</Button>
            <Button variant="ghost" onClick={() => { setDone(null); setP(null); setFile(null); }}>{t("imp.another")}</Button>
          </div>
        </div></section>
      ) : (
        <div className="stack">
          <section className="panel">
            <div className="panel-head"><h2><span className="cm-step">1</span> {t("imp.step1")}</h2></div>
            <div className="panel-body row wrap" style={{ alignItems: "flex-end" }}>
              {sites.length > 1 && <div style={{ minWidth: 260 }}><SelectField label={t("imp.site")} value={site} onChange={setSite}
                options={sites.map((s) => ({ value: s.id, label: `${s.code} · ${s.name}` }))} /></div>}
              <input ref={fileRef} type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden
                onChange={(e) => { const f = e.target.files?.[0]; if (f) choose(f); e.target.value = ""; }} />
              <Button variant="primary" icon={<LuUpload />} busy={busy === "read" && !p} onClick={() => fileRef.current?.click()}>{t("imp.choose")}</Button>
              {file && <span className="file-chip"><LuFileSpreadsheet aria-hidden /> {file.filename}</span>}
            </div>
          </section>
          {p && file && (
            <>
              <section className="panel">
                <div className="panel-head">
                  <h2><span className="cm-step">2</span> {t("imp.step2")}</h2><span className="spacer" />
                  {ai?.configured && ai.import && <Button icon={<LuSparkles />} busy={busy === "ai"} onClick={() => run(file.id, { use_ai: true, mapping: undefined }, "ai")}>
                    {busy === "ai" ? t("imp.aiBusy") : t("imp.useAi")}</Button>}
                </div>
                <div className="panel-body stack">
                  <div className="row wrap">
                    <div style={{ minWidth: 200 }}><SelectField label={t("imp.sheet")} value={p.sheet} onChange={(v) => run(file.id, { sheet: v })}
                      options={p.sheets.map((s) => ({ value: s, label: s }))} /></div>
                    <div style={{ width: 120 }}><SelectField label={t("imp.headerRow")} value={String(p.header_row)} onChange={(v) => run(file.id, { header_row: Number(v) })}
                      options={Array.from({ length: 20 }, (_, i) => ({ value: String(i + 1), label: String(i + 1) }))} /></div>
                  </div>
                  {p.ai && <Notice tone="info"><div className="stack" style={{ gap: 4 }}><span><LuSparkles aria-hidden /> {t("imp.aiUsed", { model: p.ai.model })}</span>
                    {p.ai.notes && <span className="small"><strong>{t("imp.aiNotes")}:</strong> {p.ai.notes}</span>}</div></Notice>}
                  {p.missing_required.length > 0 && <Notice tone="warn">{t("imp.missing", { fields: p.missing_required.map((k) => p.columns[k].label).join(", ") })}</Notice>}
                  <div className="table-wrap"><table className="table">
                    <thead><tr><th>{t("imp.column")}</th><th>{t("imp.maps")}</th><th>{t("imp.sample")}</th></tr></thead>
                    <tbody>{p.headers.map((h) => (
                      <tr key={h}>
                        <td className="cell-main">{h}</td>
                        <td><select className="select" aria-label={`${h} → ${t("imp.maps")}`} value={p.mapping[h] ?? ""}
                          onChange={(e) => { const m = { ...p.mapping }; Object.keys(m).forEach((k) => { if (m[k] === e.target.value) m[k] = null; }); m[h] = e.target.value || null; run(file.id, { mapping: m }); }}>
                          <option value="">{t("imp.ignore")}</option>
                          {keys.map((k) => <option key={k} value={k}>{p.columns[k].label}{p.columns[k].required ? " *" : ""}</option>)}
                        </select></td>
                        <td className="small muted">{sample[h]}</td>
                      </tr>))}</tbody>
                  </table></div>
                  {p.class_values.length > 0 && (
                    <div className="stack">
                      <h3>{t("imp.classes")}</h3>
                      <div className="cm-map-grid">
                        {p.class_values.map((v) => (
                          <label key={v} className="row">
                            <span className="grow">{v}</span>
                            <select className="select" value={p.classes[v] ?? (classes.data?.items.some((c) => c.code === v.toUpperCase()) ? v.toUpperCase() : "")}
                              onChange={(e) => run(file.id, { classes: { ...p.classes, [v]: e.target.value } })}>
                              <option value="">{t("imp.unmapped")}</option>
                              {(classes.data?.items ?? []).filter((c) => c.active).map((c) => <option key={c.id} value={c.code}>{c.code} · {c.name_en}</option>)}
                            </select>
                          </label>
                        ))}
                      </div>
                    </div>
                  )}
                  {p.status_values.length > 0 && (
                    <div className="stack">
                      <h3>{t("imp.statusMap")}</h3>
                      <div className="cm-map-grid">
                        {p.status_values.map((v) => (
                          <label key={v} className="row">
                            <span className="grow">{v}</span>
                            <select className="select" value={p.status[v] ?? ""} onChange={(e) => run(file.id, { status: { ...p.status, [v]: e.target.value } })}>
                              <option value="">{t("imp.unmapped")}</option>
                              {STATUS_CHOICES.map((s) => <option key={s} value={s}>{t(`status.${s}` as StringKey)}</option>)}
                            </select>
                          </label>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </section>
              <section className="panel">
                <div className="panel-head">
                  <h2><span className="cm-step">3</span> {t("imp.step3")}</h2><span className="spacer" />
                  <Button size="sm" variant="ghost" icon={<LuRefreshCw />} onClick={() => run(file.id)}>{t("imp.recheck")}</Button>
                </div>
                <div className="panel-body stack">
                  <div className="row wrap">
                    <strong>{t("imp.rows", { ok: number(p.summary.ok), rows: number(p.summary.rows) })}</strong>
                    {p.summary.errors > 0 && <span className="text-danger">{t("imp.errorsN", { n: number(p.summary.errors) })}</span>}
                  </div>
                  <label className="row small"><input type="checkbox" checked={createLocs} onChange={(e) => { setCreateLocs(e.target.checked); }} />{t("imp.createLocs")}</label>
                  {p.summary.new_locations.length > 0 && <p className="small muted">{t("imp.newLocs", { list: p.summary.new_locations.join(", ") })}</p>}
                  <div className="table-wrap"><table className="table">
                    <thead><tr><th className="num">{t("imp.row")}</th><th>{t("af.code")}</th><th>{t("af.name_en")}</th><th>{t("af.class")}</th><th>{t("imp.problems")}</th></tr></thead>
                    <tbody>{p.rows.map((r) => (
                      <tr key={r.row} className={r.errors.length ? "cm-row-bad" : ""}>
                        <td className="num small">{r.row}</td>
                        <td className="mono-id">{String(r.values.code ?? "")}</td>
                        <td>{String(r.values.name_en ?? "")}</td>
                        <td className="small">{String(r.values.class_code ?? "")}</td>
                        <td className="small">
                          {r.errors.map((e) => <div key={e} className="text-danger">{e}</div>)}
                          {r.warnings.map((w) => <div key={w} className="subtle">{w}</div>)}
                        </td>
                      </tr>))}</tbody>
                  </table></div>
                  <div><Button variant="primary" busy={busy === "commit"} disabled={p.summary.errors > 0 || p.summary.ok === 0 || p.missing_required.length > 0}
                    onClick={commit}>{t("imp.commit", { n: number(p.summary.ok) })}</Button></div>
                </div>
              </section>
            </>
          )}
        </div>
      )}
      {signing && done && <SignStatus assets={toApprove.map((c) => ({ id: c.id, code: c.code, status: "draft" as AssetStatus }))}
        initial="in_service" onClose={() => setSigning(false)} onDone={() => navigate(`/assets?site_id=${site}`)} />}
    </>
  );
}
