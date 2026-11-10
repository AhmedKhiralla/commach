// The bell in the top bar: unread count, the latest notifications, and a link to My tasks.
import { useCallback, useEffect, useState } from "react";
import { LuBell, LuCheckCheck } from "react-icons/lu";
import { get, post } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { navigate, usePath } from "../lib/router";
import { Menu } from "./ui";

interface Note { id: string; kind: string; level: number | null; title: string; body: string | null; link: string | null; created_at: string; read_at: string | null }

export function NotificationBell() {
  const { t, dateTime } = useI18n();
  const path = usePath();
  const [data, setData] = useState<{ items: Note[]; unread: number } | null>(null);
  const load = useCallback(() => { get<{ items: Note[]; unread: number }>("/api/notifications").then(setData).catch(() => {}); }, []);
  useEffect(() => {
    load();
    const h = setInterval(load, 60_000);
    return () => clearInterval(h);
  }, [load, path]);
  const unread = data?.unread ?? 0;
  const open = async (n: Note, close: () => void) => {
    close();
    if (!n.read_at) post("/api/notifications/read", { ids: [n.id] }).then(load).catch(() => {});
    if (n.link) navigate(n.link);
  };
  return (
    <Menu trigger={(p) => (
      <button type="button" className="icon-btn cm-bell" {...p} aria-label={`${t("nt.title")}${unread ? ` (${unread})` : ""}`}>
        <LuBell aria-hidden />{unread > 0 && <span className="cm-bell-n">{unread > 99 ? "99+" : unread}</span>}
      </button>
    )}>
      {(close) => (
        <div className="cm-notes" role="none">
          <div className="cm-notes-head">
            <strong>{t("nt.title")}</strong>
            {unread > 0 && <button type="button" className="link-btn small" onClick={() => post("/api/notifications/read", {}).then(load)}>
              <LuCheckCheck aria-hidden /> {t("nt.readAll")}</button>}
          </div>
          {!data?.items.length ? <div className="small subtle cm-notes-empty">{t("nt.empty")}</div> : (
            <ul>
              {data.items.slice(0, 20).map((n) => (
                <li key={n.id}>
                  <button type="button" role="menuitem" className={`menu-item cm-note ${n.read_at ? "" : "unread"} ${n.level ? `lv${n.level}` : ""}`} onClick={() => open(n, close)}>
                    <span className="cm-note-dot" aria-hidden />
                    <span className="grow">
                      <span className="cm-note-title">{n.title}</span>
                      {n.body && <span className="small subtle cm-note-body">{n.body}</span>}
                      <span className="small subtle">{n.level ? `${t("nt.level", { n: n.level })} · ` : ""}{dateTime(n.created_at)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="cm-notes-foot"><button type="button" className="link-btn small" onClick={() => { close(); navigate("/todo"); }}>{t("nav.myTasks")} →</button></div>
        </div>
      )}
    </Menu>
  );
}
