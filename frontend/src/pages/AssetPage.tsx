import { useRef, useState, type ReactNode } from "react";
import {
  LuArrowLeft, LuFileText, LuHistory, LuPencil, LuPlus, LuPrinter, LuQrCode, LuSignature, LuSparkles, LuTrash2, LuUpload,
} from "react-icons/lu";
import { CategoryIcon, Programmes, SignStatus, StatusBadge } from "../components/cm";
import { Badge, Button, Dialog, Empty, LoadError, Notice, SelectField, TextField, errorText, useLoad, useToast } from "../components/ui";
import { get, post, upload } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link, navigate } from "../lib/router";
import type { StringKey } from "../lib/strings";
import { nameOf, type Asset, type AssetFile } from "../lib/types";
import { AssetForm } from "./AssetForm";
import { AssetCalibration } from "./AssetWork";

const FILE_KINDS = ["photo", "nameplate", "manual", "certificate", "drawing", "other"] as const;
type Tab = "details" | "calibration" | "components" | "files" | "history" | "ai";

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return <div className="cm-fact"><dt>{label}</dt><dd>{children || <span className="subtle">—</span>}</dd></div>;
}

function AddFile({ asset, onClose, onDone }: { asset: Asset; onClose: () => void; onDone: (a: Asset) => void }) {
  const { t } = useI18n();
  const [kind, setKind] = useState<string>("photo");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Dialog title={t("asset.upload")} onClose={onClose} footer={<>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      <Button variant="primary" icon={<LuUpload />} busy={busy} disabled={!file} onClick={async () => {
        if (!file) return;
        setBusy(true); setErr(null);
        try {
          const purpose = kind === "photo" || kind === "nameplate" || kind === "manual" || kind === "certificate" ? kind : "attachment";
          const f = await upload<{ id: string }>(file, file.name, purpose);
          onDone(await post<Asset>(`/api/assets/${asset.id}/files`, { file_id: f.id, kind, title: title || null }));
          onClose();
        } catch (e) { setErr(errorText(e, t)); setBusy(false); }
      }}>{busy ? t("asset.uploading") : t("asset.upload")}</Button>
    </>}>
      <SelectField label={t("asset.fileKind")} value={kind} onChange={setKind}
        options={FILE_KINDS.map((k) => ({ value: k, label: t(`asset.fk.${k}` as StringKey) }))} />
      <TextField label={t("asset.fileTitle")} value={title} onChange={setTitle} maxLength={200} />
      <input type="file" accept={kind === "photo" || kind === "nameplate" ? "image/*" : "application/pdf,image/*,.docx,.xlsx"}
        capture={kind === "photo" || kind === "nameplate" ? "environment" : undefined}
        onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label={t("asset.upload")} />
      {err && <Notice tone="danger">{err}</Notice>}
    </Dialog>
  );
}

function RemoveFile({ file, onClose, onDone }: { file: AssetFile; onClose: () => void; onDone: (a: Asset) => void }) {
  const { t } = useI18n();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Dialog title={t("asset.removeTitle", { name: file.title || file.filename })} onClose={onClose} footer={<>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      <Button variant="danger" busy={busy} disabled={reason.trim().length < 3} onClick={async () => {
        setBusy(true); setErr(null);
        try { onDone(await post<Asset>(`/api/asset-files/${file.id}/remove`, { reason })); onClose(); }
        catch (e) { setErr(errorText(e, t)); setBusy(false); }
      }}>{t("asset.removeFile")}</Button>
    </>}>
      <p>{t("asset.removeBody")}</p>
      <TextField label={t("asset.removeReason")} value={reason} onChange={setReason} maxLength={500} />
      {err && <Notice tone="danger">{err}</Notice>}
    </Dialog>
  );
}

export function AssetPage({ assetId }: { assetId: string }) {
  const { t, lang, date, dateTime, qty } = useI18n();
  const toast = useToast();
  const { data: a, error, reload, setData } = useLoad(() => get<Asset>(`/api/assets/${assetId}`), [assetId]);
  const [tab, setTab] = useState<Tab>(() => (new URLSearchParams(window.location.search).get("tab") as Tab) || "details");
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [signing, setSigning] = useState(false);
  const [addFile, setAddFile] = useState(false);
  const [removing, setRemoving] = useState<AssetFile | null>(null);
  const photoRef = useRef<HTMLImageElement>(null);
  if (error) return <div className="panel"><LoadError error={error} onRetry={reload} /></div>;
  if (!a) return <div className="panel"><div className="panel-body"><div className="skeleton" style={{ width: "40%" }} /></div></div>;
  const nm = (en: string | null, ar: string | null) => (lang === "ar" && ar ? ar : en ?? "");
  const canSign = a.transitions.some((x) => x.can_sign);
  const range = a.range_min !== null || a.range_max !== null ? `${qty(a.range_min, 6)} – ${qty(a.range_max, 6)} ${a.unit ?? ""}` : (a.unit ?? "");
  const unavailable = ["standby", "out_of_service", "quarantined", "out_for_calibration"].includes(a.status);
  const photo = a.photo_file_id ?? a.nameplate_file_id;
  const tabs: { key: Tab; label: string; n?: number }[] = [
    { key: "details", label: t("asset.tab.details") },
    ...(a.calibration_required ? [{ key: "calibration" as Tab, label: t("asset.tab.calibration") }] : []),
    { key: "components", label: t("asset.tab.components"), n: a.children.length },
    { key: "files", label: t("asset.tab.files"), n: a.files.length },
    { key: "history", label: t("asset.tab.history"), n: a.history.length },
    { key: "ai", label: t("asset.tab.ai"), n: a.ai.length },
  ];
  return (
    <>
      <Link to="/assets" className="back-link"><LuArrowLeft className="flip-rtl" aria-hidden /> {t("asset.back")}</Link>
      <div className="cm-asset-head">
        {photo ? <img ref={photoRef} className="cm-asset-photo" src={`/api/files/${photo}/content?inline=1`} alt={t("asset.photo")} />
          : <div className="cm-asset-photo empty"><CategoryIcon category={a.category} /></div>}
        <div className="titles">
          <div className="row wrap" style={{ gap: 8 }}>
            <span className="mono-id cm-code">{a.code}</span>
            <StatusBadge status={a.status} />
            <Badge>{t(`crit.${a.gmp_criticality}` as StringKey)}</Badge>
          </div>
          <h1>{nameOf(a, lang)}</h1>
          <p className="muted small">
            {nm(a.class_name_en, a.class_name_ar)} · {a.site_code}{a.location_path.length ? ` / ${a.location_path.map((p) => p.code).join(" / ")}` : ""}
            {a.parent_code && <> · {t("assets.partOf", { code: "" })}<Link to={`/assets/${a.parent_id}`} className="mono-id">{a.parent_code}</Link></>}
            {a.status_changed_at && <> · {t("asset.since", { date: date(a.status_changed_at) })}</>}
          </p>
        </div>
        <div className="actions row wrap">
          {a.can_edit && <Button icon={<LuPencil />} onClick={() => setEditing(true)}>{t("asset.edit")}</Button>}
          {canSign && <Button variant="primary" icon={<LuSignature />} onClick={() => setSigning(true)}>{t("asset.status")}</Button>}
          <Button icon={<LuPrinter />} onClick={() => window.open(`/api/assets/labels.pdf?ids=${a.id}&layout=roll`, "_blank", "noopener")}>{t("asset.label")}</Button>
        </div>
      </div>
      {a.status === "draft" && <Notice tone="warn">{t("asset.draftNotice")}</Notice>}
      {unavailable && <Notice tone="danger">{t("asset.unavailableNotice", { status: t(`status.${a.status}` as StringKey) })}{a.status_note ? ` ${a.status_note}` : ""}</Notice>}
      {a.status === "restricted" && <Notice tone="warn">{t("asset.restrictedNotice", { note: a.status_note ?? "" })}</Notice>}
      {(a.status === "disposed" || a.status === "transferred") && <Notice>{t("asset.closed")}</Notice>}

      <div className="tabs" role="tablist">
        {tabs.map((x) => (
          <button key={x.key} type="button" role="tab" className="tab" aria-selected={tab === x.key} onClick={() => setTab(x.key)}>
            {x.label}{x.n ? <span className="cm-count">{x.n}</span> : null}
          </button>
        ))}
      </div>

      {tab === "details" && (
        <div className="grid-2" style={{ alignItems: "start" }}>
          <section className="panel"><div className="panel-head"><h3>{t("form.sec.identity")}</h3></div>
            <dl className="cm-facts">
              <Fact label={t("af.name_en")}>{a.name_en}</Fact>
              <Fact label={t("af.name_ar")}><span lang="ar" dir="rtl">{a.name_ar}</span></Fact>
              <Fact label={t("af.class")}>{a.class_code} · {nm(a.class_name_en, a.class_name_ar)}</Fact>
              <Fact label={t("af.manufacturer")}>{a.manufacturer}</Fact>
              <Fact label={t("af.model")}>{a.model}</Fact>
              <Fact label={t("af.serial_no")}><span className="mono-id">{a.serial_no}</span></Fact>
              <Fact label={t("af.asset_tag")}>{a.asset_tag}</Fact>
              <Fact label={t("af.capacity")}>{a.capacity}</Fact>
              <Fact label={t("af.range")}>{range}</Fact>
              <Fact label={t("af.resolution")}>{a.resolution}</Fact>
              <Fact label={t("af.accuracy")}>{a.accuracy}</Fact>
            </dl>
          </section>
          <div className="stack">
            <section className="panel"><div className="panel-head"><h3>{t("form.sec.place")}</h3></div>
              <dl className="cm-facts">
                <Fact label={t("af.site")}>{a.site_code} · {a.site_name}{a.group_name ? ` (${a.group_name})` : ""}</Fact>
                <Fact label={t("af.location")}>{a.location_path.map((p) => `${p.code} · ${nm(p.name_en, p.name_ar)}`).join(" › ")}</Fact>
                {a.is_portable && <Fact label={t("af.current_location")}>{a.current_location_code ? `${a.current_location_code} · ${nm(a.current_location_name_en, a.current_location_name_ar)}` : ""}</Fact>}
                <Fact label={t("af.line")}>{a.line_code ? `${a.line_code} · ${nm(a.line_name_en, a.line_name_ar)}` : ""}</Fact>
                <Fact label={t("af.department")}>{a.department_code ? `${a.department_code} · ${nm(a.department_name_en, a.department_name_ar)}` : ""}</Fact>
                <Fact label={t("af.parent")}>{a.parent_code && <Link to={`/assets/${a.parent_id}`}>{a.parent_code} · {nm(a.parent_name_en, a.parent_name_ar)}</Link>}</Fact>
              </dl>
            </section>
            <section className="panel"><div className="panel-head"><h3>{t("form.sec.gmp")}</h3></div>
              <dl className="cm-facts">
                <Fact label={t("asset.programmes")}><Programmes cal={a.calibration_required} pm={a.pm_required} /></Fact>
                <Fact label={t("af.gmp_criticality")}>{t(`crit.${a.gmp_criticality}` as StringKey)}</Fact>
                <Fact label={t("af.install_date")}>{date(a.install_date)}</Fact>
                <Fact label={t("af.warranty_until")}>{date(a.warranty_until)}</Fact>
                <Fact label={t("af.service_vendor")}>{a.service_vendor_name}</Fact>
                <Fact label={t("af.supplier")}>{a.supplier_name}</Fact>
                <Fact label={t("af.external_ref")}><span className="mono-id">{a.external_ref}</span></Fact>
                <Fact label={t("af.notes")}>{a.notes}</Fact>
              </dl>
              <div className="panel-body small subtle row"><LuQrCode aria-hidden /> {t("asset.scanHint")}</div>
            </section>
          </div>
        </div>
      )}

      {tab === "calibration" && <AssetCalibration asset={a} onChanged={reload} />}

      {tab === "components" && (
        <section className="panel">
          <div className="panel-head"><h3>{t("asset.tab.components")}</h3><span className="spacer" />
            {a.can_edit && <Button size="sm" icon={<LuPlus />} onClick={() => setAdding(true)}>{t("asset.addComponent")}</Button>}</div>
          {a.children.length === 0 ? <Empty icon={<LuPlus />} title={t("asset.noComponents")} body={t("asset.noComponentsBody")} /> : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>{t("assets.col.code")}</th><th>{t("assets.col.name")}</th><th>{t("assets.col.class")}</th><th>{t("assets.col.prog")}</th><th>{t("assets.col.status")}</th></tr></thead>
              <tbody>{a.children.map((c) => (
                <tr key={c.id}>
                  <td><Link to={`/assets/${c.id}`} className="mono-id cell-main">{c.code}</Link></td>
                  <td>{nameOf(c, lang)}</td>
                  <td><span className="row small"><CategoryIcon category={c.category} />{nm(c.class_name_en, c.class_name_ar)}</span></td>
                  <td><Programmes cal={c.calibration_required} pm={c.pm_required} /></td>
                  <td><StatusBadge status={c.status} /></td>
                </tr>))}</tbody>
            </table></div>
          )}
        </section>
      )}

      {tab === "files" && (
        <section className="panel">
          <div className="panel-head"><h3>{t("asset.tab.files")}</h3><span className="spacer" />
            {a.can_edit && <Button size="sm" icon={<LuUpload />} onClick={() => setAddFile(true)}>{t("asset.upload")}</Button>}</div>
          {a.files.length === 0 ? <Empty icon={<LuFileText />} title={t("asset.noFiles")} body={t("asset.noFilesBody")} /> : (
            <ul className="cm-files">
              {a.files.map((f) => (
                <li key={f.id}>
                  {f.mime.startsWith("image/") ? <img src={`/api/files/${f.file_id}/content?inline=1`} alt="" /> : <span className="cm-file-icon"><LuFileText /></span>}
                  <div className="grow">
                    <a href={`/api/files/${f.file_id}/content`} className="cell-main">{f.title || f.filename}</a>
                    <div className="small subtle">{t(`asset.fk.${f.kind}` as StringKey)} · {Math.max(1, Math.round(f.size_bytes / 1024))} KB · {dateTime(f.created_at)}{f.added_by ? ` · ${f.added_by}` : ""}</div>
                    <div className="small subtle mono-id" title={t("asset.hash")}>SHA-256 {f.sha256.slice(0, 16)}…</div>
                  </div>
                  {a.can_edit && <Button size="sm" variant="ghost" icon={<LuTrash2 />} onClick={() => setRemoving(f)}>{t("asset.removeFile")}</Button>}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {tab === "history" && (
        <section className="panel">
          {a.history.length === 0 ? <Empty icon={<LuHistory />} title={t("asset.noHistory")} body={t("asset.noHistoryBody")} /> : (
            <ol className="cm-history">
              {a.history.map((h) => (
                <li key={h.id}>
                  <div className="row wrap" style={{ gap: 8 }}>
                    {h.from_status && <><StatusBadge status={h.from_status} /><span aria-hidden>→</span></>}
                    <StatusBadge status={h.to_status} />
                    <span className="small muted">{dateTime(h.changed_at)}</span>
                  </div>
                  <p>{h.reason}</p>
                  {h.source === "signature" ? (
                    <div className="sign-record small">
                      <strong><LuSignature aria-hidden /> {t("asset.signedBy", { name: h.user_name })}</strong> &lt;{h.user_email}&gt; · {dateTime(h.signed_at)}
                      <div className="subtle">“{h.statement}”</div>
                      <div className="subtle mono-id" title={t("asset.hash")}>SHA-256 {h.record_hash}</div>
                    </div>
                  ) : <Badge>{t("asset.demoData")}</Badge>}
                </li>
              ))}
            </ol>
          )}
        </section>
      )}

      {tab === "ai" && (
        <section className="panel">
          {a.ai.length === 0 ? <div className="panel-body muted">{t("asset.noAi")}</div> : (
            <ul className="cm-history">
              {a.ai.map((x) => (
                <li key={x.id}>
                  <div className="row wrap" style={{ gap: 8 }}><LuSparkles aria-hidden /><strong>{t(`asset.ai.${x.kind}` as StringKey)}</strong>
                    <span className="small muted">{dateTime(x.created_at)}</span></div>
                  <p className="small">{t("asset.ai.by", { model: x.model, name: x.created_by_name ?? "" })} · {x.prompt_version}</p>
                  {x.reviewed_by_name && <p className="small">{t("asset.ai.accepted", { name: x.reviewed_by_name })} · {dateTime(x.reviewed_at)}</p>}
                  <p className="small">{x.changed_fields && Object.keys(x.changed_fields).length
                    ? t("asset.ai.changed", { fields: Object.entries(x.changed_fields).map(([k, v]) => `${t(`af.${k}` as StringKey)}: “${v.ai}” → “${v.final ?? "—"}”`).join("; ") })
                    : t("asset.ai.unchanged")}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {editing && <AssetForm asset={a} siteId={a.site_id} onClose={() => setEditing(false)} onSaved={(x) => setData(x)} />}
      {adding && <AssetForm parent={a} siteId={a.site_id} onClose={() => setAdding(false)} onSaved={(x) => navigate(`/assets/${x.id}`)} />}
      {signing && <SignStatus assets={[{ id: a.id, code: a.code, status: a.status }]} onClose={() => setSigning(false)} onDone={reload} />}
      {addFile && <AddFile asset={a} onClose={() => setAddFile(false)} onDone={(x) => { setData(x); toast(t("list.saved")); }} />}
      {removing && <RemoveFile file={removing} onClose={() => setRemoving(null)} onDone={(x) => setData(x)} />}
    </>
  );
}

export function ScanCode({ code }: { code: string }) {
  const { t } = useI18n();
  const [err, setErr] = useState<string | null>(null);
  const started = useRef(false);
  if (!started.current) {
    started.current = true;
    get<{ id: string }>(`/api/assets/by-code/${encodeURIComponent(code)}`)
      .then((r) => navigate(`/assets/${r.id}`, true))
      .catch(() => setErr(t("scan.notFound", { code })));
  }
  return <div className="panel"><div className="panel-body">{err ? <Notice tone="warn">{err}</Notice> : <span className="muted">{t("scan.opening", { code })}</span>}</div></div>;
}
