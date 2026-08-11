import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type {
  Brief,
  BriefGenerationProgress,
  BriefSchedule,
  Domain,
  Feed,
  LLMConnection,
  Tag,
} from "../types";
import { BriefWorkspace, naturalPeriodRange } from "./BriefWorkspace";
import { resetMathJaxForTests } from "./MathJax";

async function pickIn(container: HTMLElement, comboLabel: string, optionLabel: string) {
  const combo = within(container).getByRole("combobox", { name: comboLabel });
  await userEvent.click(combo);
  await userEvent.click(within(combo.closest(".app-dropdown") as HTMLElement).getByRole("option", { name: optionLabel }));
}

const schedule: BriefSchedule = {
  id: 3,
  name: "工作日晨报",
  period: "daily",
  timezone: "Asia/Shanghai",
  cutoff_time: "09:00",
  weekday: null,
  month_day: null,
  year_month: null,
  domain_ids: [],
  feed_ids: [],
  tag_ids: [],
  domain_match: "any",
  enabled: true,
  last_run_at: null,
  created_at: "2026-07-28T00:00:00Z",
  updated_at: "2026-07-28T00:00:00Z",
};

const connection: LLMConnection = {
  id: 7,
  name: "摘要模型",
  base_url: "https://llm.test/v1",
  model: "summary-model",
  api_key_configured: true,
  api_key_hint: "****test",
  used_by: [],
};

const brief: Brief = {
  id: 9,
  schedule_id: null,
  period: "daily",
  period_start: "2026-07-27T01:00:00Z",
  period_end: "2026-07-28T01:00:00Z",
  start_at: "2026-07-27T01:00:00Z",
  end_at: "2026-07-28T01:00:00Z",
  title: "每日简报 · 2026-07-28",
  notes: "## 今日概览\n\n研究方向正在收敛。\n\n## 优先阅读建议\n\n- 关注方法比较。",
  stats: { entries: 6, feeds: 2, analyzed_entries: 6 },
  filters: {},
  item_count: 6,
  created_at: "2026-07-28T02:00:00Z",
  updated_at: "2026-07-28T02:00:00Z",
  status: "ready",
};

function renderModal(
  schedules: BriefSchedule[] = [],
  connections: LLMConnection[] = [],
  briefs: Brief[] = [],
  latestProgress: BriefGenerationProgress | null = null,
  options: {
    domains?: Domain[];
    feeds?: Feed[];
    tags?: Tag[];
  } = {},
) {
  vi.spyOn(api, "briefs").mockResolvedValue(briefs);
  vi.spyOn(api, "briefSchedules").mockResolvedValue(schedules);
  vi.spyOn(api, "llmConnections").mockResolvedValue(connections);
  vi.spyOn(api, "domains").mockResolvedValue(options.domains ?? []);
  vi.spyOn(api, "feeds").mockResolvedValue(options.feeds ?? []);
  vi.spyOn(api, "tags").mockResolvedValue(options.tags ?? []);
  vi.spyOn(api, "briefConfiguration").mockResolvedValue({
    llm_connection_id: null,
    llm_connection_name: null,
    model: null,
    configured: false,
  });
  vi.spyOn(api, "briefRule").mockResolvedValue({
    content: "# 简报生成规则\n\n- 只做综合分析。",
    is_custom: false,
  });
  vi.spyOn(api, "setBriefConfiguration").mockResolvedValue({
    llm_connection_id: connection.id,
    llm_connection_name: connection.name,
    model: connection.model,
    configured: true,
  });
vi.spyOn(api, "latestBriefGenerationProgress").mockResolvedValue(latestProgress);
  render(
    <BriefWorkspace
      locale="zh-CN"
      onBack={vi.fn()}
      notify={vi.fn()}
    />,
  );
}

describe("BriefWorkspace", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete window.MathJax;
    resetMathJaxForTests();
  });

  it("uses localized, descriptive controls instead of a stretched plus button", async () => {
    renderModal();

    expect(await screen.findByText("还没有自动计划")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "计划名称" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "时区" })).toHaveValue(
      Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    );
    expect(screen.getByRole("button", { name: "添加计划" })).toHaveClass(
      "brief-schedule-form__submit",
    );
    expect(document.querySelector(".brief-workspace > .provider-warning")).toHaveTextContent("LLM");
    expect(screen.queryByRole("button", { name: "+" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders schedule metadata and status in the dedicated schedule row", async () => {
    renderModal([schedule]);

    expect(await screen.findByText("工作日晨报")).toBeInTheDocument();
    expect(screen.getByText("每日 · 09:00 · Asia/Shanghai")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "已启用" })).toBeChecked();
  });

  it("renders the LLM synthesis as a report instead of an editable article list", async () => {
    renderModal([], [connection], [brief]);

    expect(await screen.findByRole("heading", { name: "今日概览" })).toBeInTheDocument();
    expect(screen.getByText("研究方向正在收敛。")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "备注" })).not.toBeInTheDocument();
    expect(screen.queryByText("Entries")).not.toBeInTheDocument();
  });

  it("renders the brief as GitHub-flavored Markdown", async () => {
    const typesetPromise = vi.fn().mockResolvedValue(undefined);
    window.MathJax = {
      startup: { promise: Promise.resolve() },
      typesetClear: vi.fn(),
      typesetPromise,
    };
    renderModal([], [connection], [{
      ...brief,
      notes: [
        "# 综合简报",
        "",
        "**核心判断**与*补充说明*。",
        "",
        "- 主题一",
        "  - 子主题",
        "",
        "> 跨来源观察",
        "",
        "| 指标 | 结果 |",
        "| --- | --- |",
        "| 覆盖 | 完整 |",
        "",
        "`inline-code`",
        "",
        "Inline formula $a*b_c$ and \\(E=mc^2\\).",
        "",
        "$$",
        "\\begin{aligned}a&=b\\\\c&=d\\end{aligned}",
        "$$",
        "",
        "`$code_not_math$`",
        "",
        "![Remote chart](https://images.example.test/chart.png)",
        "",
        "[Blocked location](file:///private/report)",
        "",
        "[参考来源](https://example.test/report)",
      ].join("\n"),
    }]);

    expect(await screen.findByRole("heading", { name: "综合简报", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("核心判断").tagName).toBe("STRONG");
    expect(screen.getByText("补充说明").tagName).toBe("EM");
    expect(screen.getAllByRole("list")).toHaveLength(2);
    expect(screen.getByText("子主题").closest("ul")).toBeInTheDocument();
    expect(screen.getByText("跨来源观察").closest("blockquote")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("inline-code").tagName).toBe("CODE");
    expect(screen.getByText("$code_not_math$").tagName).toBe("CODE");
    expect(document.querySelectorAll(".mathjax-source--inline")).toHaveLength(2);
    expect(document.querySelectorAll(".mathjax-source--display")).toHaveLength(1);
    await waitFor(() => expect(typesetPromise).toHaveBeenCalledOnce());
    expect(screen.getByRole("img", { name: "Remote chart" })).toHaveClass("brief-image-placeholder");
    expect(document.querySelector(".brief-summary img")).not.toBeInTheDocument();
    expect(screen.getByText("Blocked location")).not.toHaveAttribute("href");
    expect(screen.getByRole("link", { name: "参考来源" })).toHaveAttribute(
      "target",
      "_blank",
    );
  });

  it("generates with a compatible request key and binds the selected LLM", async () => {
    const generated = { ...brief, id: 10 };
    const create = vi.spyOn(api, "createBrief").mockResolvedValue(generated);
    renderModal([], [connection]);

    await userEvent.click(await screen.findByRole("button", { name: "生成简报" }));

    expect(api.setBriefConfiguration).toHaveBeenCalledWith(connection.id);
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        period: "daily",
        idempotency_key: expect.stringMatching(/^brief-/),
        start_at: expect.any(String),
        end_at: expect.any(String),
      }),
    );
    expect(await screen.findByText("研究方向正在收敛。")).toBeInTheDocument();
  });

  it("shows the server-reported brief generation stage and batch progress", async () => {
    let finishGeneration: ((value: Brief) => void) | undefined;
    vi.spyOn(api, "createBrief").mockImplementation(
      () => new Promise((resolve) => {
        finishGeneration = resolve;
      }),
    );
    vi.spyOn(api, "briefGenerationProgress").mockResolvedValue({
      idempotency_key: "brief-progress-test",
      status: "running",
      stage: "consolidating",
      completed: 2,
      total: 4,
    });
    renderModal([], [connection]);

    await userEvent.click(await screen.findByRole("button", { name: "生成简报" }));

    expect(
      await screen.findByText("正在合并各批结果", {}, { timeout: 2000 }),
    ).toBeInTheDocument();
    expect(screen.getByText("2 / 4")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "简报生成进度" })).toHaveAttribute(
      "value",
      "2",
    );

    await act(async () => {
      finishGeneration?.(brief);
    });
    expect(await screen.findByText("简报生成完成")).toBeInTheDocument();
  });

  it("keeps waiting when the request disconnects but the server job is running", async () => {
    vi.spyOn(api, "createBrief").mockRejectedValue(new Error("connection lost"));
    vi.spyOn(api, "briefGenerationProgress").mockResolvedValue({
      idempotency_key: "brief-disconnected-test",
      status: "running",
      stage: "summarizing_batches",
      completed: 1,
      total: 3,
    });
    renderModal([], [connection]);

    const generateButton = await screen.findByRole("button", { name: "生成简报" });
    await userEvent.click(generateButton);

    expect(
      await screen.findByText(
        "请求连接已中断，但后台仍在生成；请继续等待进度更新。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("正在分批总结条目")).toBeInTheDocument();
    expect(generateButton).toBeDisabled();
  });

  it("restores a failed task after reopening and retries from its checkpoint", async () => {
    const resumed = { ...brief, id: 11, title: "断点续跑简报" };
    const retry = vi.spyOn(api, "retryBriefGeneration").mockResolvedValue(resumed);
    vi.spyOn(api, "briefGenerationProgress").mockResolvedValue({
      idempotency_key: "brief-resume-test",
      status: "running",
      stage: "summarizing_batches",
      completed: 7,
      total: 8,
      can_retry: true,
      attempt: 2,
    });
    renderModal([], [connection], [], {
      idempotency_key: "brief-resume-test",
      status: "failed",
      stage: "summarizing_batches",
      completed: 7,
      total: 8,
      message: "temporary 503",
      can_retry: true,
      attempt: 1,
    });

    await userEvent.click(
      await screen.findByRole("button", { name: "从断点继续" }),
    );

    expect(retry).toHaveBeenCalledWith("brief-resume-test");
    expect(
      await screen.findByRole("heading", { name: resumed.title }),
    ).toBeInTheDocument();
  });

  it("shows and saves the editable rule", async () => {
    const saveRule = vi.spyOn(api, "setBriefRule").mockResolvedValue({
      content: "# 新规则\n\n只输出趋势。",
      is_custom: true,
    });
    renderModal([], [connection]);

    expect(screen.queryByRole("heading", { name: "生成规则" })).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "查看规则" }));
    expect(await screen.findByRole("heading", { name: "生成规则" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "修改规则" }));
    const editor = screen.getByRole("textbox", { name: "简报生成规则" });
    await userEvent.clear(editor);
    await userEvent.type(editor, "# 新规则\n\n只输出趋势。");
    await userEvent.click(screen.getByRole("button", { name: "保存规则" }));

    expect(saveRule).toHaveBeenCalledWith("# 新规则\n\n只输出趋势。");
    expect(await screen.findByText("当前使用你保存的自定义规则。")).toBeInTheDocument();
  });

  it("deletes a brief after confirmation and selects the next one", async () => {
    const nextBrief = { ...brief, id: 10, title: "上一期简报" };
    const remove = vi.spyOn(api, "deleteBrief").mockResolvedValue();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderModal([], [connection], [brief, nextBrief]);

    expect(await screen.findByRole("heading", { name: brief.title })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "删除简报" }));

    expect(remove).toHaveBeenCalledWith(brief.id);
    expect(await screen.findByRole("heading", { name: nextBrief.title })).toBeInTheDocument();
    expect(screen.queryByText(brief.title)).not.toBeInTheDocument();
  });

  it("edits an existing schedule and saves the changes", async () => {
    const update = vi.spyOn(api, "updateBriefSchedule").mockResolvedValue({
      ...schedule,
      name: "每周简报",
      period: "weekly",
      cutoff_time: "07:30",
      timezone: "Asia/Shanghai",
      weekday: 0,
    });
    renderModal([schedule]);

    await userEvent.click(
      await screen.findByRole("button", { name: "修改计划 工作日晨报" }),
    );

    const nameField = screen.getByRole("textbox", { name: "计划名称" });
    await userEvent.clear(nameField);
    await userEvent.type(nameField, "每周简报");
    await userEvent.click(screen.getByRole("button", { name: "每周" }));
    const timeField = screen.getByLabelText("生成时间");
    await userEvent.clear(timeField);
    await userEvent.type(timeField, "07:30");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(update).toHaveBeenCalledWith(
      3,
      expect.objectContaining({
        name: "每周简报",
        period: "weekly",
        cutoff_time: "07:30",
        weekday: 0,
      }),
    );
    expect(screen.queryByRole("button", { name: "取消" })).not.toBeInTheDocument();
  });

  it("lets a daily schedule choose a start time and sends it on save", async () => {
    const dailySchedule = { ...schedule, start_time: "09:00" };
    const update = vi.spyOn(api, "updateBriefSchedule").mockResolvedValue({
      ...dailySchedule,
      name: "工作日窗口",
      cutoff_time: "18:00",
    });
    renderModal([dailySchedule]);

    await userEvent.click(
      await screen.findByRole("button", { name: "修改计划 工作日晨报" }),
    );

    const startField = screen.getByLabelText("窗口开始时间");
    expect(startField).toHaveValue("09:00");
    await userEvent.clear(startField);
    await userEvent.type(startField, "08:30");
    const endField = screen.getByLabelText("生成时间");
    await userEvent.clear(endField);
    await userEvent.type(endField, "18:00");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(update).toHaveBeenCalledWith(
      3,
      expect.objectContaining({
        period: "daily",
        start_time: "08:30",
        cutoff_time: "18:00",
      }),
    );
  });

  it("selects domain, feed, and tag coverage for a schedule and saves them", async () => {
    const domain: Domain = {
      id: 1,
      name: "Physics",
      description: "",
      color: "#2bc7c3",
      position: 0,
      feed_count: 1,
      entry_count: 2,
    };
    const feed: Feed = {
      id: 5,
      title: "Quantum Journal",
      url: "https://example.test/quantum.xml",
      folder: "Physics",
      position: 0,
      enabled: true,
      poll_interval_minutes: 60,
      status: "healthy",
      unread_count: 1,
      entry_count: 3,
      error_count: 0,
      domains: [],
    };
    const tag: Tag = { id: 7, name: "method", color: null, entry_count: 1 };
    const create = vi.spyOn(api, "createBriefSchedule").mockResolvedValue({
      ...schedule,
      name: "过滤计划",
      domain_ids: [1],
      feed_ids: [5],
      tag_ids: [7],
    });
    renderModal([], [], [], null, { domains: [domain], feeds: [feed], tags: [tag] });

    const nameField = await screen.findByRole("textbox", { name: "计划名称" });
    await userEvent.type(nameField, "过滤计划");
    const form = document.querySelector("form.brief-schedule-form") as HTMLElement;
    await pickIn(form, "领域", "Physics");
    await pickIn(form, "订阅源", "Quantum Journal");
    await pickIn(form, "标签", "method");
    await userEvent.click(screen.getByRole("button", { name: "添加计划" }));

    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        domain_ids: [1],
        feed_ids: [5],
        tag_ids: [7],
        domain_match: "any",
      }),
    );
  });

  it("shows the ANY/ALL domain match control when more than one domain is selected", async () => {
    const domainA: Domain = { id: 1, name: "Physics", description: "", color: "#2bc7c3", position: 0, feed_count: 1, entry_count: 1 };
    const domainB: Domain = { id: 2, name: "AI", description: "", color: "#6d5fc2", position: 1, feed_count: 1, entry_count: 1 };
    renderModal([], [], [], null, { domains: [domainA, domainB] });

    await screen.findByRole("textbox", { name: "计划名称" });
    const form = document.querySelector("form.brief-schedule-form") as HTMLElement;
    expect(within(form).queryByRole("button", { name: "ANY" })).not.toBeInTheDocument();
    const domainCombo = within(form).getByRole("combobox", { name: "领域" });
    await userEvent.click(domainCombo);
    await userEvent.click(within(domainCombo.closest(".app-dropdown") as HTMLElement).getByRole("option", { name: "Physics" }));
    await userEvent.click(within(domainCombo.closest(".app-dropdown") as HTMLElement).getByRole("option", { name: "AI" }));
    expect(within(form).getByRole("button", { name: "ANY" })).toBeInTheDocument();
    await userEvent.click(within(form).getByRole("button", { name: "ALL" }));
  });

  it("deletes an existing schedule after confirmation", async () => {
    const remove = vi.spyOn(api, "deleteBriefSchedule").mockResolvedValue();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderModal([schedule]);

    await userEvent.click(
      await screen.findByRole("button", { name: "删除计划 工作日晨报" }),
    );

    expect(remove).toHaveBeenCalledWith(schedule.id);
  });

  it("runs a schedule immediately from its row", async () => {
    const run = vi.spyOn(api, "runBriefSchedule").mockResolvedValue(brief);
    renderModal([schedule]);

    await userEvent.click(
      await screen.findByRole("button", { name: "立即生成 工作日晨报" }),
    );

    expect(run).toHaveBeenCalledWith(schedule.id);
    expect(await screen.findByRole("heading", { name: brief.title })).toBeInTheDocument();
  });

  it("sends the selected coverage when generating a brief manually", async () => {
    const domain: Domain = { id: 1, name: "Physics", description: "", color: "#2bc7c3", position: 0, feed_count: 1, entry_count: 2 };
    const feed: Feed = {
      id: 5, title: "Quantum Journal", url: "https://example.test/q.xml", folder: "Physics",
      position: 0, enabled: true, poll_interval_minutes: 60, status: "healthy",
      unread_count: 0, entry_count: 3, error_count: 0, domains: [],
    };
    const tag: Tag = { id: 7, name: "method", color: null, entry_count: 1 };
    const create = vi.spyOn(api, "createBrief").mockResolvedValue(brief);
    renderModal([], [connection], [], null, { domains: [domain], feeds: [feed], tags: [tag] });

    await screen.findByRole("button", { name: "生成简报" });
    const domainCombo = screen.getAllByRole("combobox", { name: "领域" })[0];
    await userEvent.click(domainCombo);
    await userEvent.click(within(domainCombo.closest(".app-dropdown") as HTMLElement).getByRole("option", { name: "Physics" }));
    const feedCombo = screen.getAllByRole("combobox", { name: "订阅源" })[0];
    await userEvent.click(feedCombo);
    await userEvent.click(within(feedCombo.closest(".app-dropdown") as HTMLElement).getByRole("option", { name: "Quantum Journal" }));
    const tagCombo = screen.getAllByRole("combobox", { name: "标签" })[0];
    await userEvent.click(tagCombo);
    await userEvent.click(within(tagCombo.closest(".app-dropdown") as HTMLElement).getByRole("option", { name: "method" }));
    await userEvent.click(screen.getByRole("button", { name: "生成简报" }));

    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        domain_ids: [1],
        feed_ids: [5],
        tag_ids: [7],
        domain_match: "any",
      }),
    );
  });

  it("stops a running generation from the progress panel", async () => {
    const stop = vi.spyOn(api, "stopBriefGeneration").mockResolvedValue({
      idempotency_key: "brief-stop-test",
      status: "running",
      stage: "summarizing_batches",
      completed: 2,
      total: 4,
      can_retry: false,
      attempt: 1,
    });
    renderModal([], [connection], [], {
      idempotency_key: "brief-stop-test",
      status: "running",
      stage: "summarizing_batches",
      completed: 2,
      total: 4,
    });

    await userEvent.click(
      await screen.findByRole("button", { name: "停止生成" }),
    );

    expect(stop).toHaveBeenCalledWith("brief-stop-test");
  });

  it("offers restart from scratch alongside checkpoint resume after a stop", async () => {
    const restart = vi.spyOn(api, "restartBriefGeneration").mockResolvedValue({
      ...brief,
      title: "从头再来的简报",
    });
    renderModal([], [connection], [], {
      idempotency_key: "brief-stopped-test",
      status: "failed",
      stage: "summarizing_batches",
      completed: 2,
      total: 4,
      can_retry: true,
      stopped: true,
      message: "Generation stopped by owner",
    });

    expect(await screen.findByText("生成已停止")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "从断点继续" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "从头开始" }));
    expect(restart).toHaveBeenCalledWith("brief-stopped-test");
    expect(await screen.findByRole("heading", { name: "从头再来的简报" })).toBeInTheDocument();
  });
});

describe("naturalPeriodRange", () => {
  it("defaults daily briefs to local midnight through now", () => {
    const range = naturalPeriodRange("daily", new Date(2026, 6, 28, 23, 30));
    expect(range).toEqual({
      start: "2026-07-28T00:00",
      end: "2026-07-28T23:30",
    });
  });
});
