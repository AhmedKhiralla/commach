// Pilot readiness: the platform owner's system check, run after each deployment.
import { useState } from "react";
import { LuCircleAlert, LuCircleCheck, LuCircleX, LuInfo, LuRefreshCw } from "react-icons/lu";
import { Badge, Button, LoadError, LoadingRows, PageHead, useLoad } from "../components/ui";
import { get } from "../lib/api";
import { useI18n } from "../lib/i18n";
import type { StringKey } from "../lib/strings";

interface Check { key: string; status: "ok" | "warn" | "fail" | "info"; detail: string; action: string | null }
interface Out {
  status: "ok" | "warn" | "fail"; checks: Check[]; deep: boolean; seconds: number; checked_at: string; version: string | null;
  counts: { companies: number; users: number; assets: number; tasks: number; audit_entries: number; database_size: string };
}

const ICON = { ok: <LuCircleCheck />, warn: <LuCircleAlert />, fail: <LuCircleX />, info: <LuInfo /> };
const TONE = { ok: "ok", warn: "warn", fail: "danger", info: undefined } as const;

export function SystemCheckPage() {
  const { t, dateTime } = useI18n();
  const [deep, setDeep] = useState(false);
  const [n, setN] = useState(0);
  const r = useLoad(() => get<Out>(`/api/platform/system-check${deep ? "?deep=1" : ""}`), [deep, n]);
  return (
    <>
      <PageHead title={t("sys.title")} lead={t("sys.lead")} actions={<>
        <Button icon={<LuRefreshCw />} onClick={() => { setDeep(false); setN((x) => x + 1); }} disabled={!r.data && !r.error}>{t("sys.run")}</Button>
        <Button variant="primary" onClick={() => { setDeep(true); setN((x) => x + 1); }} disabled={!r.data && !r.error}>{t("sys.deep")}</Button>
      </>} />
      {r.error ? <div className="panel"><LoadError error={r.error} onRetry={r.reload} /></div> : !r.data ? <div className="panel"><LoadingRows rows={8} cols={3} /></div> : (
        <>
          <div className={`panel cm-pad cm-sys-head ${r.data.status}`}>
            <span className="cm-sys-big">{ICON[r.data.status]} {t(`sys.overall.${r.data.status}` as StringKey)}</span>
            <span className="small muted">{dateTime(r.data.checked_at)} · {r.data.seconds} s{r.data.deep ? ` · ${t("sys.deepDone")}` : ""}
              {r.data.version ? ` · ${r.data.version}` : ""}</span>
            <span className="small muted">{t("sys.counts", { ...r.data.counts })}</span>
          </div>
          <div className="panel">
            <ul className="cm-sys">
              {r.data.checks.map((c) => (
                <li key={c.key} className={c.status}>
                  <span className={`cm-sys-icon ${c.status}`}>{ICON[c.status]}</span>
                  <div className="grow">
                    <div className="row wrap" style={{ gap: 8 }}><strong>{t(`sys.k.${c.key}` as StringKey)}</strong>
                      <Badge tone={TONE[c.status]}>{t(`sys.s.${c.status}` as StringKey)}</Badge></div>
                    <div className="small">{c.detail}</div>
                    {c.action && <div className="small muted">→ {c.action}</div>}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </>
  );
}
