import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import {
  Layout,
  ConfigProvider,
  theme,
  Tabs,
  Badge,
  Button,
  Tooltip,
  Drawer,
  App as AntdApp,
} from "antd";
import {
  MessageOutlined,
  FolderOutlined,
  HistoryOutlined,
  SettingOutlined,
  MoonOutlined,
  SunOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  BranchesOutlined,
  MenuOutlined,
} from "@ant-design/icons";
import { motion, AnimatePresence, MotionConfig } from "framer-motion";
import { ThemeProvider, useTheme } from "./components/ThemeProvider";
import { useHealthCheck } from "./hooks/useHealthCheck";
import { initFileToken } from "./api/client";
import { ViewerProvider } from "./contexts/ViewerContext";
import { ChatProvider } from "./contexts/ChatContext";
import { ErrorBoundary } from "./components/app/ErrorBoundaries";
import AppRoutes from "./components/app/AppRoutes";
import { BREAKPOINT_COMPACT, BREAKPOINT_MOBILE } from "./constants/breakpoints";
import { useIntl } from "react-intl";

const { Header, Content } = Layout;

function getStyleNonce(): string {
  if (typeof document === "undefined") return "meeting-agent-style-nonce";
  const nonce = document.querySelector('meta[name="csp-style-nonce"]')?.getAttribute("content");
  return nonce?.trim() || "meeting-agent-style-nonce";
}

const TAB_ITEMS = [
  { key: "/", messageId: "nav.chat", icon: <MessageOutlined /> },
  { key: "/generate", messageId: "nav.generate", icon: <ThunderboltOutlined /> },
  { key: "/materials", messageId: "nav.materials", icon: <FolderOutlined /> },
  { key: "/history", messageId: "nav.history", icon: <HistoryOutlined /> },
  { key: "/memory", messageId: "nav.memory", icon: <BranchesOutlined /> },
  { key: "/settings", messageId: "nav.settings", icon: <SettingOutlined /> },
];

function AppLayout() {
  const { formatMessage } = useIntl();
  const navigate = useNavigate();
  const location = useLocation();
  const { theme: currentTheme, toggleTheme } = useTheme();
  const isDark = currentTheme === "dark";
  const isOnline = useHealthCheck();
  const styleNonce = getStyleNonce();

  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [isCompact, setIsCompact] = useState(false);
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    let rafId: number;
    const checkBreakpoints = () => {
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        setIsCompact(window.innerWidth <= BREAKPOINT_COMPACT);
        setIsMobile(window.innerWidth <= BREAKPOINT_MOBILE);
      });
    };
    checkBreakpoints();
    window.addEventListener("resize", checkBreakpoints);
    return () => {
      window.removeEventListener("resize", checkBreakpoints);
      cancelAnimationFrame(rafId);
    };
  }, []);

  const activeKey = TAB_ITEMS.find((t) => t.key === location.pathname)?.key ?? "/";

  const prefetchRoute = useCallback((path: string) => {
    const prefetchMap: Record<string, () => void> = {
      "/generate": () => import("./pages/GenerationPage"),
      "/materials": () => import("./pages/MaterialsPage"),
      "/history": () => import("./pages/HistoryPage"),
      "/memory": () => import("./pages/MemoryPage"),
      "/settings": () => import("./views/SettingsView"),
    };
    prefetchMap[path]?.();
  }, []);

  const tabItems = useMemo(
    () =>
      TAB_ITEMS.map((t) => ({
        key: t.key,
        label: (
          <span
            aria-label={formatMessage({ id: t.messageId })}
            style={{
              display: "flex",
              alignItems: "center",
              gap: isCompact ? 4 : 8,
              border: "none",
              background: "none",
              padding: 0,
              margin: 0,
              font: "inherit",
              color: "inherit",
            }}
          >
            {t.icon}
            {!isCompact && formatMessage({ id: t.messageId })}
          </span>
        ),
      })),
    [formatMessage, isCompact],
  );

  return (
    <ConfigProvider
      csp={{ nonce: styleNonce }}
      theme={{
        algorithm: isDark ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: {
          colorPrimary: "#4f46e5",
          colorSuccess: "#10b981",
          colorWarning: "#f59e0b",
          colorError: "#ef4444",
          colorInfo: "#3b82f6",
          borderRadius: 12,
          borderRadiusLG: 16,
          borderRadiusSM: 8,
          fontFamily:
            "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
        },
        components: {
          Card: {
            headerBg: "transparent",
          },
          Layout: {
            bodyBg: "var(--color-bg-primary)",
            headerBg: "transparent",
            siderBg: "var(--color-bg-surface)",
          },
          Button: {
            borderRadius: 10,
          },
          Input: {
            borderRadius: 10,
          },
          Select: {
            borderRadius: 10,
          },
          Tag: {
            borderRadius: 20,
          },
        },
      }}
    >
      <AntdApp>
        <MotionConfig reducedMotion="user">
          <ErrorBoundary>
            <a href="#main-content" className="skip-link">
              {formatMessage({ id: "nav.skipToMain" })}
            </a>
            <h1 className="sr-only">Meeting Agent</h1>
            <Layout style={{ minHeight: "100vh", background: "var(--color-bg-primary)" }}>
              <Header
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: `0 ${isMobile ? 12 : 24}px`,
                  height: 72,
                  borderBottom: "1px solid var(--color-border)",
                  position: "sticky",
                  top: 0,
                  zIndex: 100,
                  backdropFilter: "blur(20px)",
                  WebkitBackdropFilter: "blur(20px)",
                  background: isDark ? "rgba(10, 12, 16, 0.85)" : "rgba(248, 250, 252, 0.85)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: isCompact ? 12 : 24 }}>
                  {isMobile && (
                    <Button
                      type="text"
                      icon={<MenuOutlined />}
                      onClick={() => setMobileMenuOpen(true)}
                      aria-label={formatMessage({ id: "nav.openMenu" })}
                      style={{ color: "var(--color-text-secondary)", width: 40, height: 40 }}
                    />
                  )}

                  {/* Logo */}
                  <motion.button
                    type="button"
                    aria-label={formatMessage({ id: "nav.chat" })}
                    whileHover={{ scale: 1.05 }}
                    whileTap={{ scale: 0.98 }}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: isCompact ? 8 : 12,
                      cursor: "pointer",
                      height: 42,
                      border: 0,
                      padding: 0,
                      background: "transparent",
                      color: "inherit",
                    }}
                    onClick={() => navigate("/")}
                  >
                    <div
                      style={{
                        width: isCompact ? 36 : 42,
                        height: isCompact ? 36 : 42,
                        borderRadius: isCompact ? 12 : 14,
                        background: "var(--gradient-primary)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        color: "#fff",
                        fontSize: isCompact ? 18 : 20,
                        boxShadow: "var(--shadow-md), var(--glow-primary)",
                      }}
                    >
                      <RobotOutlined />
                    </div>
                    {!isMobile && (
                      <span
                        className="gradient-heading"
                        style={{
                          margin: 0,
                          fontSize: isCompact ? 16 : 20,
                          fontWeight: 700,
                          color: "var(--color-primary)",
                          background: "var(--gradient-primary)",
                          WebkitBackgroundClip: "text",
                          WebkitTextFillColor: "transparent",
                          backgroundClip: "text",
                          lineHeight: "42px",
                          whiteSpace: "nowrap",
                        }}
                      >
                        Meeting Agent
                      </span>
                    )}
                  </motion.button>

                  {!isMobile && (
                    <Tabs
                      className="app-navigation-tabs"
                      activeKey={activeKey}
                      // Route transitions may leave Tabs' controlled selection one render
                      // behind the URL. Every explicit tab activation must navigate.
                      onTabClick={(key) => navigate(key)}
                      onMouseOver={(event) => {
                        const tab = (event.target as HTMLElement).closest<HTMLElement>(
                          "[data-node-key]",
                        );
                        if (tab?.dataset.nodeKey) prefetchRoute(tab.dataset.nodeKey);
                      }}
                      onKeyDown={(event) => {
                        if (event.key !== "Enter" && event.key !== " ") return;
                        const tab = (event.target as HTMLElement).closest<HTMLElement>(
                          "[data-node-key]",
                        );
                        const path = tab?.dataset.nodeKey;
                        if (path) {
                          event.preventDefault();
                          navigate(path);
                        }
                      }}
                      items={tabItems}
                      style={{ marginBottom: 0, height: 42, display: "flex", alignItems: "center" }}
                      size={isCompact ? "small" : "middle"}
                    />
                  )}
                </div>

                {/* Right Actions */}
                <div style={{ display: "flex", alignItems: "center", gap: isCompact ? 8 : 16 }}>
                  <AnimatePresence mode="wait">
                    <motion.div
                      key={currentTheme}
                      initial={{ opacity: 0, scale: 0.8, rotate: -90 }}
                      animate={{ opacity: 1, scale: 1, rotate: 0 }}
                      exit={{ opacity: 0, scale: 0.8, rotate: 90 }}
                      transition={{ duration: 0.3, ease: "easeOut" }}
                    >
                      <Tooltip
                        title={formatMessage({ id: isDark ? "nav.lightMode" : "nav.darkMode" })}
                      >
                        <Button
                          type="text"
                          shape="circle"
                          size={isCompact ? "middle" : "large"}
                          icon={isDark ? <SunOutlined /> : <MoonOutlined />}
                          onClick={toggleTheme}
                          aria-label={formatMessage({
                            id: isDark ? "nav.lightMode" : "nav.darkMode",
                          })}
                          style={{
                            color: "var(--color-text-secondary)",
                            width: isCompact ? 34 : 40,
                            height: isCompact ? 34 : 40,
                          }}
                        />
                      </Tooltip>
                    </motion.div>
                  </AnimatePresence>

                  {!isMobile && (
                    <Badge
                      status={isOnline ? "success" : "error"}
                      text={
                        <span style={{ color: "var(--color-text-tertiary)", fontSize: 13 }}>
                          {formatMessage({ id: isOnline ? "nav.online" : "nav.offline" })}
                        </span>
                      }
                      style={{ marginLeft: 8 }}
                    />
                  )}
                </div>
              </Header>

              {/* Mobile navigation drawer */}
              <Drawer
                open={mobileMenuOpen}
                onClose={() => setMobileMenuOpen(false)}
                placement="left"
                size={260}
                styles={{ body: { padding: "12px 0" } }}
              >
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {TAB_ITEMS.map((t) => (
                    <Button
                      key={t.key}
                      type={activeKey === t.key ? "primary" : "text"}
                      size="large"
                      icon={t.icon}
                      block
                      aria-current={activeKey === t.key ? "page" : undefined}
                      style={{
                        justifyContent: "flex-start",
                        height: 48,
                        borderRadius: 8,
                        marginBottom: 4,
                      }}
                      onClick={() => {
                        navigate(t.key);
                        setMobileMenuOpen(false);
                      }}
                    >
                      {formatMessage({ id: t.messageId })}
                    </Button>
                  ))}
                </div>
              </Drawer>

              <Content
                id="main-content"
                tabIndex={-1}
                style={{
                  height: "calc(100vh - 72px)",
                  overflow: "auto",
                  background: "var(--color-bg-primary)",
                }}
              >
                <div style={{ height: "100%" }}>
                  <AppRoutes />
                </div>
              </Content>
            </Layout>
          </ErrorBoundary>
        </MotionConfig>
      </AntdApp>
    </ConfigProvider>
  );
}

export default function App() {
  useEffect(() => {
    // Asset-token warmup is best-effort and must never block the application
    // shell when the backend is slow or temporarily offline.
    void initFileToken();
  }, []);

  return (
    <ThemeProvider>
      <ChatProvider>
        <ViewerProvider>
          <AppLayout />
        </ViewerProvider>
      </ChatProvider>
    </ThemeProvider>
  );
}
