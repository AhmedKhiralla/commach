// Settings › Calibration rules: tolerance windows per interval band, "due soon", quarantine on fail, uncertainty ratio.
import { useEffect, useState } from "react";
import { LuPlus, LuTrash2 } from "react-icons/lu";
import { Badge, Button, LoadError, Notice, Switch, errorText, useLoad, useToast } from "../components/ui";
import type { Rule, TolUnit } from "../components/work";
import { get, post, put } from "../lib/api";
import { useI18n } from "../lib/i18n";
import type { StringKey } from "../lib/strings";

interface Closure { from: string; to: string; name: string }
interface Cal { weekend: number[]; closures: Closure[]; shift: "earlier" | "later"; kinds: ("calibration" | "pm")[] }
interface Esc { accept_before_due_days: number; deviation_after_days: Record<string, number | null>; kinds: ("calibration" | "pm")[]; digest_hour: number }
interface WS { tolerance: { calibration: Rule[]; pm: Rule[] }; due_soon_days: number; quarantine_on_fail: boolean; uncertainty_ratio_min: number; calendar: Cal; escalation: Esc }

const CRIT = ["critical", "major", "minor", "non_gmp"] as const;

function EscalationEditor({ esc, onChange }: { esc: Esc; onChange: (e: Esc) => void }) {
  const { t, dateTime } = useI18n();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<{ items: { id: string; to_email: string; kind: string; subject: string; status: string; created_at: string; last_error: string | null }[]; configured: boolean } | null>(null);
  useEffect(() => { get("/api/notifications/emails").then(setLog).catch(() => {}); }, []);
  return (
    <section className="panel">
      <div className="panel-head"><h3>{t("es.title")}</h3><span className="spacer" />
        <Button size="sm" busy={busy} onClick={async () => {
          setBusy(true);
          try {
            const r = await post<Record<string, number>>("/api/notifications/run");
            toast(t("es.ran", { l1: r.level1 ?? 0, l2: r.level2 ?? 0, l3: r.level3 ?? 0, dev: r.deviations ?? 0, dig: r.digests ?? 0 }));
            get("/api/notifications/emails").then(setLog).catch(() => {});
          } finally { setBusy(false); }
        }}>{t("es.run")}</Button></div>
      <div className="panel-body stack">
        <p className="small muted">{t("es.lead")}</p>
        <label className="row"><span className="grow">{t("es.accept")}</span>
          <input className="input" type="number" min={0} max={90} style={{ width: 90 }} aria-label={t("es.accept")} value={esc.accept_before_due_days}
            onChange={(e) => onChange({ ...esc, accept_before_due_days: Number(e.target.value) || 0 })} /></label>
        <div className="field">
          <span className="field-label">{t("es.deviation")}</span>
          <div className="grid-4">
            {CRIT.map((c) => (
              <label key={c} className="stack" style={{ gap: 4 }}><span className="small">{t(`crit.${c}` as StringKey)}</span>
                <select className="select" value={esc.deviation_after_days[c] == null ? "" : String(esc.deviation_after_days[c])}
                  onChange={(e) => onChange({ ...esc, deviation_after_days: { ...esc.deviation_after_days, [c]: e.target.value === "" ? null : Number(e.target.value) } })}>
                  <option value="">{t("es.never")}</option>
                  {[0, 1, 3, 7, 14, 30, 60].map((d) => <option key={d} value={d}>{d}</option>)}
                </select></label>
            ))}
          </div>
        </div>
        <div className="field">
          <span className="field-label">{t("es.kinds")}</span>
          <div className="chip-row">
            {(["calibration", "pm"] as const).map((k) => (
              <button key={k} type="button" className="chip" aria-pressed={esc.kinds.includes(k)}
                onClick={() => onChange({ ...esc, kinds: esc.kinds.includes(k) ? esc.kinds.filter((x) => x !== k) : [...esc.kinds, k] })}>{t(k === "pm" ? "ws.pm" : "ws.cal")}</button>
            ))}
          </div>
        </div>
        <label className="row"><span className="grow">{t("es.digestHour")}</span>
          <select className="select" style={{ width: 110 }} aria-label={t("es.digestHour")} value={esc.digest_hour} onChange={(e) => onChange({ ...esc, digest_hour: Number(e.target.value) })}>
            {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
          </select></label>
        {log && !log.configured && <Notice tone="warn">{t("em.notConfigured")}</Notice>}
        <h4 className="sheet-section">{t("es.log")}</h4>
        {!log?.items.length ? <p className="small subtle">{t("es.logEmpty")}</p> : (
          <div className="table-wrap"><table className="table">
            <tbody>{log.items.slice(0, 12).map((e) => (
              <tr key={e.id}><td className="small nowrap">{dateTime(e.created_at)}</td><td className="small">{e.to_email}</td>
                <td className="small">{e.subject}</td>
                <td><Badge tone={e.status === "sent" ? "ok" : e.status === "failed" ? "danger" : undefined}>{t(`es.st.${e.status}` as StringKey)}</Badge></td></tr>))}</tbody>
          </table></div>
        )}
      </div>
    </section>
  );
}

function CalendarEditor({ cal, onChange }: { cal: Cal; onChange: (c: Cal) => void }) {
  const { t } = useI18n();
  const order = [5, 6, 0, 1, 2, 3, 4];   // Saturday first, as on Egyptian and Gulf calendars
  const setC = (i: number, p: Partial<Closure>) => onChange({ ...cal, closures: cal.closures.map((c, j) => (j === i ? { ...c, ...p } : c)) });
  return (
    <section className="panel">
      <div className="panel-head"><h3>{t("cal.title")}</h3></div>
      <div className="panel-body stack">
        <p className="small muted">{t("cal.lead")}</p>
        <div className="field">
          <span className="field-label">{t("cal.weekend")}</span>
          <div className="chip-row">
            {order.map((d) => (
              <button key={d} type="button" className="chip" aria-pressed={cal.weekend.includes(d)}
                onClick={() => onChange({ ...cal, weekend: cal.weekend.includes(d) ? cal.weekend.filter((x) => x !== d) : [...cal.weekend, d] })}>{t(`day.${d}` as StringKey)}</button>
            ))}
          </div>
        </div>
        <div className="grid-2">
          <div className="field">
            <span className="field-label">{t("cal.shift")}</span>
            <div className="seg" role="group" aria-label={t("cal.shift")}>
              {(["earlier", "later"] as const).map((x) => <button key={x} type="button" aria-pressed={cal.shift === x} onClick={() => onChange({ ...cal, shift: x })}>{t(`cal.shift.${x}` as StringKey)}</button>)}
            </div>
          </div>
          <div className="field">
            <span className="field-label">{t("cal.kinds")}</span>
            <div className="chip-row">
              {(["pm", "calibration"] as const).map((k) => (
                <button key={k} type="button" className="chip" aria-pressed={cal.kinds.includes(k)}
                  onClick={() => onChange({ ...cal, kinds: cal.kinds.includes(k) ? cal.kinds.filter((x) => x !== k) : [...cal.kinds, k] })}>{t(k === "pm" ? "ws.pm" : "ws.cal")}</button>
              ))}
            </div>
          </div>
        </div>
        <div className="field">
          <span className="field-label">{t("cal.closures")}</span>
          {cal.closures.length === 0 ? <span className="small subtle">{t("cal.none")}</span> : (
            <div className="table-wrap"><table className="table edit-table">
              <thead><tr><th>{t("cal.from")}</th><th>{t("cal.to")}</th><th>{t("cal.name")}</th><th /></tr></thead>
              <tbody>{cal.closures.map((c, i) => (
                <tr key={i}>
                  <td><input className="input cell-input" style={{ width: 150 }} type="date" aria-label={t("cal.from")} value={c.from} onChange={(e) => setC(i, { from: e.target.value, to: c.to && c.to >= e.target.value ? c.to : e.target.value })} /></td>
                  <td><input className="input cell-input" style={{ width: 150 }} type="date" aria-label={t("cal.to")} value={c.to} min={c.from} onChange={(e) => setC(i, { to: e.target.value })} /></td>
                  <td><input className="input cell-input wide" aria-label={t("cal.name")} value={c.name} maxLength={120} onChange={(e) => setC(i, { name: e.target.value })} /></td>
                  <td><Button size="sm" variant="ghost" iconOnly aria-label={t("common.delete")} icon={<LuTrash2 />} onClick={() => onChange({ ...cal, closures: cal.closures.filter((_, j) => j !== i) })} /></td>
                </tr>))}</tbody>
            </table></div>
          )}
          <div><Button size="sm" icon={<LuPlus />} onClick={() => { const d = new Date().toISOString().slice(0, 10); onChange({ ...cal, closures: [...cal.closures, { from: d, to: d, name: "" }] }); }}>{t("cal.add")}</Button></div>
        </div>
      </div>
    </section>
  );
}

function Bands({ kind, bands, onChange }: { kind: "calibration" | "pm"; bands: Rule[]; onChange: (b: Rule[]) => void }) {
  const { t } = useI18n();
  const sorted = bands.slice().sort((a, b) => (a.up_to_days == null ? 1 : b.up_to_days == null ? -1 : a.up_to_days - b.up_to_days));
  const set = (i: number, patch: Partial<Rule>) => onChange(sorted.map((b, j) => (j === i ? { ...b, ...patch } : b)));
  const unit = (withAny: boolean) => (withAny ? ["any", "day", "week", "month", "percent"] : ["day", "week", "month", "percent"]) as TolUnit[];
  return (
    <section className="panel">
      <div className="panel-head"><h3>{t(kind === "calibration" ? "ws.cal" : "ws.pm")}</h3><span className="spacer" />
        <Button size="sm" icon={<LuPlus />} onClick={() => onChange([...sorted.filter((b) => b.up_to_days != null),
          { up_to_days: Math.max(1, ...sorted.map((b) => b.up_to_days ?? 0)) + 30, before_unit: kind === "calibration" ? "any" : "day", before_value: kind === "calibration" ? null : 7, after_unit: "day", after_value: 7 },
          ...sorted.filter((b) => b.up_to_days == null)])}>{t("ws.addBand")}</Button></div>
      <div className="table-wrap">
        <table className="table edit-table">
          <thead><tr><th>{t("ws.upTo")}</th><th>{t("ws.early")}</th><th>{t("ws.late")}</th><th /></tr></thead>
          <tbody>
            {sorted.map((b, i) => (
              <tr key={i}>
                <td>{b.up_to_days == null ? <span className="muted">{t("ws.longer")}</span> : (
                  <input className="input cell-input" type="number" min={1} aria-label={t("ws.upTo")} value={b.up_to_days}
                    onChange={(e) => set(i, { up_to_days: Number(e.target.value) || 1 })} />
                )}</td>
                <td><span className="row" style={{ gap: 4 }}>
                  {b.before_unit !== "any" && <input className="input cell-input" type="number" min={0} aria-label={t("ws.early")} value={b.before_value ?? 0}
                    onChange={(e) => set(i, { before_value: Number(e.target.value) })} />}
                  <select className="select cell-input" aria-label={t("ws.early")} value={b.before_unit}
                    onChange={(e) => set(i, { before_unit: e.target.value as TolUnit, before_value: e.target.value === "any" ? null : b.before_value ?? 0 })}>
                    {unit(true).map((u) => <option key={u} value={u}>{t(`unit.${u}` as StringKey)}</option>)}
                  </select>
                </span></td>
                <td><span className="row" style={{ gap: 4 }}>
                  <input className="input cell-input" type="number" min={0} aria-label={t("ws.late")} value={b.after_value ?? 0}
                    onChange={(e) => set(i, { after_value: Number(e.target.value) })} />
                  <select className="select cell-input" aria-label={t("ws.late")} value={b.after_unit} onChange={(e) => set(i, { after_unit: e.target.value as TolUnit })}>
                    {unit(false).map((u) => <option key={u} value={u}>{t(`unit.${u}` as StringKey)}</option>)}
                  </select>
                </span></td>
                <td>{b.up_to_days != null && <Button size="sm" variant="ghost" iconOnly aria-label={t("common.delete")} icon={<LuTrash2 />}
                  onClick={() => onChange(sorted.filter((_, j) => j !== i))} />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function WorkSettingsTab() {
  const { t } = useI18n();
  const toast = useToast();
  const { data, error, reload, setData } = useLoad(() => get<{ settings: WS }>("/api/settings/work"));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (error) return <LoadError error={error} onRetry={reload} />;
  if (!data) return <div className="skeleton" style={{ width: "40%" }} />;
  const s = data.settings;
  const set = (p: Partial<WS>) => setData({ settings: { ...s, ...p } });
  const save = async () => {
    setBusy(true); setErr(null);
    try {
      setData(await put<{ settings: WS }>("/api/settings/work", { calibration: s.tolerance.calibration, pm: s.tolerance.pm,
        due_soon_days: s.due_soon_days, quarantine_on_fail: s.quarantine_on_fail, uncertainty_ratio_min: s.uncertainty_ratio_min,
        calendar: { ...s.calendar, closures: s.calendar.closures.filter((c) => c.from && c.name.trim()) }, escalation: s.escalation }));
      toast(t("list.saved"));
    } catch (e) { setErr(errorText(e, t)); } finally { setBusy(false); }
  };
  return (
    <div className="stack">
      <p className="muted">{t("ws.lead")}</p>
      {err && <Notice tone="danger">{err}</Notice>}
      <div className="grid-2" style={{ alignItems: "start" }}>
        <Bands kind="calibration" bands={s.tolerance.calibration} onChange={(b) => set({ tolerance: { ...s.tolerance, calibration: b } })} />
        <Bands kind="pm" bands={s.tolerance.pm} onChange={(b) => set({ tolerance: { ...s.tolerance, pm: b } })} />
      </div>
      <CalendarEditor cal={s.calendar} onChange={(c) => set({ calendar: c })} />
      <EscalationEditor esc={s.escalation} onChange={(e) => set({ escalation: e })} />
      <section className="panel"><div className="panel-body stack">
        <label className="row"><span className="grow">{t("ws.dueSoon")}</span>
          <input className="input" type="number" min={1} max={180} style={{ width: 90 }} aria-label={t("ws.dueSoon")} value={s.due_soon_days}
            onChange={(e) => set({ due_soon_days: Number(e.target.value) || 30 })} /></label>
        <label className="row"><span className="grow">{t("ws.ratio")}</span>
          <input className="input" type="number" min={1} max={10} step={0.5} style={{ width: 90 }} aria-label={t("ws.ratio")} value={s.uncertainty_ratio_min}
            onChange={(e) => set({ uncertainty_ratio_min: Number(e.target.value) || 4 })} /></label>
        <label className="row"><span className="grow">{t("ws.quarantine")}</span>
          <Switch checked={s.quarantine_on_fail} onChange={(v) => set({ quarantine_on_fail: v })} label={t("ws.quarantine")} /></label>
      </div></section>
      <div><Button variant="primary" busy={busy} onClick={save}>{t("common.save")}</Button></div>
    </div>
  );
}
