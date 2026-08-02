import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type {
  Brief,
  BriefGenerationProgress,
  BriefSchedule,
  LLMConnection,
} from "../types";
import { BriefWorkspace, naturalPeriodRange } from "./BriefWorkspace";

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
) {
  vi.spyOn(api, "briefs").mockResolvedValue(briefs);
  vi.spyOn(api, "briefSchedules").mockResolvedValue(schedules);
  vi.spyOn(api, "llmConnections").mockResolvedValue(connections);
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
  afterEach(() => vi.restoreAllMocks());

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
      await screen.findByRole("button", { name: "从断点重试" }),
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
