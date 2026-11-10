import { StrictMode, type ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { LuCompass, LuShieldCheck } from "react-icons/lu";
import "./styles.css";
import { Shell } from "./components/Shell";
import { Button, Empty, ToastProvider } from "./components/ui";
import { I18nProvider, useI18n } from "./lib/i18n";
import { match, navigate, usePath } from "./lib/router";
import { SessionProvider, useSession } from "./lib/session";
import { ThemeProvider } from "./lib/theme";
import { Audit } from "./pages/Audit";
import { ChangePasswordPage, ForcedPasswordChange } from "./pages/ChangePassword";
import { Companies } from "./pages/Companies";
import { Home } from "./pages/Home";
import { Login } from "./pages/Login";
import { Settings } from "./pages/Settings";
import { Sites } from "./pages/Sites";
import { Users } from "./pages/Users";
import { Logo } from "./components/Logo";
import { AssetPage, ScanCode } from "./pages/AssetPage";
import { Assets } from "./pages/Assets";
import { ImportPage } from "./pages/Import";
import { Classes, Departments, Vendors } from "./pages/Lists";
import { Locations } from "./pages/Locations";
import { CalibrationWork } from "./pages/Calibration";
import { ImpactPage, Impacts } from "./pages/Impacts";
import { TaskPage } from "./pages/TaskPage";
import { Templates } from "./pages/Templates";

function NotFound() {
  const { t } = useI18n();
  return <div className="panel"><Empty icon={<LuCompass />} title={t("notFound.title")} body={t("notFound.body")}
    action={<Button onClick={() => navigate("/")}>{t("notFound.home")}</Button>} /></div>;
}

function Forbidden() {
  const { t } = useI18n();
  return <div className="panel"><Empty icon={<LuShieldCheck />} title={t("forbidden.title")} body={t("forbidden.body")}
    action={<Button onClick={() => navigate("/")}>{t("notFound.home")}</Button>} /></div>;
}

function Routes() {
  const path = usePath();
  const { me } = useSession();
  if (!me) return null;
  const platform = me.user.is_platform_admin;
  const admin = me.user.is_tenant_admin;
  const gate = (ok: boolean, el: ReactElement) => (ok ? el : <Forbidden />);
  const tenantUser = !!me.tenant;
  let m: Record<string, string> | null;
  const canAudit = admin || platform || me.sites.some((s) => s.roles.includes("auditor") || s.roles.includes("qa_approver"));
  if ((m = match("/assets/:id", path))) return gate(tenantUser, <AssetPage key={m.id} assetId={m.id} />);
  if ((m = match("/calibration/tasks/:id", path))) return gate(tenantUser, <TaskPage key={m.id} taskId={m.id} />);
  if ((m = match("/calibration/impacts/:id", path))) return gate(tenantUser, <ImpactPage key={m.id} impactId={m.id} />);
  if ((m = match("/a/:code", path))) return gate(tenantUser, <ScanCode key={m.code} code={m.code} />);
  switch (path) {
    case "/assets": return gate(tenantUser, <Assets key={window.location.search.includes("new=1") ? "new" : "list"} />);
    case "/calibration": return gate(tenantUser, <CalibrationWork />);
    case "/calibration/templates": return gate(tenantUser, <Templates />);
    case "/calibration/impacts": return gate(tenantUser, <Impacts />);
    case "/import": return gate(tenantUser, <ImportPage />);
    case "/locations": return gate(tenantUser, <Locations />);
    case "/classes": return gate(tenantUser, <Classes />);
    case "/vendors": return gate(tenantUser, <Vendors />);
    case "/departments": return gate(tenantUser, <Departments />);
    case "/":
    case "/login": return <Home />;
    case "/companies": return gate(platform, <Companies />);
    case "/users": return gate(admin, <Users />);
    case "/sites": return gate(admin, <Sites />);
    case "/settings": return gate(admin, <Settings />);
    case "/audit": return gate(canAudit, <Audit />);
    case "/account/password": return <ChangePasswordPage />;
    default: return <NotFound />;
  }
}

function App() {
  const { status, me } = useSession();
  if (status === "loading") return <div className="boot" aria-busy="true"><Logo height={40} /></div>;
  if (status === "signed-out" || !me) return <Login />;
  if (me.user.must_change_password) return <ForcedPasswordChange />;
  return <Shell><Routes /></Shell>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <I18nProvider>
        <ToastProvider>
          <SessionProvider>
            <App />
          </SessionProvider>
        </ToastProvider>
      </I18nProvider>
    </ThemeProvider>
  </StrictMode>,
);
