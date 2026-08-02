import { createServer } from "node:http";

const host = "127.0.0.1";
const port = 18081;
const domains = [
  { id: 1, name: "Science", description: "", color: "#2bc7c3", position: 0, feed_count: 1, entry_count: 2 },
  { id: 2, name: "Technology", description: "", color: "#8878e8", position: 1, feed_count: 1, entry_count: 1 },
];
const originalEntries = [
  {
    id: 101, title: "A reproducible experiment", translated_title: "一项可复现实验",
    summary: "The original summary remains readable.", translated_summary: "原始摘要始终可读。",
    url: "https://example.org/articles/101", authors: ["Ada Lovelace"], categories: ["research"],
    published_at: "2026-07-26T03:00:00Z", updated_at: "2026-07-26T03:00:00Z",
    feed_titles: ["Example Science"], feed_ids: [1], domains, tags: [],
    state: { read: false, starred: false, later: false, archived: false },
    translation_status: "complete", translation_error: null,
  },
  {
    id: 102, title: "A provider-independent article", translated_title: null,
    summary: "Reading works even when translation is unavailable.", translated_summary: null,
    url: "https://example.org/articles/102", authors: ["Grace Hopper"], categories: ["technology"],
    published_at: "2026-07-26T02:00:00Z", updated_at: "2026-07-26T02:00:00Z",
    feed_titles: ["Example Science"], feed_ids: [1], domains: [domains[0]], tags: [],
    state: { read: false, starred: false, later: false, archived: false },
    translation_status: "failed", translation_error: "provider timeout",
  },
];
const feed = {
  id: 1, title: "Example Science", url: "https://example.org/feed.xml", site_url: "https://example.org",
  folder: "Research", enabled: true, poll_interval_minutes: 45, status: "healthy", error_count: 0,
  last_checked_at: "2026-07-26T04:10:00Z", last_fetched_at: "2026-07-26T04:10:00Z",
  next_fetch_at: "2026-07-26T04:55:00Z", last_error: null, domains,
};
const brief = {
  id: 9, schedule_id: null, period: "daily",
  period_start: "2026-07-26T00:00:00Z", period_end: "2026-07-27T00:00:00Z",
  start_at: "2026-07-26T00:00:00Z", end_at: "2026-07-27T00:00:00Z",
  title: "Daily brief · 2026-07-27",
  notes: "## Overview\n\n**Key finding** across sources.\n\n| Theme | Direction |\n| --- | --- |\n| Reproducibility | Improving |",
  stats: { entries: 2, feeds: 1, analyzed_entries: 2 },
  filters: {}, item_count: 2,
  created_at: "2026-07-27T01:00:00Z", updated_at: "2026-07-27T01:00:00Z",
  status: "ready",
};
let entries = structuredClone(originalEntries);

function json(response, status, value) {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-CSRF-Token": "e2e-token" });
  response.end(JSON.stringify(value));
}
function empty(response) {
  response.writeHead(204, { "X-CSRF-Token": "e2e-token" });
  response.end();
}
async function body(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {};
}

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url ?? "/", `http://${host}:${port}`);
    const path = url.pathname;
    if (path === "/__test__/health") return json(response, 200, { ok: true });
    if (path === "/__test__/reset") { entries = structuredClone(originalEntries); return empty(response); }
    if (path === "/api/v1/auth/status") return json(response, 200, { setup_required: false, authenticated: true, mode: "owner", csrf_token: "e2e-token", owner: { name: "Owner" } });
    if (path === "/api/v1/feeds/sort-settings") return json(response, 200, { sort_mode: "alpha", sort_direction: "asc" });
    if (path === "/api/v1/feeds" && request.method === "GET") return json(response, 200, { items: [{ ...feed, unread_count: entries.filter((item) => !item.state.read).length, entry_count: entries.length }] });
    if (path === "/api/v1/folders") return json(response, 200, { items: [{ id: 1, name: "Research", position: 0, sort_mode: "alpha", sort_direction: "asc", feed_count: 1 }] });
    if (path === "/api/v1/domains") return json(response, 200, { items: domains });
    if (path === "/api/v1/tags") return json(response, 200, { items: [] });
    if (path === "/api/v1/llm/connections") return json(response, 200, []);
    if (path === "/api/v1/briefs/configuration") return json(response, 200, { llm_connection_id: null, llm_connection_name: null, model: null, configured: false });
    if (path === "/api/v1/briefs/rule") return json(response, 200, { content: "# Brief generation rule\n\n- Synthesize trends.", is_custom: false });
    if (path === "/api/v1/briefs" && request.method === "GET") return json(response, 200, { items: [brief] });
    if (path === "/api/v1/brief-schedules") return json(response, 200, { items: [] });
    if (path === "/api/v1/entries" && request.method === "GET") {
      let result = [...entries];
      const view = url.searchParams.get("view");
      if (view === "unread") result = result.filter((item) => !item.state.read);
      if (view === "starred") result = result.filter((item) => item.state.starred);
      const selected = url.searchParams.getAll("domain_ids").map(Number);
      if (selected.length) {
        const match = url.searchParams.get("domain_match") || "any";
        result = result.filter((item) => match === "all" ? selected.every((id) => item.domains.some((domain) => domain.id === id)) : selected.some((id) => item.domains.some((domain) => domain.id === id)));
      }
      const query = url.searchParams.get("q")?.toLowerCase();
      if (query) result = result.filter((item) => JSON.stringify(item).toLowerCase().includes(query));
      return json(response, 200, { items: result, total: result.length, page: 1, per_page: 40 });
    }
    const state = path.match(/^\/api\/v1\/entries\/(\d+)\/state$/);
    if (state && request.method === "PATCH") {
      const entry = entries.find((item) => item.id === Number(state[1]));
      entry.state = { ...entry.state, ...(await body(request)) };
      return json(response, 200, entry);
    }
    const detail = path.match(/^\/api\/v1\/entries\/(\d+)$/);
    if (detail && request.method === "GET") return json(response, 200, entries.find((item) => item.id === Number(detail[1])));
    if (path === "/api/v1/auth/logout") return empty(response);
    return json(response, 404, { detail: `${request.method} ${path}` });
  } catch (error) {
    return json(response, 500, { detail: String(error) });
  }
});
server.listen(port, host);
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.close(() => process.exit(0)));
