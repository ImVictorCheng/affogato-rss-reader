import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const entry = {
  id: 1, title: "A general research article", translated_title: "一篇通用研究文章",
  summary: "Original summary.", translated_summary: "中文摘要。", url: "https://example.test/one",
  authors: ["Alice"], categories: ["science"], published_at: "2026-07-26T02:00:00Z",
  feed_titles: ["Example Science"], feed_ids: [1], state: { read: false, starred: false, later: false, archived: false },
  tags: [], domains: [{ id: 3, name: "Science", description: "", color: null, position: 0, feed_count: 1, entry_count: 1 }],
};
const theme = {
  id: "builtin-quantum",
  label: "Quantum physics",
  accent: "#16a6a1",
  secondary: "#8568df",
  nav: "#091329",
  paper: "#f5f8fc",
  surface: "#ffffff",
  ink: "#182237",
  density: "compact",
  typography: "technical",
  motif: "orbit",
  source: "builtin",
  identity: {
    name: "Quantum Physics Digest",
    source: "builtin",
    logo_kind: "generated",
    primary_template: "quantum-physics",
  },
};

function response(body: unknown) { return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } })); }
function mockApi(authenticated = true, onboardingRequired = false, updateAvailable = false) {
  let entryRead = false;
  const currentEntry = () => ({ ...entry, state: { ...entry.state, read: entryRead } });
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/auth/status")) return response({ setup_required: !authenticated, authenticated, onboarding_required: onboardingRequired, mode: "owner", csrf_token: "csrf", theme });
    if (url.endsWith("/auth/setup")) return response({ setup_required: false, authenticated: true, onboarding_required: true, mode: "owner", csrf_token: "csrf2" });
    if (url.endsWith("/onboarding") && init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      return response({ completed: true, selected_domains: body.selected_domains, primary_domain: body.primary_domain, theme: body.theme, ai_personalized: false });
    }
    if (url.endsWith("/onboarding")) return response({ completed: true, selected_domains: ["Quantum physics"], primary_domain: "Quantum physics", theme, ai_personalized: false });
    if (url.endsWith("/translations/status")) return response({ enabled: false, target_language: "zh-CN", provider: "google_gtx", provider_healthy: true, pending_count: 2, running_count: 1, completed_count: 3, failed_count: 4 });
    if (url.endsWith("/llm/connections")) return response([]);
    if (url.endsWith("/briefs/configuration")) return response({ llm_connection_id: null, llm_connection_name: null, model: null, configured: false });
    if (url.endsWith("/briefs/rule")) return response({ content: "# Brief generation rule\n\n- Synthesize trends.", is_custom: false });
    if (url.includes("/briefs?")) return response({ items: [] });
    if (url.endsWith("/brief-schedules")) return response({ items: [] });
    if (url.endsWith("/network-proxy/test")) return response({ results: [{ target_url: "https://google.com/", ok: true, status_code: 200, elapsed_ms: 25, final_url: "https://www.google.com/", error: null }, { target_url: "https://bing.com/", ok: false, status_code: null, elapsed_ms: 31, final_url: null, error: "Proxy test failed: ConnectTimeout" }] });
    if (url.endsWith("/network-proxy")) return response({ enabled: false, url: "", username: null, password_configured: false, password_hint: null, global_mode: "direct", running_in_container: true, feed_modes: {}, llm_connection_modes: {}, translation_service_modes: { "google-gtx": "direct", deepl: "direct", "google-cloud": "direct" } });
    if (url.endsWith("/updates/status") || url.endsWith("/updates/check")) return response({ current_version: "0.3.0", latest_version: updateAvailable ? "0.3.1" : "0.3.0", status: updateAvailable ? "downloaded" : "up_to_date", release_url: updateAvailable ? "https://github.com/ImVictorCheng/affogato-rss-reader/releases/tag/v0.3.1" : null, release_notes: null, published_at: null, last_checked_at: "2026-08-01T21:00:00Z", downloaded_at: updateAvailable ? "2026-08-01T21:00:02Z" : null, install_requested_at: null, installed_at: null, downloaded: updateAvailable, downloaded_bytes: updateAvailable ? 4096 : null, install_supported: true, automatic_checks_enabled: true, check_hour: 5, error: null });
    if (url.endsWith("/settings")) return response({ app_name: "Affogato RSS Reader", version: "0.3.0", timezone: "UTC", debug: false });
    if (url.includes("/jobs?") || url.includes("/jobs/sync-runs?")) return response({ items: [] });
    if (url.includes("/call-logs?")) return response({
      items: [{
        id: "log-1", timestamp: "2026-07-29T09:00:00Z", category: "llm",
        operation: "chat_completion", feature: "brief", provider: null,
        model: "summary-model", connection_id: 7, connection_name: "Brief LLM",
        target_language: null, status: "success", duration_ms: 850,
        input_chars: 1200, output_chars: 340, cached: false, error: null,
      }],
      file_path: "/app/logs/llm-translation.jsonl",
      host_path_hint: "logs/llm-translation.jsonl",
    });
    if (url.endsWith("/feeds/1/refresh") && init?.method === "POST") return response({});
    if (url.endsWith("/feeds/sort-settings")) return response({ sort_mode: "alpha", sort_direction: "asc" });
    if (url.endsWith("/feeds")) return response({ items: [{ id: 1, title: "Example Science", url: "https://example.test/rss", folder: "Research", enabled: true, poll_interval_minutes: 45, status: "healthy", unread_count: entryRead ? 0 : 1, entry_count: 1, error_count: 0, domains: entry.domains }] });
    if (url.endsWith("/folders")) return response({ items: [{ id: 1, name: "Research", position: 0, feed_count: 1 }] });
    if (url.endsWith("/domains")) return response({ items: entry.domains });
    if (url.endsWith("/tags")) return response({ items: [] });
    if (url.includes("/entries/mark-all-read") && init?.method === "POST") {
      const updated = entryRead ? 0 : 1;
      entryRead = true;
      return response({ updated });
    }
    if (url.endsWith("/entries/1")) return response(currentEntry());
    if (url.includes("/entries?")) {
      const unreadView = url.includes("view=unread");
      return response({ items: unreadView && entryRead ? [] : [currentEntry()], total: unreadView && entryRead ? 0 : 1, page: 1, per_page: 40 });
    }
    if (url.includes("/state") && init?.method === "PATCH") {
      const body = JSON.parse(String(init.body));
      if (typeof body.read === "boolean") entryRead = body.read;
      return response(currentEntry());
    }
    return response({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Affogato RSS Reader", () => {
  beforeEach(() => { localStorage.clear(); localStorage.setItem("affogato-rss-reader:locale", "en"); });
  it("renders a generic three-pane library", async () => {
    mockApi(); render(<App />);
    expect(await screen.findByRole("heading", { name: "Unread" })).toBeInTheDocument();
    expect((await screen.findAllByText("A general research article")).length).toBeGreaterThan(0);
    expect(screen.getByText("Original summary.")).toBeInTheDocument();
  });
  it("opens briefs as an integrated workspace", async () => {
    const user = userEvent.setup();
    mockApi(); render(<App />);
    await screen.findByRole("heading", { name: "Unread" });

    const button = screen.getByRole("button", { name: "Briefs" });
    expect(button).toBeEnabled();
    await user.click(button);

    expect(await screen.findByRole("heading", { name: "Briefs", level: 1 }, { timeout: 3000 })).toBeInTheDocument();
    expect(document.querySelector(".reader-shell")).toHaveClass("is-brief-workspace");
  });
  it("resizes the desktop panes from separators and persists the widths", async () => {
    mockApi(); render(<App />);
    const navigationResizer = await screen.findByRole("separator", { name: "Resize navigation pane" });
    const listResizer = screen.getByRole("separator", { name: "Resize article list pane" });
    const shell = navigationResizer.closest(".reader-shell") as HTMLElement;
    const pointer = (type: string, clientX: number) => {
      const event = new MouseEvent(type, { bubbles: true, button: 0, clientX });
      Object.defineProperty(event, "pointerId", { value: 1 });
      return event;
    };

    expect(navigationResizer).toHaveAttribute("aria-valuenow", "264");
    expect(listResizer).toHaveAttribute("aria-valuenow");
    fireEvent(navigationResizer, pointer("pointerdown", 264));
    fireEvent(navigationResizer, pointer("pointermove", 304));
    fireEvent(navigationResizer, pointer("pointerup", 304));

    await waitFor(() => expect(shell.style.getPropertyValue("--sidebar-pane-width")).toBe("304px"));
    await waitFor(() => expect(JSON.parse(localStorage.getItem("affogato-rss-reader:pane-widths") || "{}").sidebar).toBe(304));
    expect(screen.getByRole("button", { name: /^Example Science/ })).toBeInTheDocument();
  });
  it("collapses the navigation pane and remembers the choice", async () => {
    mockApi(); const user = userEvent.setup(); const { unmount } = render(<App />);
    const collapse = await screen.findByRole("button", { name: "Collapse navigation" });
    const shell = collapse.closest(".reader-shell") as HTMLElement;

    await user.click(collapse);

    expect(shell).toHaveClass("is-sidebar-collapsed");
    expect(screen.getByRole("button", { name: "Expand navigation" })).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(localStorage.getItem("affogato-rss-reader:sidebar-collapsed")).toBe("true"));

    unmount();
    mockApi(); render(<App />);
    expect(await screen.findByRole("button", { name: "Expand navigation" })).toBeInTheDocument();
  });
  it("submits full-field search filters", async () => {
    const fetchMock = mockApi(); const user = userEvent.setup(); render(<App />);
    const search = await screen.findByRole("searchbox");
    await user.type(search, "alice"); await user.keyboard("{Enter}");
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("q=alice"))).toBe(true));
  });
  it("marks a clicked timeline card as read and updates unread counts", async () => {
    const fetchMock = mockApi(); const user = userEvent.setup(); render(<App />);
    const cardTitle = await screen.findByRole("heading", { name: "一篇通用研究文章", level: 3 });
    expect(screen.getByRole("button", { name: /Unread 1/ })).toBeInTheDocument();
    await user.click(cardTitle);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => {
      if (!String(url).includes("/entries/1/state") || init?.method !== "PATCH") return false;
      return JSON.parse(String(init.body)).read === true;
    })).toBe(true));
    expect(screen.getByRole("button", { name: /Unread 0/ })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Example Science" })).toBeInTheDocument());
  });
  it("refreshes the selected source, toggles unread mode, and marks the filtered timeline read", async () => {
    const fetchMock = mockApi(); const user = userEvent.setup(); render(<App />);
    const refresh = await screen.findByRole("button", { name: /Refresh source/ });
    expect(refresh).toBeDisabled();
    await user.click(await screen.findByRole("button", { name: /Example Science 1/ }));
    await waitFor(() => expect(refresh).toBeEnabled());
    await user.click(refresh);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/feeds/1/refresh") && init?.method === "POST")).toBe(true));
    await user.click(screen.getByRole("button", { name: /Unread only/ }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/entries?") && String(url).includes("view=unread") && String(url).includes("feed_id=1"))).toBe(true));
    await user.click(screen.getByRole("button", { name: /Mark all read/ }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url).includes("/entries/mark-all-read?") && String(url).includes("feed_id=1") && init?.method === "POST")).toBe(true));
    await waitFor(() => expect(screen.getByRole("button", { name: /Unread 0/ })).toBeInTheDocument());
  });
  it("shows clean first-run setup", async () => {
    mockApi(false); render(<App />);
    expect(await screen.findByRole("heading", { name: "Create the owner password" })).toBeInTheDocument();
  });
  it("offers built-in and custom cross-field onboarding before the library", async () => {
    mockApi(true, true); const user = userEvent.setup(); render(<App />);
    expect(await screen.findByRole("heading", { name: "What fields do you follow?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Quantum physics/ }));
    await user.type(screen.getByPlaceholderText(/Quantum computing/), "Quantum computing");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Choose how to shape the interface" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Preview built-in theme" }));
    expect(await screen.findByRole("button", { name: "Use this interface" })).toBeInTheDocument();
  });
  it("allows a generated template brand to be customized during onboarding", async () => {
    mockApi(true, true); const user = userEvent.setup(); render(<App />);
    await user.click(await screen.findByRole("button", { name: /Quantum physics/ }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Customize name or logo" }));
    const name = screen.getByRole("textbox", { name: "Site name (optional)" });
    await user.clear(name);
    await user.type(name, "Qubit Observer");
    expect(screen.getAllByText("Qubit Observer").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Restore template branding" })).toBeInTheDocument();
  });
  it("allows the saved template brand to be changed later in Settings", async () => {
    const fetchMock = mockApi(); const user = userEvent.setup(); render(<App />);
    await screen.findByRole("heading", { name: "Unread" });
    await user.click(screen.getByRole("button", { name: /Settings/ }));
    await user.click(await screen.findByRole("button", { name: /Appearance & language/ }));
    const section = (await screen.findByText("SITE IDENTITY")).closest("section");
    expect(section).not.toBeNull();
    const name = within(section!).getByRole("textbox", { name: "Site name" });
    await user.clear(name);
    await user.type(name, "Qubit Observer");
    await user.click(within(section!).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      const request = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/onboarding") && init?.method === "PUT");
      expect(request).toBeTruthy();
      expect(JSON.parse(String(request?.[1]?.body)).theme.identity).toMatchObject({
        name: "Qubit Observer",
        logo_kind: "generated",
        primary_template: "quantum-physics",
      });
    });
  });
  it("keeps LLM connection management separate from translation settings", async () => {
    const fetchMock = mockApi(); const user = userEvent.setup(); render(<App />);
    await screen.findByRole("heading", { name: "Unread" });
    await user.click(screen.getByRole("button", { name: /Settings/ }));
    expect(await screen.findByRole("button", { name: /LLM connections/ })).toBeInTheDocument();
    expect(screen.queryByText("NETWORK PROXY")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /LLM connections/ }));
    const llmSection = (await screen.findByText("LLM CONNECTIONS")).closest("section");
    expect(llmSection).not.toBeNull();
    expect(within(llmSection!).getByRole("textbox", { name: "Connection name" })).toBeInTheDocument();
    expect(within(llmSection!).getByRole("button", { name: "Add connection" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "← Back to settings" }));
    await user.click(screen.getByRole("button", { name: /Network proxy/ }));
    const proxySection = (await screen.findByText("NETWORK PROXY")).closest("section");
    expect(proxySection).not.toBeNull();
    expect(within(proxySection!).getByRole("textbox", { name: "Proxy URL" })).toBeInTheDocument();
    const dockerNotice = within(proxySection!).getByText("Docker setup").closest("div");
    expect(dockerNotice).toHaveTextContent("http://host.docker.internal:7890");
    expect(dockerNotice).toHaveTextContent("not 127.0.0.1");
    const globalMode = within(proxySection!).getByRole("combobox", { name: "Application-wide proxy route" });
    expect(globalMode).toHaveTextContent("Direct");
    const feedMode = within(proxySection!).getByRole("combobox", { name: /Example Science/ });
    expect(feedMode).toHaveTextContent("Direct");
    const gtxMode = within(proxySection!).getByRole("combobox", { name: /Google GTX/ });
    expect(gtxMode).toHaveTextContent("Direct");
    expect(within(proxySection!).getByRole("button", { name: "Test custom proxy" })).toBeDisabled();
    await user.click(feedMode);
    await user.click(screen.getByRole("option", { name: "Custom proxy" }));
    await user.click(globalMode);
    await user.click(screen.getByRole("option", { name: "System proxy" }));
    await user.click(gtxMode);
    await user.click(screen.getByRole("option", { name: "System proxy" }));
    await user.type(within(proxySection!).getByRole("textbox", { name: "Proxy URL" }), "http://127.0.0.1:7890");
    await user.click(within(proxySection!).getByRole("button", { name: "Test custom proxy" }));
    expect((await within(proxySection!).findByText(/google\.com/)).closest("p")).toHaveTextContent("Reachable");
    expect(within(proxySection!).getByText(/bing\.com/).closest("p")).toHaveTextContent("Failed");
    await user.click(within(proxySection!).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      const request = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/network-proxy") && init?.method === "PATCH");
      expect(JSON.parse(String(request?.[1]?.body)).feed_modes).toMatchObject({ "1": "custom" });
      expect(JSON.parse(String(request?.[1]?.body)).global_mode).toBe("system");
      expect(JSON.parse(String(request?.[1]?.body)).translation_service_modes).toMatchObject({ "google-gtx": "system", deepl: "direct", "google-cloud": "direct" });
    });
    await user.click(screen.getByRole("button", { name: "← Back to settings" }));
    await user.click(screen.getByRole("button", { name: /TRANSLATION Translation/ }));
    const translationSection = (await screen.findByText("TRANSLATION")).closest("section");
    expect(translationSection).not.toBeNull();
    expect(within(translationSection!).queryByRole("textbox", { name: "Connection name" })).not.toBeInTheDocument();
    expect(within(translationSection!).getByText("Queued for translation")).toBeInTheDocument();
    expect(within(translationSection!).getByText("Translating now")).toBeInTheDocument();
    expect(within(translationSection!).getByText("Failed")).toBeInTheDocument();
    expect(within(translationSection!).getByRole("button", { name: "Retry failed" })).toBeEnabled();
  });
  it("prompts for a downloaded update and exposes update controls in Settings", async () => {
    mockApi(true, false, true); const user = userEvent.setup(); render(<App />);
    expect(await screen.findByText("Version 0.3.1 is ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install and restart" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: /Settings/ }));
    await user.click(await screen.findByRole("button", { name: /Account & system/ }));
    expect(await screen.findByRole("heading", { name: "Application update" })).toBeInTheDocument();
    expect(screen.getByText("Version 0.3.1 is downloaded")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check now" })).toBeEnabled();
  });
  it("loads only lightweight data when settings first opens", async () => {
    const fetchMock = mockApi(); const user = userEvent.setup(); render(<App />);
    await screen.findByRole("heading", { name: "Unread" });
    const callsBeforeSettings = fetchMock.mock.calls.length;

    await user.click(screen.getByRole("button", { name: /Settings/ }));
    expect(await screen.findByRole("button", { name: /Activity, logs & shortcuts/ })).toBeInTheDocument();

    const settingsCalls = fetchMock.mock.calls
      .slice(callsBeforeSettings)
      .map(([url]) => String(url));
    expect(settingsCalls.some((url) => url.endsWith("/settings"))).toBe(false);
    expect(settingsCalls.some((url) => url.endsWith("/onboarding"))).toBe(false);
    expect(settingsCalls.some((url) => url.endsWith("/translations/status"))).toBe(false);
    expect(settingsCalls.some((url) => url.endsWith("/network-proxy"))).toBe(false);
    expect(settingsCalls.some((url) => url.includes("/jobs?"))).toBe(false);
    expect(settingsCalls.some((url) => url.includes("/call-logs?"))).toBe(false);
  });
  it("shows LLM and translation call logs in settings", async () => {
    mockApi(); const user = userEvent.setup(); render(<App />);
    await screen.findByRole("heading", { name: "Unread" });
    await user.click(screen.getByRole("button", { name: /Settings/ }));
    await user.click(await screen.findByRole("button", { name: /Activity, logs & shortcuts/ }));

    expect(await screen.findByRole("heading", { name: "LLM and translation call logs" })).toBeInTheDocument();
    expect(screen.getByText("LLM · brief")).toBeInTheDocument();
    expect(screen.getByText("Brief LLM · summary-model")).toBeInTheDocument();
    expect(screen.getByText("logs/llm-translation.jsonl")).toBeInTheDocument();
  });
});
