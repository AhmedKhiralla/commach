// Phase 5: print a record on the company's approved form. Every print is a numbered controlled copy, so the
// button fetches the file (to show a clear message when no approved form exists) and then opens or downloads it.
import { useState } from "react";
import { LuPrinter } from "react-icons/lu";
import { qs } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Button } from "./ui";

export type FormSource = "calibration_plan" | "pm_plan" | "calibration_record" | "pm_record" | "history_card" | "status_label";

/** Fetch a form endpoint and hand the file to the user: PDFs open in a new tab, Word files download. */
export async function fetchForm(url: string, init?: RequestInit, win?: Window | null): Promise<{ number: string | null }> {
  const res = await fetch(url, { credentials: "same-origin", ...init,
    headers: { "X-Requested-With": "commach", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...(init?.headers ?? {}) } });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const e = new Error(body?.error?.message ?? res.statusText) as Error & { code?: string };
    e.code = body?.error?.code;
    throw e;
  }
  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  const disp = res.headers.get("Content-Disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disp)?.[1] ?? "form";
  if (blob.type === "application/pdf") {
    if (win && !win.closed) win.location.href = href;
    else window.open(href, "_blank", "noopener");
  } else {
    win?.close();
    const a = document.createElement("a");
    a.href = href; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
  }
  setTimeout(() => URL.revokeObjectURL(href), 60_000);
  return { number: res.headers.get("X-Copy-Number") };
}

/** Print button used on the task, plan and asset pages. */
export function PrintFormButton({ source, params, label, size, variant }: {
  source: FormSource; params: Record<string, string | number | undefined | null>; label?: string;
  size?: "sm" | "md"; variant?: "default" | "primary" | "ghost";
}) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    setBusy(true); setErr(null);
    const win = window.open("", "_blank");      // opened now so the browser does not block it as a pop-up
    try {
      await fetchForm(`/api/forms/print${qs({ source, ...params })}`, undefined, win);
    } catch (e) {
      win?.close();
      const ex = e as Error & { code?: string };
      setErr(ex.code === "no_form" ? t("fm.noApproved") : ex.message);
    } finally { setBusy(false); }
  };
  return (
    <span className="cm-print">
      <Button size={size} variant={variant} icon={<LuPrinter />} busy={busy} onClick={go}>{label ?? t("fm.print")}</Button>
      {err && <span className="small cm-print-err" role="alert">{err}</span>}
    </span>
  );
}
