// Phase 6: "Ask ComMach" — questions answered from the records the user can see.
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { LuArrowUp, LuMessageSquarePlus, LuSearch, LuSparkles } from "react-icons/lu";
import { Button, LoadError, Notice, PageHead, errorText, useLoad } from "../components/ui";
import { get, post } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Link } from "../lib/router";
import type { StringKey } from "../lib/strings";

const TOOLS = ["search_assets", "asset", "tasks", "kpis", "drift", "intervals", "deviations", "impacts", "plans", "pm_gaps"];

interface LinkRef { type: "task" | "asset"; label: string; name?: string; href: string }
interface Msg { role: "user" | "assistant"; content: string; links?: LinkRef[]; lookups?: { tool: string }[]; error?: boolean }
interface Past { id: string; question: string; answer: string | null; error: string | null; asked_at: string }

/** A small, safe markdown subset: paragraphs, bullet and numbered lists, simple tables, **bold**, `code`. */
function Md({ text, links }: { text: string; links: LinkRef[] }) {
  const byLabel = new Map(links.map((l) => [l.label, l]));
  const inline = (s: string, key: string): ReactNode[] => {
    const parts = s.split(/(\*\*[^*]+\*\*|`[^`]+`|\b(?:CAL|PM)-\d{4}-\d{3,6}\b|\b[A-Z]{2,6}-[A-Z0-9-]{1,12}\b)/g);
    return parts.map((p, i) => {
      if (!p) return null;
      const k = `${key}-${i}`;
      if (p.startsWith("**") && p.endsWith("**")) return <strong key={k}>{inline(p.slice(2, -2), k)}</strong>;
      if (p.startsWith("`") && p.endsWith("`")) return <code key={k}>{p.slice(1, -1)}</code>;
      const l = byLabel.get(p);
      if (l) return <Link key={k} to={l.href} className="mono-id">{p}</Link>;
      return <Fragment key={k}>{p}</Fragment>;
    });
  };
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r/g, "").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (/^\s*\|/.test(line)) {
      const rows: string[][] = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) {
        if (!/^\s*\|[\s:|-]+\|\s*$/.test(lines[i])) rows.push(lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
        i++;
      }
      blocks.push(<div className="table-wrap" key={i}><table className="table compact"><thead><tr>{rows[0].map((c, j) => <th key={j}>{inline(c, `h${i}${j}`)}</th>)}</tr></thead>
        <tbody>{rows.slice(1).map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j}>{inline(c, `c${i}${ri}${j}`)}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    const ul = /^\s*[-*•]\s+/, ol = /^\s*\d+[.)]\s+/;
    if (ul.test(line) || ol.test(line)) {
      const ordered = ol.test(line);
      const items: string[] = [];
      while (i < lines.length && (ordered ? ol : ul).test(lines[i])) { items.push(lines[i].replace(ordered ? ol : ul, "")); i++; }
      const L = ordered ? "ol" : "ul";
      blocks.push(<L key={i}>{items.map((it, j) => <li key={j}>{inline(it, `l${i}${j}`)}</li>)}</L>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !ul.test(lines[i]) && !ol.test(lines[i]) && !/^\s*\|/.test(lines[i])) {
      para.push(lines[i].replace(/^#+\s*/, "")); i++;
    }
    blocks.push(<p key={i}>{inline(para.join(" "), `p${i}`)}</p>);
  }
  return <>{blocks}</>;
}

export function AskPage() {
  const { t, lang, dateTime } = useI18n();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const hist = useLoad(() => get<{ items: Past[] }>("/api/assistant/history"), []);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, busy]);

  const ask = async (question: string) => {
    const text = question.trim();
    if (text.length < 2 || busy) return;
    const history = msgs.filter((m) => !m.error).slice(-8).map((m) => ({ role: m.role, content: m.content }));
    setMsgs((m) => [...m, { role: "user", content: text }]);
    setQ(""); setBusy(true); setErr(null);
    try {
      const out = await post<{ answer: string; links: LinkRef[]; lookups: { tool: string }[] }>("/api/assistant/ask",
        { question: text, history, language: lang });
      setMsgs((m) => [...m, { role: "assistant", content: out.answer, links: out.links, lookups: out.lookups }]);
      hist.reload();
    } catch (e) {
      setErr(errorText(e, t));
    } finally { setBusy(false); }
  };
  const suggestions = (["ask.s1", "ask.s2", "ask.s3", "ask.s4"] as StringKey[]).map((k) => t(k));
  return (
    <>
      <PageHead title={t("ask.title")} lead={t("ask.lead")} actions={msgs.length > 0 &&
        <Button icon={<LuMessageSquarePlus />} onClick={() => { setMsgs([]); setErr(null); }}>{t("ask.clear")}</Button>} />
      <div className="cm-ask">
        <section className="panel cm-ask-chat">
          <div className="cm-ask-log" aria-live="polite">
            {msgs.length === 0 && (
              <div className="cm-ask-empty">
                <LuSparkles aria-hidden className="cm-ask-spark" />
                <div className="cm-ask-sugg">
                  {suggestions.map((s) => <button key={s} type="button" className="chip" onClick={() => ask(s)}>{s}</button>)}
                </div>
              </div>
            )}
            {msgs.map((m, i) => (
              <div key={i} className={`cm-ask-msg ${m.role}`}>
                {m.role === "user" ? <p>{m.content}</p> : (
                  <>
                    <div className="cm-ask-md"><Md text={m.content} links={m.links ?? []} /></div>
                    {(m.links?.length ?? 0) > 0 && (
                      <div className="cm-ask-links">
                        {m.links!.map((l) => <Link key={l.href} to={l.href} className="chip small">{l.label}{l.name ? ` · ${l.name}` : ""}</Link>)}
                      </div>
                    )}
                    {(m.lookups?.length ?? 0) > 0 && (
                      <p className="small subtle"><LuSearch aria-hidden /> {t("ask.looked", {
                        list: [...new Set(m.lookups!.map((x) => x.tool))].filter((x) => TOOLS.includes(x)).map((x) => t(`ask.tool.${x}` as StringKey)).join(", ") })}</p>
                    )}
                  </>
                )}
              </div>
            ))}
            {busy && <div className="cm-ask-msg assistant"><p className="muted cm-ask-wait">{t("ask.thinking")}</p></div>}
            {err && <Notice tone="danger">{err}</Notice>}
            <div ref={end} />
          </div>
          <form className="cm-ask-input" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
            <textarea className="input" rows={2} value={q} placeholder={t("ask.placeholder")} aria-label={t("ask.placeholder")} dir="auto"
              maxLength={1500} onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(q); } }} />
            <Button type="submit" variant="primary" icon={<LuArrowUp />} busy={busy} disabled={q.trim().length < 2}>{t("ask.send")}</Button>
          </form>
          <p className="small subtle cm-ask-note">{t("ask.disclaimer")}</p>
        </section>
        <aside className="panel cm-ask-hist">
          <div className="panel-head"><h3>{t("ask.history")}</h3></div>
          {hist.error ? <LoadError error={hist.error} onRetry={hist.reload} /> : !hist.data ? null :
            hist.data.items.length === 0 ? <p className="panel-body small muted">{t("ask.noHistory")}</p> : (
              <ul className="cm-ask-past">
                {hist.data.items.map((h) => (
                  <li key={h.id}>
                    <button type="button" className="link-btn" onClick={() => setMsgs([{ role: "user", content: h.question },
                      ...(h.answer ? [{ role: "assistant" as const, content: h.answer }] : [])])}>{h.question}</button>
                    <span className="small subtle">{dateTime(h.asked_at)}</span>
                  </li>
                ))}
              </ul>
            )}
        </aside>
      </div>
    </>
  );
}
