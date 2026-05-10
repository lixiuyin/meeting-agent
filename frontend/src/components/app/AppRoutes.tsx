import { Suspense } from "react";
import { Routes, Route, useNavigate } from "react-router-dom";
import { Button, Result, Spin } from "antd";
import { LoadingOutlined } from "@ant-design/icons";
import { RouteErrorBoundary } from "./ErrorBoundaries";
import { lazyWithReload } from "../../utils/lazyWithReload";

const HomePage = lazyWithReload(() => import("../../pages/HomePage"));
const MaterialsPage = lazyWithReload(() => import("../../pages/MaterialsPage"));
const HistoryPage = lazyWithReload(() => import("../../pages/HistoryPage"));
const GenerationPage = lazyWithReload(() => import("../../pages/GenerationPage"));
const SettingsView = lazyWithReload(() => import("../../views/SettingsView"));
const MemoryPage = lazyWithReload(() => import("../../pages/MemoryPage"));

function PageLoading() {
  return (
    <div
      style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100%" }}
    >
      <Spin indicator={<LoadingOutlined spin />} size="large" />
    </div>
  );
}

export default function AppRoutes() {
  const navigate = useNavigate();

  return (
    <Suspense fallback={<PageLoading />}>
      <Routes>
        <Route
          path="/"
          element={
            <RouteErrorBoundary>
              <HomePage />
            </RouteErrorBoundary>
          }
        />
        <Route
          path="/generate"
          element={
            <RouteErrorBoundary>
              <GenerationPage />
            </RouteErrorBoundary>
          }
        />
        <Route
          path="/materials"
          element={
            <RouteErrorBoundary>
              <MaterialsPage />
            </RouteErrorBoundary>
          }
        />
        <Route
          path="/history"
          element={
            <RouteErrorBoundary>
              <HistoryPage />
            </RouteErrorBoundary>
          }
        />
        <Route
          path="/memory"
          element={
            <RouteErrorBoundary>
              <MemoryPage />
            </RouteErrorBoundary>
          }
        />
        <Route
          path="/settings"
          element={
            <RouteErrorBoundary>
              <SettingsView />
            </RouteErrorBoundary>
          }
        />
        <Route
          path="*"
          element={
            <Result
              status="404"
              title="404"
              subTitle="Page not found"
              extra={
                <Button type="primary" onClick={() => navigate("/")}>
                  Back to Home
                </Button>
              }
            />
          }
        />
      </Routes>
    </Suspense>
  );
}
