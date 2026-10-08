import { createServer } from "node:http";

const host = "127.0.0.1";
const port = 18081;
const domains = [
  { id: 1, name: "Science", description: "", color: "#2bc7c3", position: 0, feed_count: 1, entry_count: 2 },
  { id: 2, name: "Technology", description: "", color: "#8878e8", position: 1, feed_count: 1, entry_count: 1 },
];
const originalTags = [
  { id: 1, name: "Condensed Matter", color: "#8878e8", description: "Physics of condensed phases", aliases: [], origin: "manual", auto_assignable: true, entry_count: 1 },
  { id: 2, name: "QEC", color: "#8878e8", description: "", aliases: ["Quantum error correction"], origin: "legacy", auto_assignable: true, entry_count: 1 },
  { id: 3, name: "Quantum Computing", color: "#8878e8", description: "Quantum information processing", aliases: ["QC"], origin: "manual", auto_assignable: true, entry_count: 2 },
];
const originalCleanupItems = [
  { tag_id: 2, name: "QEC", total_count: 1, inferred_auto_count: 1, legacy_count: 1, manual_count: 0, auto_count: 0, feed_count: 0, schedule_count: 0, deletable: true },
  { tag_id: 1, name: "Condensed Matter", total_count: 1, inferred_auto_count: 1, legacy_count: 1, manual_count: 1, auto_count: 0, feed_count: 0, schedule_count: 0, deletable: false },
];
const originalEntries = [
  {
    id: 101, title: "A reproducible $E=mc^2$ experiment", translated_title: "一项 \\(E=mc^2\\) 可复现实验",
    summary: "The state $\\lvert\\psi\\rangle=a*b_c$ evolves with \\[H=\\frac{p^2}{2m}+V(x)\\].", translated_summary: "能量满足 \\(E=mc^2\\)，原始摘要始终可读。",
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
  notes: "## Overview\n\n**Key finding** across sources with $a*b_c + \\href{https://math-marker.invalid}{marker} + \\class{math-marker}{x} + \\style{color:red}{y}$ and \\(E=mc^2\\).\n\n$$\n\\begin{aligned}a&=b\\\\c&=d\\end{aligned}\n$$\n\n`$code_not_math$`\n\n![Remote chart](https://remote-marker.invalid/chart.png)\n\n[Blocked location](file:///private/report) · [Safe reference](https://example.test/report)\n\n| Theme | Direction |\n| --- | --- |\n| Reproducibility | Improving |",
  stats: { entries: 2, feeds: 1, analyzed_entries: 2 },
  filters: {}, item_count: 2,
  created_at: "2026-07-27T01:00:00Z", updated_at: "2026-07-27T01:00:00Z",
  status: "ready",
};
let entries = structuredClone(originalEntries);
let tags = structuredClone(originalTags);
let cleanupItems = structuredClone(originalCleanupItems);
let cleanupRevision = 1;
let cleanupReviewed = false;
let invalidateCleanupAfterReviewRead = false;
let cleanupPreviewFailures = 0;
const cleanupReviewToken = () => cleanupRevision.toString(16).padStart(64, "0");
let autoTagPolicyRevision = 1;
const autoTagPolicyVersion = () => `e2e-policy-${autoTagPolicyRevision}`;
const defaultAutoTagStatus = {
  enabled: false, create_new: true, growth_mode: "threshold",
  llm_connection_id: 1, llm_connection_name: "Test LLM", model: "test-model", configured: true,
  max_tags_per_entry: 3, promotion_threshold: 10, support_window_days: 365,
  canonical_language: "en", min_confidence: 0.8, preview_required: true,
  proposal_count: 1, promoted_count: 1, estimated_calls: 200,
  outdated_count: 0, needs_rebuild: false,
  pending_count: 0, running_count: 0, complete_count: 0, failed_count: 0,
};
let autoTagStatus = structuredClone(defaultAutoTagStatus);
let autoTagPreview = null;
let autoTagPreviewFinal = null;
let nextAutoTagPreviewId = 1;
let autoTagPreviewMode = "success";
let autoTagProposals = [
  { id: 7, name: "reproducibility", description: "Reproducible research", status: "active", support_count: 4, promoted_tag_id: null, aliases: ["reproducible science"], created_at: "2026-07-27T01:00:00Z", updated_at: "2026-07-27T01:00:00Z" },
  { id: 8, name: "Quantum Sensing", description: "Quantum-enhanced sensing", status: "promoted", support_count: 10, promoted_tag_id: 3, aliases: ["量子传感"], created_at: "2026-07-26T01:00:00Z", updated_at: "2026-07-27T01:00:00Z" },
];

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
    if (path === "/__test__/reset") {
      entries = structuredClone(originalEntries);
      tags = structuredClone(originalTags);
      cleanupItems = structuredClone(originalCleanupItems);
      cleanupRevision = 1;
      cleanupReviewed = false;
      invalidateCleanupAfterReviewRead = false;
      cleanupPreviewFailures = 0;
      autoTagPolicyRevision = 1;
      autoTagStatus = structuredClone(defaultAutoTagStatus);
      autoTagPreview = null;
      autoTagPreviewFinal = null;
      nextAutoTagPreviewId = 1;
      autoTagPreviewMode = "success";
      return empty(response);
    }
    if (path === "/__test__/auto-tag-scenario" && request.method === "POST") {
      const input = await body(request);
      if (input.rebuild) {
        autoTagStatus = { ...autoTagStatus, needs_rebuild: true, outdated_count: Number(input.outdated_count || 23) };
      }
      if (input.preview_mode) autoTagPreviewMode = input.preview_mode;
      if (input.cleanup_empty) {
        cleanupItems = [];
        cleanupRevision += 1;
        cleanupReviewed = false;
      }
      if (input.invalidate_cleanup_review) {
        cleanupRevision += 1;
        cleanupReviewed = false;
      }
      if (input.invalidate_cleanup_after_review_read) invalidateCleanupAfterReviewRead = true;
      if (input.fail_cleanup_preview_once) cleanupPreviewFailures += 1;
      return json(response, 200, { ok: true });
    }
    if (path === "/api/v1/auth/status") return json(response, 200, { setup_required: false, authenticated: true, mode: "owner", csrf_token: "e2e-token", owner: { name: "Owner" } });
    if (path === "/api/v1/feeds/sort-settings") return json(response, 200, { sort_mode: "alpha", sort_direction: "asc" });
    if (path === "/api/v1/feeds" && request.method === "GET") return json(response, 200, { items: [{ ...feed, unread_count: entries.filter((item) => !item.state.read).length, entry_count: entries.length }] });
    if (path === "/api/v1/folders") return json(response, 200, { items: [{ id: 1, name: "Research", position: 0, sort_mode: "alpha", sort_direction: "asc", feed_count: 1 }] });
    if (path === "/api/v1/domains") return json(response, 200, { items: domains });
    if (path === "/api/v1/tags" && request.method === "GET") return json(response, 200, { items: tags });
    if (path === "/api/v1/tags" && request.method === "POST") {
      const input = await body(request);
      const tag = { id: Math.max(0, ...tags.map((item) => item.id)) + 1, color: null, description: "", aliases: [], origin: "manual", auto_assignable: true, entry_count: 0, ...input };
      tags.push(tag);
      autoTagPolicyRevision += 1;
      autoTagStatus = { ...autoTagStatus, preview_required: true, needs_rebuild: true, outdated_count: 50 };
      return json(response, 201, tag);
    }
    const tagDetail = path.match(/^\/api\/v1\/tags\/(\d+)$/);
    if (tagDetail && request.method === "PATCH") {
      const tag = tags.find((item) => item.id === Number(tagDetail[1]));
      Object.assign(tag, await body(request));
      autoTagPolicyRevision += 1;
      autoTagStatus = { ...autoTagStatus, preview_required: true, needs_rebuild: true, outdated_count: 50 };
      return json(response, 200, tag);
    }
    if (tagDetail && request.method === "DELETE") {
      tags = tags.filter((item) => item.id !== Number(tagDetail[1]));
      autoTagPolicyRevision += 1;
      autoTagStatus = { ...autoTagStatus, preview_required: true, needs_rebuild: true, outdated_count: 50 };
      return empty(response);
    }
    if (path === "/api/v1/llm/connections") return json(response, 200, [{ id: 1, name: "Test LLM", base_url: "https://example.test/v1", model: "test-model", api_key_configured: true, api_key_hint: "test…key", used_by: ["auto_tag"] }]);
    if (path === "/api/v1/auto-tag/status" && request.method === "PATCH") {
      const patch = await body(request);
      const growthMode = patch.growth_mode ?? autoTagStatus.growth_mode;
      const llmConnectionId = patch.llm_connection_id ?? autoTagStatus.llm_connection_id;
      const policyChanged = growthMode !== autoTagStatus.growth_mode || llmConnectionId !== autoTagStatus.llm_connection_id;
      if (policyChanged) autoTagPolicyRevision += 1;
      autoTagStatus = {
        ...autoTagStatus,
        ...patch,
        growth_mode: growthMode,
        create_new: growthMode === "threshold",
        llm_connection_id: llmConnectionId,
        configured: Boolean(llmConnectionId),
        ...(policyChanged ? { enabled: false, preview_required: true, needs_rebuild: true, outdated_count: 50 } : {}),
      };
      return json(response, 200, autoTagStatus);
    }
    if (path === "/api/v1/auto-tag/status") return json(response, 200, autoTagStatus);
    if (path === "/api/v1/auto-tag/previews" && request.method === "GET") return json(response, 200, { items: autoTagPreview ? [autoTagPreview] : [] });
    if (path === "/api/v1/auto-tag/previews" && request.method === "POST") {
      const input = await body(request);
      if (!cleanupReviewed) return json(response, 409, { detail: "Review the current cleanup preview before creating an auto-tag preview" });
      const previewId = nextAutoTagPreviewId++;
      if (autoTagPreviewMode === "failed") {
        autoTagPreview = {
          id: previewId, status: "pending", sample_size: input.sample_size || 50, entry_ids: [], results: [], metrics: {}, last_error: null,
          created_at: "2026-07-27T01:00:00Z", updated_at: "2026-07-27T01:00:00Z",
        };
        autoTagPreviewFinal = { ...autoTagPreview, status: "failed", last_error: "provider timeout", updated_at: "2026-07-27T01:00:01Z" };
        return json(response, 202, autoTagPreview);
      }
      const results = Array.from({ length: 50 }, (_, index) => ({
        entry_id: 1000 + index,
        title: index < entries.length ? entries[index].title : `Cross-feed sample article ${index + 1}`,
        topics: index === 0 ? [{ kind: "proposal", id: 7, name: "reproducibility", confidence: 0.91 }] : [],
      }));
      autoTagPreview = {
        id: previewId, status: "pending", sample_size: input.sample_size || 50, entry_ids: results.map((item) => item.entry_id),
        results: [], metrics: {}, last_error: null,
        created_at: "2026-07-27T01:00:00Z", updated_at: "2026-07-27T01:00:00Z",
      };
      autoTagPreviewFinal = {
        ...autoTagPreview,
        status: "complete",
        results,
        metrics: { classified_count: 50, topic_count: 1, zero_topic_count: 49, new_candidate_count: 1, estimated_full_calls: 200, policy_version: autoTagPolicyVersion() },
        updated_at: "2026-07-27T01:00:01Z",
      };
      return json(response, 202, autoTagPreview);
    }
    const autoTagPreviewApprove = path.match(/^\/api\/v1\/auto-tag\/previews\/(\d+)\/approve$/);
    if (autoTagPreviewApprove && request.method === "POST") {
      const input = await body(request);
      if (!cleanupReviewed) return json(response, 409, { detail: "Cleanup review changed; run a new preview" });
      if (input.scope !== "all" || autoTagPreview?.status !== "complete") return json(response, 409, { detail: "The preview must complete successfully before approval" });
      if (autoTagPreview.metrics?.policy_version !== autoTagPolicyVersion()) return json(response, 409, { detail: "The auto-tag policy or taxonomy changed; run a new preview" });
      autoTagStatus = { ...autoTagStatus, enabled: true, preview_required: false, needs_rebuild: false, outdated_count: 0, pending_count: 50 };
      autoTagPreview = { ...autoTagPreview, status: "applied" };
      return json(response, 200, autoTagStatus);
    }
    const autoTagPreviewDetail = path.match(/^\/api\/v1\/auto-tag\/previews\/(\d+)$/);
    if (autoTagPreviewDetail && request.method === "GET") {
      if (autoTagPreviewFinal && autoTagPreview?.id === Number(autoTagPreviewDetail[1]) && ["pending", "running"].includes(autoTagPreview.status)) {
        autoTagPreview = autoTagPreviewFinal;
        autoTagPreviewFinal = null;
      }
      return json(response, 200, autoTagPreview);
    }
    if (path === "/api/v1/auto-tag/cleanup-preview") {
      if (cleanupPreviewFailures > 0) {
        cleanupPreviewFailures -= 1;
        return json(response, 503, { detail: "temporary cleanup refresh failure" });
      }
      const cleanup = {
        items: cleanupItems,
        inferred_auto_association_count: cleanupItems.reduce((total, item) => total + item.inferred_auto_count, 0),
        review_token: cleanupReviewToken(),
        reviewed: cleanupReviewed,
      };
      if (invalidateCleanupAfterReviewRead && cleanupReviewed) {
        invalidateCleanupAfterReviewRead = false;
        cleanupRevision += 1;
        cleanupReviewed = false;
      }
      return json(response, 200, cleanup);
    }
    if (path === "/api/v1/auto-tag/cleanup" && request.method === "POST") {
      const input = await body(request);
      if (input.review_token !== cleanupReviewToken()) return json(response, 409, { detail: "Cleanup preview changed; refresh and review it again" });
      const affected = new Set([...input.remove_tag_ids, ...input.keep_tag_ids]);
      const selected = cleanupItems.filter((item) => affected.has(item.tag_id));
      cleanupItems = cleanupItems.filter((item) => !affected.has(item.tag_id));
      const deletableIds = new Set(selected.filter((item) => item.deletable && input.remove_tag_ids.includes(item.tag_id)).map((item) => item.tag_id));
      tags = tags.filter((tag) => !deletableIds.has(tag.id));
      if (selected.length > 0) cleanupRevision += 1;
      cleanupReviewed = true;
      return json(response, 200, { removed_tag_ids: [...deletableIds], kept_tag_ids: input.keep_tag_ids, removed_count: selected.reduce((total, item) => total + item.inferred_auto_count, 0) });
    }
    if (path === "/api/v1/auto-tag/proposals") return json(response, 200, {
      items: autoTagProposals.slice(Number(url.searchParams.get("offset") || 0), Number(url.searchParams.get("offset") || 0) + Number(url.searchParams.get("limit") || 50)),
      total: autoTagProposals.length, offset: Number(url.searchParams.get("offset") || 0), limit: Number(url.searchParams.get("limit") || 50),
    });
    const mergeTags = path.match(/^\/api\/v1\/tags\/(\d+)\/merge\/(\d+)$/);
    if (mergeTags && request.method === "POST") {
      const sourceId = Number(mergeTags[1]);
      const targetId = Number(mergeTags[2]);
      const source = tags.find((tag) => tag.id === sourceId);
      const target = tags.find((tag) => tag.id === targetId);
      if (!source || !target) return json(response, 404, { detail: "Tag not found" });
      target.aliases = [...new Set([...(target.aliases || []), source.name, ...(source.aliases || [])])];
      target.entry_count = Math.max(target.entry_count || 0, source.entry_count || 0);
      tags = tags.filter((tag) => tag.id !== sourceId);
      cleanupItems = cleanupItems.filter((item) => item.tag_id !== sourceId);
      cleanupRevision += 1;
      cleanupReviewed = false;
      autoTagPolicyRevision += 1;
      autoTagStatus = { ...autoTagStatus, preview_required: true, needs_rebuild: true, outdated_count: 50 };
      return json(response, 200, target);
    }
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
