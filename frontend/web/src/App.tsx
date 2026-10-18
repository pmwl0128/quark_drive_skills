import { EscapeLayers } from "./components/EscapeLayers.tsx";
import "./components/build/styles.fields.css";
import { Component, Suspense, lazy, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { BRAND_LOGO } from "./assets/icons.ts";
import { PageRailProvider, SearchEntry, useSiteSearch } from "./components/SideRail.tsx";
import { LibraryDockButton } from "./components/LibraryDockButton.tsx";
import { SiteFooter } from "./components/SiteFooter.tsx";
import { ThemeToggle } from "./components/ThemeToggle.tsx";
import { AdaptiveSelect } from "./components/AdaptiveSelect.tsx";
import { useThemePreference, type ThemePreference } from "./lib/theme.ts";
import { LibraryWorkspaceProvider, useLibraryWorkspace } from "./lib/library/workspace.tsx";
import { LangContext, detectLang, saveLang, useLang, useT, type Lang } from "./i18n.ts";
import { HomePage } from "./pages/HomePage.tsx";
import { RuntimeProvider, useRuntime } from "./runtime/context.tsx";
import { ToastProvider } from "./components/Toast.tsx";
import { preloadable } from "./lib/preloadable.ts";
import { StorageFailureNotice } from "./components/StorageFailureNotice.tsx";

// Keep the home launchpad in the entry chunk and split every other surface. Navigation intent
// preloads the relevant module, so desktop hover/keyboard focus hides the network boundary while a
// direct URL still downloads only the page it needs.
const loadRankingPage = () => import("./pages/RankingPage.tsx");
const loadDexPage = () => import("./pages/DexPage.tsx");
const loadPokemonPage = () => import("./pages/PokemonPage.tsx");
const loadMetaPage = () => import("./pages/MetaPage.tsx");
const loadMatchupPage = () => import("./pages/MatchupPage.tsx");
const loadCalcPage = () => import("./pages/CalcPage.tsx");
const loadAssistantPage = () => import("./pages/AssistantPage.tsx");
const loadTeamsPage = () => import("./pages/TeamsPage.tsx");
const loadUepPages = () => import("./pages/uep/index.ts");
const LocalHomePage = lazy(() => import("./pages/LocalHomePage.tsx").then(m => ({ default: m.LocalHomePage })));
// Only a runtime with a session feed can lose it; the online entry never loads this.
const ConnectionNotice = lazy(() => import("./components/session/ConnectionNotice.tsx").then(m => ({ default: m.ConnectionNotice })));

const RankingPage = lazy(() => loadRankingPage().then((m) => ({ default: m.RankingPage })));
const DexPage = lazy(() => loadDexPage().then((m) => ({ default: m.DexPage })));
const PokemonPage = lazy(() => loadPokemonPage().then((m) => ({ default: m.PokemonPage })));
const MetaPage = lazy(() => loadMetaPage().then((m) => ({ default: m.MetaPage })));
const MatchupPage = lazy(() => loadMatchupPage().then((m) => ({ default: m.MatchupPage })));
const CalcPage = lazy(() => loadCalcPage().then((m) => ({ default: m.CalcPage })));
const AssistantPage = lazy(() => loadAssistantPage().then((m) => ({ default: m.AssistantPage })));
const TeamsPage = lazy(() => loadTeamsPage().then((m) => ({ default: m.TeamsPage })));
// The environment search pages without their own fall back to, and the library dock (with the storage
// code under it), are fetched on intent — a hover or focus of their entry — and open without a wait.
const MetaSearch = preloadable(() => import("./components/MetaSearch.tsx").then((m) => ({ default: m.MetaSearch })));
const LibraryDock = preloadable(() => import("./components/library/LibraryDock.tsx")
  .then((m) => ({ default: m.LibraryDock })));
// Both local UEP pages intentionally share one chunk; online runtimes never request it.
const SessionsPage = lazy(() => loadUepPages().then((m) => ({ default: m.SessionsPage })));
const SessionPage = lazy(() => loadUepPages().then((m) => ({ default: m.SessionPage })));

function NavItem({ to, label, preload, end = false }: {
  to: string;
  label: string;
  preload?: () => Promise<unknown>;
  end?: boolean;
}) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => (isActive ? "active" : "")}
      onPointerEnter={preload} onFocus={preload}>
      {label}
    </NavLink>
  );
}

function RouteLoading() {
  const t = useT();
  return <div className="spinner route-loading" role="status" aria-live="polite">{t("state.loading")}</div>;
}

function PageCrashFallback() {
  const t = useT();
  return (
    <main className="content" role="alert">
      <section className="panel route-missing">
        <h1>{t("state.pageCrashTitle")}</h1>
        <p>{t("state.pageCrashBody")}</p>
        <button type="button" className="primary-btn" onClick={() => window.location.reload()}>
          {t("state.reload")}
        </button>
      </section>
    </main>
  );
}

class AppErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error) {
    console.error("UI render failed", error);
  }

  render() {
    return this.state.failed ? <PageCrashFallback /> : this.props.children;
  }
}

/** A route change starts at the top — unless the link names a section of the page (`/#home-install`),
 * which is then scrolled into view once it has rendered (a page may still be loading its parts). */
function RouteScrollReset() {
  const { pathname, hash } = useLocation();
  const landing = useRef(true);
  useEffect(() => {
    const first = landing.current;
    landing.current = false;
    const id = hash ? decodeURIComponent(hash.slice(1)) : "";
    if (!id) {
      // Only a move between pages resets: a fresh load already starts at the top (or where the browser
      // restores it), and scrolling there would force the first page's layout inside the render.
      if (!first) window.scrollTo({ top: 0, left: 0 });
      return;
    }
    // Parts above the section may still be loading and push it down: align again a few times while
    // the page settles, and stop the moment the reader scrolls on their own.
    let frame = 0;
    let tries = 0;
    let stopped = false;
    const timers: number[] = [];
    const stop = () => { stopped = true; };
    const align = () => { if (!stopped) document.getElementById(id)?.scrollIntoView({ block: "start" }); };
    const seek = () => {
      if (document.getElementById(id)) {
        align();
        for (const ms of [250, 600, 1200, 2000]) timers.push(window.setTimeout(align, ms));
      } else if (tries++ < 60) frame = requestAnimationFrame(seek);
      else window.scrollTo({ top: 0, left: 0 });
    };
    const events = ["wheel", "touchstart", "keydown"] as const;
    for (const name of events) window.addEventListener(name, stop, { passive: true });
    seek();
    return () => {
      cancelAnimationFrame(frame);
      for (const timer of timers) window.clearTimeout(timer);
      for (const name of events) window.removeEventListener(name, stop);
    };
  }, [pathname, hash]);
  return null;
}

function MissingRoute() {
  const t = useT();
  return (
    <section className="panel route-missing" role="status">
      <h1>{t("route.missingTitle")}</h1>
      <p>{t("route.missingBody")}</p>
      <NavLink className="primary-btn" to="/">{t("route.home")}</NavLink>
    </section>
  );
}

/** The site search on a page without its own. */
function FallbackSearch() {
  const { slot, fallbackOpen } = useSiteSearch();
  if (slot || !fallbackOpen) return null;
  return <MetaSearch />;
}

/** The dock, once opened: the shell owns it, so it stays open across pages. */
function LibraryDockLayer() {
  const { open } = useLibraryWorkspace("open");
  if (!open) return null;
  return <LibraryDock />;
}

function Shell({ theme, setTheme }: { theme: ThemePreference; setTheme: (theme: ThemePreference) => void }) {
  const topbar = useRef<HTMLElement>(null);
  useEffect(() => {
    const node = topbar.current;
    if (!node) return;
    // The observer reports the bar's height from the browser's own layout pass, before that frame is
    // painted, so nothing forces a layout here; the value is written only when it differs from the
    // stylesheet's (styles.css :root), which spares the whole page a second style pass.
    const root = document.documentElement;
    const observer = new ResizeObserver(([entry]) => {
      const height = entry?.borderBoxSize?.[0]?.blockSize ?? node.getBoundingClientRect().height;
      const value = `${Math.ceil(height)}px`;
      if (getComputedStyle(root).getPropertyValue("--topbar-offset").trim() !== value) root.style.setProperty("--topbar-offset", value);
    });
    observer.observe(node);
    return () => { observer.disconnect(); root.style.removeProperty("--topbar-offset"); };
  }, []);
  const { adapter, capabilities, can } = useRuntime();
  const t = useT();
  const { lang, setLang } = useLang();
  const showAssistant = (can("team.validate") && !!adapter.diagnose)
    || (can("llm.qa") && !!adapter.qa)
    || (can("llm.builder") && !!adapter.builder);
  return (
    <div className="shell">
      <EscapeLayers />
      <RouteScrollReset />
      <a className="skip-link" href="#main-content">{t("a11y.skipContent")}</a>
      <header ref={topbar} className="topbar">
        {/* First in the bar, on every page: the site search (components/SideRail.tsx). */}
        <SearchEntry onFallbackIntent={() => void MetaSearch.preload().catch(() => {})} />
        <Link className="logo" to="/" aria-label={t("nav.home")}>
          <img src={BRAND_LOGO} alt="" aria-hidden />
          <span className="logo-text">Quark Drive{" "}<span className="logo-sub">{adapter.runtime === "local" ? "Console" : "Web"}</span></span>
        </Link>
        <nav aria-label={t("a11y.primaryNav")}>
          <NavItem to="/ranking" label={t("nav.ranking")} preload={loadRankingPage} />
          <NavItem to="/dex" label={t("nav.dex")} preload={loadDexPage} />
          {can("calc.damage") && (
            <NavItem to="/calc" label={t("nav.calc")} preload={loadCalcPage} />
          )}
          {can("team.matchup") && (
            <NavItem to="/matchup" label={t("nav.matchup")} preload={loadMatchupPage} />
          )}
          {showAssistant && (
            <NavItem to="/assist" label={t("nav.assistant")} preload={loadAssistantPage} />
          )}
          {/* The library is the browser's own, so both runtimes have this page. */}
          <NavItem to="/teams" label={t("nav.teams")} preload={loadTeamsPage} />
          {can("team.uep") && (
            <NavItem to="/sessions" label={t("nav.sessions")} preload={loadUepPages} />
          )}
        </nav>
        <span className="env num" title={capabilities.deploymentId}>
          <span>{capabilities.environment.season} · {capabilities.environment.rule}</span>
          {capabilities.environment.asOf && (
            <span className="env-date"> · {capabilities.environment.asOf}</span>
          )}
        </span>
        <AdaptiveSelect value={lang} onValueChange={(value) => setLang(value as Lang)} aria-label={t("a11y.language")}>
          <option value="zh">中文</option>
          <option value="en">English</option>
          <option value="ja">日本語</option>
        </AdaptiveSelect>
        <ThemeToggle theme={theme} onChange={setTheme} />
        <LibraryDockButton preload={() => void LibraryDock.preload().catch(() => {})} />
      </header>
      <LibraryDockLayer />
      <FallbackSearch />
      <main id="main-content" className="content" tabIndex={-1}>
        <StorageFailureNotice />
        {can("team.uep") && adapter.sessions ? <Suspense fallback={null}><ConnectionNotice /></Suspense> : null}
        <Suspense fallback={<RouteLoading />}>
          <Routes>
          <Route path="/" element={adapter.runtime === "local" ? <LocalHomePage /> : <HomePage />} />
          <Route path="/ranking" element={<RankingPage />} />
          <Route path="/dex" element={<DexPage />} />
          <Route path="/pokemon/:slug" element={<PokemonPage />} />
          <Route path="/meta/:slug" element={<MetaPage />} />
          <Route path="/trend" element={<Navigate to="/ranking?view=trend" replace />} />
          <Route path="/matchup" element={<MatchupPage />} />
          <Route path="/calc" element={<CalcPage />} />
          <Route path="/teams" element={<TeamsPage />} />
          {showAssistant && (
            <Route path="/assist" element={<AssistantPage />} />
          )}
          {can("llm.qa") && <Route path="/qa" element={<Navigate to="/assist" replace />} />}
          {can("llm.builder") && (
            <Route path="/builder" element={<Navigate to="/assist?tab=wizard" replace />} />
          )}
          {can("team.uep") && (
            <>
              <Route path="/sessions" element={<SessionsPage />} />
              <Route path="/sessions/:id" element={<SessionPage />} />
            </>
          )}
          <Route path="*" element={<MissingRoute />} />
          </Routes>
        </Suspense>
      </main>
      <SiteFooter />
    </div>
  );
}

/** App-level objects every page can reach: the page rail slot and the library workspace. */
function Workspace({ children }: { children: ReactNode }) {
  return (
    <PageRailProvider>
      <LibraryWorkspaceProvider>{children}</LibraryWorkspaceProvider>
    </PageRailProvider>
  );
}

// Lang state lives above RuntimeProvider so error screens localize too.
export function App() {
  const [lang, setLangState] = useState<Lang>(detectLang());
  const [theme, setTheme] = useThemePreference();
  useEffect(() => {
    document.documentElement.lang = { zh: "zh-CN", en: "en", ja: "ja" }[lang];
  }, [lang]);
  const setLang = (l: Lang) => {
    saveLang(l);
    setLangState(l);
  };
  return (
    <LangContext.Provider value={{ lang, setLang }}>
      <AppErrorBoundary>
        <RuntimeProvider>
          <ToastProvider>
            <Workspace>
              <Shell theme={theme} setTheme={setTheme} />
            </Workspace>
          </ToastProvider>
        </RuntimeProvider>
      </AppErrorBoundary>
    </LangContext.Provider>
  );
}
