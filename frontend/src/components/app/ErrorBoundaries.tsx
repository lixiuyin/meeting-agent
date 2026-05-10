import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, Result, Alert } from "antd";
import { RobotOutlined } from "@ant-design/icons";
import { motion } from "framer-motion";
import * as Sentry from "@sentry/react";
import { ApiError } from "../../api/client";

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
  requestId: string | null;
}

function extractRequestId(error: Error | null): string | null {
  if (!error) return null;
  if (error instanceof ApiError && error.requestId) {
    return error.requestId;
  }
  const match = error.message.match(/Request ID:\s*([A-Za-z0-9._:-]+)/i);
  return match?.[1] ?? null;
}

export class RouteErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, error: null, requestId: null };

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error, requestId: extractRequestId(error) };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    if (import.meta.env.PROD) {
      Sentry.captureException(error, {
        contexts: { react: { componentStack: errorInfo.componentStack } },
      });
    } else {
      console.error("RouteErrorBoundary caught an error:", error, errorInfo);
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 40, textAlign: "center" }}>
          <Result
            status="error"
            title="Something went wrong"
            subTitle={
              this.state.requestId
                ? `${this.state.error?.message || "An unexpected error occurred"} (Request ID: ${this.state.requestId})`
                : this.state.error?.message || "An unexpected error occurred"
            }
            extra={
              <Button
                type="primary"
                onClick={() => this.setState({ hasError: false, error: null, requestId: null })}
              >
                Try Again
              </Button>
            }
          />
        </div>
      );
    }
    return this.props.children;
  }
}

export class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, error: null, requestId: null };

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error, requestId: extractRequestId(error) };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    if (import.meta.env.PROD) {
      Sentry.captureException(error, {
        contexts: { react: { componentStack: errorInfo.componentStack } },
      });
    } else {
      console.error("ErrorBoundary caught an error:", error, errorInfo);
    }
  }

  private copyRequestId = async () => {
    const requestId = this.state.requestId;
    if (!requestId) return;
    try {
      await navigator.clipboard.writeText(requestId);
    } catch {
      // Non-fatal: keep the fallback UI usable.
    }
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            minHeight: "100vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "var(--color-bg-primary)",
            padding: 24,
          }}
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.3 }}
          >
            <Result
              status="error"
              icon={
                <div
                  style={{
                    width: 72,
                    height: 72,
                    borderRadius: 20,
                    background: "rgba(239, 68, 68, 0.1)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    margin: "0 auto 16px",
                    fontSize: 36,
                  }}
                  aria-hidden="true"
                >
                  <RobotOutlined style={{ color: "#ef4444" }} />
                </div>
              }
              title="Something went wrong"
              subTitle={this.state.error?.message || "An unexpected error occurred"}
              extra={
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {this.state.requestId && (
                    <div
                      style={{
                        fontSize: 12,
                        color: "var(--color-text-muted)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        gap: 8,
                      }}
                    >
                      <span>Request ID: {this.state.requestId}</span>
                      <Button size="small" onClick={() => void this.copyRequestId()}>
                        Copy
                      </Button>
                    </div>
                  )}
                  <Button
                    type="primary"
                    size="large"
                    onClick={() => this.setState({ hasError: false, error: null, requestId: null })}
                    style={{
                      borderRadius: 12,
                      height: 44,
                      padding: "0 28px",
                    }}
                  >
                    Try Again
                  </Button>
                </div>
              }
            />
          </motion.div>
        </div>
      );
    }
    return this.props.children;
  }
}

interface FeatureErrorBoundaryProps {
  children: ReactNode;
  featureName?: string;
}

interface FeatureErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class FeatureErrorBoundary extends Component<
  FeatureErrorBoundaryProps,
  FeatureErrorBoundaryState
> {
  state: FeatureErrorBoundaryState = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): FeatureErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    if (import.meta.env.PROD) {
      Sentry.captureException(error, {
        contexts: { react: { componentStack: errorInfo.componentStack } },
        tags: { feature: this.props.featureName || "unknown" },
      });
    } else {
      console.error(
        `FeatureErrorBoundary[${this.props.featureName || "unknown"}] caught:`,
        error,
        errorInfo,
      );
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 16 }}>
          <Alert
            type="error"
            title={this.props.featureName ? `${this.props.featureName} Error` : "Component Error"}
            description={this.state.error?.message || "An unexpected error occurred"}
            showIcon
            action={
              <Button
                size="small"
                danger
                onClick={() => this.setState({ hasError: false, error: null })}
              >
                Retry
              </Button>
            }
          />
        </div>
      );
    }
    return this.props.children;
  }
}
