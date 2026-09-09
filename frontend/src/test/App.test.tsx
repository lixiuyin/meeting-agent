import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { I18nProvider } from "../i18n/I18nProvider";

vi.mock("../hooks/useHealthCheck", () => ({
  useHealthCheck: () => true,
}));

vi.mock("../pages/HomePage", () => ({
  default: () => <div>New Chat</div>,
}));

vi.mock("../pages/MaterialsPage", () => ({
  default: () => <div>New Meeting</div>,
}));

vi.mock("../pages/HistoryPage", () => ({
  default: () => <div>History</div>,
}));

vi.mock("../pages/GenerationPage", () => ({
  default: () => <div>Generate</div>,
}));

vi.mock("../pages/MemoryPage", () => ({
  default: () => <div>Memory</div>,
}));

vi.mock("../views/SettingsView", () => ({
  default: () => <div>Settings</div>,
}));

function renderWithRouter(initialEntry = "/") {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[initialEntry]}>
        <App />
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe("App", () => {
  it("renders the header", async () => {
    renderWithRouter();
    expect(
      await screen.findByRole("heading", { level: 1, name: "Meeting Agent" }),
    ).toBeInTheDocument();
  });

  it("renders all navigation tabs", async () => {
    renderWithRouter();
    await screen.findByText("New Chat");
    expect(screen.getAllByText("Chat").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Materials").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("History").length).toBeGreaterThanOrEqual(1);
  });

  it("renders Home page by default with new chat button", async () => {
    renderWithRouter();
    expect(await screen.findByText("New Chat")).toBeInTheDocument();
  });

  it("renders Materials page with upload button", async () => {
    renderWithRouter("/materials");
    expect(await screen.findByText("New Meeting")).toBeInTheDocument();
  });

  it("renders History page heading", async () => {
    renderWithRouter("/history");
    const headings = await screen.findAllByText("History");
    expect(headings.length).toBeGreaterThanOrEqual(1);
  });
});
