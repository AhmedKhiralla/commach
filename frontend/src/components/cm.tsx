// ComMach building blocks: status badges, category icons, the e-signature dialog and register lookups.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  LuBuilding2, LuFactory, LuFan, LuGauge, LuMicroscope, LuScale, LuShieldAlert, LuSignature, LuThermometer, LuWrench,
} from "react-icons/lu";
import { get, post } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import type { StringKey } from "../lib/strings";
import type {
  AssetClass, AssetRow, AssetStatus, Category, Department, Line, Location, LocationKind, Vendor,
} from "../lib/types";
import { Badge, Button, Notice, PasswordField, Sheet, TextField, errorText, fieldErrors, useToast } from "./ui";

const STATUS_TONE: Record<AssetStatus, "ok" | "warn" | "danger" | "brand" | undefined> = {
  draft: undefined, in_service: "ok", restricted: "warn", standby: "warn", out_of_service: "danger",
  quarantined: "danger", out_for_calibration: "warn", retired: undefined, disposed: undefined, transferred: undefined,
};

export function StatusBadge({ status }: { status: AssetStatus }) {
  const { t } = useI18n();
  return <Badge tone={STATUS_TONE[status]} dot>{t(`status.${status}` as StringKey)}</Badge>;
}

const CAT_ICON: Record<Category, ReactNode> = {
  equipment: <LuFactory />, instrument: <LuMicroscope />, gauge: <LuGauge />, sensor: <LuThermometer />, tool: <LuWrench />,
  reference_standard: <LuScale />, utility: <LuFan />, facility: <LuBuilding2 />, safety: <LuShieldAlert />,
};

export function CategoryIcon({ category }: { category: Category }) {
  return <span className={`cm-cat cat-${category}`} aria-hidden>{CAT_ICON[category]}</span>;
}

export function Programmes({ cal, pm }: { cal: boolean; pm: boolean }) {
  const { t } = useI18n();
  return (
    <span className="cm-prog">
      <span className={cal ? "on" : "off"} title={t("af.calibration_required")}>{t("assets.cal")}</span>
      <span className={pm ? "on" : "off"} title={t("af.pm_required")}>{t("assets.pm")}</span>
    </span>
  );
}

/** Flatten a site's locations into indented select options (building > area > room). */
export function locationOptions(locs: Location[], lang: string, exclude?: string) {
  const byParent = new Map<string | null, Location[]>();
  locs.forEach((l) => byParent.set(l.parent_id, [...(byParent.get(l.parent_id) ?? []), l]));
  const out: { value: string; label: string; kind: LocationKind }[] = [];
  const walk = (parent: string | null, depth: number) => {
    (byParent.get(parent) ?? []).sort((a, b) => a.code.localeCompare(b.code)).forEach((l) => {
      if (l.id === exclude) return;
      const name = lang === "ar" && l.name_ar ? l.name_ar : l.name_en;
      out.push({ value: l.id, label: `${"   ".repeat(depth)}${l.code} · ${name}`, kind: l.kind });
      walk(l.id, depth + 1);
    });
  };
  walk(null, 0);
  return out;
}

export interface Lookups {
  classes: AssetClass[]; departments: Department[]; vendors: Vendor[]; locations: Location[]; lines: Line[];
  assets: AssetRow[]; loading: boolean;
}

/** Everything the asset form needs for one site. */
export function useLookups(siteId: string | null): Lookups {
  const [data, setData] = useState<Lookups>({ classes: [], departments: [], vendors: [], locations: [], lines: [], assets: [], loading: true });
  useEffect(() => {
    let live = true;
    (async () => {
      const [c, d, v] = await Promise.all([
        get<{ items: AssetClass[] }>("/api/asset-classes"), get<{ items: Department[] }>("/api/departments"),
        get<{ items: Vendor[] }>("/api/vendors")]);
      let locations: Location[] = [], lines: Line[] = [], assets: AssetRow[] = [];
      if (siteId) {
        const [l, ln, a] = await Promise.all([
          get<{ items: Location[] }>(`/api/sites/${siteId}/locations`), get<{ items: Line[] }>(`/api/sites/${siteId}/lines`),
          get<{ items: AssetRow[] }>(`/api/assets?site_id=${siteId}&page_size=500&sort=code`)]);
        locations = l.items; lines = ln.items; assets = a.items;
      }
      if (live) setData({ classes: c.items, departments: d.items, vendors: v.items, locations, lines, assets, loading: false });
    })().catch(() => { if (live) setData((x) => ({ ...x, loading: false })); });
    return () => { live = false; };
  }, [siteId]);
  return data;
}

// ------------------------------------------------------------------ signing a status change
export function SignStatus({ assets, onClose, onDone, initial }: {
  assets: { id: string; code: string; status: AssetStatus }[]; onClose: () => void; onDone: () => void; initial?: AssetStatus;
}) {
  const { t, tx } = useI18n();
  const { me } = useSession();
  const toast = useToast();
  const [options, setOptions] = useState<AssetStatus[] | null>(null);
  const [status, setStatus] = useState<AssetStatus | "">("");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [email, setEmail] = useState(me?.user.email ?? "");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<string[]>([]);
  const ids = assets.map((a) => a.id).join(",");
  useEffect(() => {
    get<{ statuses: AssetStatus[] }>(`/api/assets/status-options?ids=${ids}`).then((r) => {
      setOptions(r.statuses);
      if (r.statuses.length === 1) setStatus(r.statuses[0]);
      else if (initial && r.statuses.includes(initial)) setStatus(initial);
    }).catch((e) => { setOptions([]); setErr(errorText(e, t)); });
  }, [ids]); // eslint-disable-line react-hooks/exhaustive-deps
  const meaning = useMemo(() => {
    if (!status) return null;
    const releasing = status === "in_service" && assets.every((a) => a.status === "quarantined");
    return releasing ? "release" : status;
  }, [status, assets]);
  const submit = async () => {
    if (!status) return;
    setBusy(true); setErr(null); setErrs({}); setProblems([]);
    try {
      const r = await post<{ changed: unknown[] }>("/api/assets/status", { asset_ids: assets.map((a) => a.id), status, reason, note: note || null, email, password });
      toast(t("sign.done", { n: r.changed.length, status: t(`status.${status}` as StringKey) }));
      onDone(); onClose();
    } catch (e) {
      const ex = e as { extra?: { problems?: { code: string; problem: string; from: string }[] } };
      setErrs(fieldErrors(e)); setErr(errorText(e, t)); setPassword("");
      setProblems((ex.extra?.problems ?? []).map((p) => t(p.problem === "role" ? "sign.problem.role" : "sign.problem.transition",
        { code: p.code, from: t(`status.${p.from}` as StringKey) })));
    } finally { setBusy(false); }
  };
  const title = assets.length === 1 ? `${t("sign.title")} · ${assets[0].code}` : `${t("sign.title")} · ${t("sign.assets", { n: assets.length })}`;
  return (
    <Sheet title={title} lead={t("sign.lead")} onClose={onClose} onSubmit={submit}
      footer={<>
        <Button onClick={onClose}>{t("common.cancel")}</Button>
        <Button type="submit" variant="primary" icon={<LuSignature />} busy={busy}
          disabled={!status || reason.trim().length < 3 || !email || !password}>{t("sign.submit")}</Button>
      </>}>
      {err && <Notice tone="danger">{err}{problems.length > 0 && <ul className="plain-list small">{problems.map((p) => <li key={p}>{p}</li>)}</ul>}</Notice>}
      {options && options.length === 0 && !err && <Notice tone="warn">{t("sign.none")}</Notice>}
      {assets.length > 1 && <div className="chip-row">{assets.slice(0, 40).map((a) => <span key={a.id} className="chip mono-id">{a.code}</span>)}
        {assets.length > 40 && <span className="chip">+{assets.length - 40}</span>}</div>}
      <div className="field">
        <span className="field-label">{t("sign.to")}</span>
        <div className="cm-status-pick" role="radiogroup" aria-label={t("sign.to")}>
          {(options ?? []).map((s) => (
            <label key={s} className={status === s ? "on" : ""}>
              <input type="radio" name="to" value={s} checked={status === s} onChange={() => setStatus(s)} />
              <StatusBadge status={s} />
            </label>
          ))}
        </div>
      </div>
      <TextField label={t("sign.reason")} hint={t("sign.reasonHint")} value={reason} onChange={setReason} maxLength={500} error={errs.reason} required />
      <TextField label={t("sign.note")} hint={t("sign.noteHint")} value={note} onChange={setNote} maxLength={1000} />
      {meaning && (
        <div className="sign-statement">
          <span className="small muted">{t("sign.statement")}</span>
          <p>“{tx(`stmt.${meaning}`, meaning)}”</p>
        </div>
      )}
      <div className="grid-2">
        <TextField label={t("sign.email")} type="email" dir="ltr" autoComplete="username" value={email} onChange={setEmail} />
        <PasswordField label={t("sign.password")} value={password} onChange={setPassword} autoComplete="current-password" />
      </div>
      <p className="small subtle">{t("sign.legal")}</p>
    </Sheet>
  );
}

export function Kpi({ label, value, tone, hint }: { label: string; value: ReactNode; tone?: "ok" | "warn" | "danger" | "brand"; hint?: string }) {
  return (
    <div className={`cm-kpi ${tone ?? ""}`} title={hint}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}
