import { type FormEvent, useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api";
import { t } from "../i18n";
import type {
  Brief,
  BriefConfiguration,
  BriefGenerationProgress,
  BriefPeriod,
  BriefRule,
  BriefSchedule,
  LLMConnection,
  Locale,
} from "../types";
import {
  downloadBlob,
  errorText,
  formatDateTime,
  uniqueRequestKey,
} from "../utils";
import {
  EmptyState,
  ErrorNotice,
  Modal,
  SelectMenu,
  Spinner,
  Toggle,
} from "./Common";

function localDateTimeValue(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function naturalPeriodRange(period: BriefPeriod, now = new Date()) {
  const start = new Date(now);
  start.setSeconds(0, 0);
  if (period === "daily") {
    start.setHours(0, 0, 0, 0);
  } else if (period === "weekly") {
    const mondayOffset = (start.getDay() + 6) % 7;
    start.setDate(start.getDate() - mondayOffset);
    start.setHours(0, 0, 0, 0);
  } else if (period === "monthly") {
    start.setDate(1);
    start.setHours(0, 0, 0, 0);
  } else {
    start.setMonth(0, 1);
    start.setHours(0, 0, 0, 0);
  }
  return {
    start: localDateTimeValue(start),
    end: localDateTimeValue(now),
  };
}

function BriefSummary({ markdown }: { markdown: string }) {
  return (
    <article className="brief-summary">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ node: _node, href, children, ...props }) => (
            <a
              {...props}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
            >
              {children}
            </a>
          ),
          img: ({ node: _node, alt, ...props }) => (
            <img
              {...props}
              alt={alt ?? ""}
              loading="lazy"
              referrerPolicy="no-referrer"
            />
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </article>
  );
}

export function BriefWorkspace({ locale, onBack, notify }: {
  locale: Locale;
  onBack: () => void;
  notify: (message: string, tone?: "success" | "error") => void;
}) {
  const [period, setPeriod] = useState<BriefPeriod>("daily");
  const [briefs, setBriefs] = useState<Brief[]>([]);
  const [schedules, setSchedules] = useState<BriefSchedule[]>([]);
  const [active, setActive] = useState<Brief | null>(null);
  const [connections, setConnections] = useState<LLMConnection[]>([]);
  const [configuration, setConfiguration] = useState<BriefConfiguration | null>(null);
  const [connectionId, setConnectionId] = useState("");
  const initialRange = naturalPeriodRange("daily");
  const [startAt, setStartAt] = useState(initialRange.start);
  const [endAt, setEndAt] = useState(initialRange.end);
  const [generating, setGenerating] = useState(false);
  const [retryingGeneration, setRetryingGeneration] = useState(false);
  const [generationProgress, setGenerationProgress] =
    useState<BriefGenerationProgress | null>(null);
  const [rule, setRule] = useState("");
  const [savedRule, setSavedRule] = useState("");
  const [ruleCustom, setRuleCustom] = useState(false);
  const [ruleModalOpen, setRuleModalOpen] = useState(false);
  const [ruleLoading, setRuleLoading] = useState(false);
  const [ruleError, setRuleError] = useState("");
  const [editingRule, setEditingRule] = useState(false);
  const [savingRule, setSavingRule] = useState(false);
  const [deletingBriefId, setDeletingBriefId] = useState<number | null>(null);
  const [scheduleName, setScheduleName] = useState("");
  const [cutoffTime, setCutoffTime] = useState("09:00");
  const [timezone, setTimezone] = useState(
    Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  );
  const [error, setError] = useState("");
  const zh = locale === "zh-CN";
  const periodLabel = {
    daily: zh ? "每日" : "Daily",
    weekly: zh ? "每周" : "Weekly",
    monthly: zh ? "每月" : "Monthly",
    yearly: zh ? "每年" : "Yearly",
  } satisfies Record<BriefPeriod, string>;
  const closeRuleModal = useCallback(() => {
    setRule(savedRule);
    setEditingRule(false);
    setRuleError("");
    setRuleModalOpen(false);
  }, [savedRule]);

  async function load() {
    try {
      const [items, plans, availableConnections, briefConfiguration] = await Promise.all([
        api.briefs(period),
        api.briefSchedules(),
        api.llmConnections(),
        api.briefConfiguration(),
      ]);
      setBriefs(items);
      setSchedules(plans);
      setConnections(availableConnections);
      setConfiguration(briefConfiguration);
      setConnectionId(
        briefConfiguration.llm_connection_id
          ? String(briefConfiguration.llm_connection_id)
          : availableConnections.length === 1
            ? String(availableConnections[0].id)
            : "",
      );
      const first = items[0] ?? null;
      setActive(first);
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  useEffect(() => {
    const range = naturalPeriodRange(period);
    setStartAt(range.start);
    setEndAt(range.end);
    void load();
  }, [period]);

  useEffect(() => {
    let cancelled = false;
    setGenerationProgress(null);
    void api
      .latestBriefGenerationProgress(period)
      .then((value) => {
        if (!cancelled && value) setGenerationProgress(value);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [period]);

  useEffect(() => {
    if (!generationProgress || generationProgress.status !== "running") return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const value = await api.briefGenerationProgress(
          generationProgress.idempotency_key,
        );
        if (cancelled) return;
        if (value.status === "completed" && value.brief_id) {
          const completedBrief = await api.brief(value.brief_id);
          if (cancelled) return;
          setGenerationProgress(value);
          setBriefs((current) => [
            completedBrief,
            ...current.filter((item) => item.id !== completedBrief.id),
          ]);
          setActive(completedBrief);
          setError("");
        } else {
          setGenerationProgress(value);
        }
        if (value.status === "failed") {
          setError(
            value.message ||
              (zh ? "简报生成失败。" : "Brief generation failed."),
          );
        }
      } catch {
        // Keep the last known state and retry. A slow or disconnected request
        // does not mean the server-side generation has failed.
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 750);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [generationProgress?.idempotency_key, generationProgress?.status]);

  async function generate() {
    if (!connectionId) {
      setError(
        zh
          ? "请先选择用于生成简报的 LLM 连接。"
          : "Choose an LLM connection for brief generation.",
      );
      return;
    }
    setGenerating(true);
    setGenerationProgress(null);
    setError("");
    let requestKey = "";
    try {
      if (configuration?.llm_connection_id !== Number(connectionId)) {
        const nextConfiguration = await api.setBriefConfiguration(Number(connectionId));
        setConfiguration(nextConfiguration);
      }
      requestKey = uniqueRequestKey("brief");
      setGenerationProgress({
        idempotency_key: requestKey,
        status: "running",
        stage: "preparing",
        completed: 0,
        total: 1,
      });
      const value = await api.createBrief({
        period,
        idempotency_key: requestKey,
        start_at: new Date(startAt).toISOString(),
        end_at: new Date(endAt).toISOString(),
        domain_ids: [],
        domain_match: "any",
      });
      setGenerationProgress({
        idempotency_key: requestKey,
        status: "completed",
        stage: "finalizing",
        completed: 1,
        total: 1,
        brief_id: value.id,
      });
      setBriefs((current) => [value, ...current]);
      setActive(value);
      notify(zh ? "简报已生成" : "Brief generated");
    } catch (caught) {
      const message = errorText(caught);
      if (requestKey) {
        try {
          const serverProgress = await api.briefGenerationProgress(requestKey);
          setGenerationProgress(serverProgress);
          setError(
            serverProgress.status === "running"
              ? (zh
                  ? "请求连接已中断，但后台仍在生成；请继续等待进度更新。"
                  : "The request disconnected, but generation is still running in the background.")
              : serverProgress.message || message,
          );
        } catch {
          setError(
            zh
              ? "连接已中断，暂时无法确认后台状态；系统会继续自动查询，请勿立即重试。"
              : "Disconnected and unable to confirm the background state. Progress checks will continue; do not retry yet.",
          );
        }
      } else {
        setError(message);
      }
    } finally {
      setGenerating(false);
    }
  }

  async function retryGeneration() {
    if (!generationProgress?.can_retry) return;
    const requestKey = generationProgress.idempotency_key;
    setRetryingGeneration(true);
    setError("");
    setGenerationProgress({
      ...generationProgress,
      status: "running",
      message: zh ? "正在从已保存的批次继续…" : "Resuming from saved batches…",
    });
    try {
      const value = await api.retryBriefGeneration(requestKey);
      setGenerationProgress({
        idempotency_key: requestKey,
        status: "completed",
        stage: "finalizing",
        completed: 1,
        total: 1,
        brief_id: value.id,
        can_retry: false,
        attempt: (generationProgress.attempt || 1) + 1,
      });
      setBriefs((current) => [
        value,
        ...current.filter((item) => item.id !== value.id),
      ]);
      setActive(value);
      notify(zh ? "简报已从断点继续并生成完成" : "Brief resumed and completed");
    } catch (caught) {
      const message = errorText(caught);
      try {
        const serverProgress = await api.briefGenerationProgress(requestKey);
        setGenerationProgress(serverProgress);
        setError(
          serverProgress.status === "running"
            ? (zh
                ? "连接已中断，但后台仍在从断点继续。"
                : "Disconnected, but the server is still resuming from checkpoints.")
            : serverProgress.message || message,
        );
      } catch {
        setError(message);
      }
    } finally {
      setRetryingGeneration(false);
    }
  }

  async function saveRule() {
    if (!rule.trim()) {
      setRuleError(zh ? "生成规则不能为空。" : "The generation rule cannot be empty.");
      return;
    }
    setSavingRule(true);
    setRuleError("");
    try {
      const value = await api.setBriefRule(rule);
      setRule(value.content);
      setSavedRule(value.content);
      setRuleCustom(value.is_custom);
      setEditingRule(false);
      notify(zh ? "生成规则已保存" : "Generation rule saved");
    } catch (caught) {
      setRuleError(errorText(caught));
    } finally {
      setSavingRule(false);
    }
  }

  async function restoreRule() {
    setSavingRule(true);
    setRuleError("");
    try {
      const value = await api.resetBriefRule();
      setRule(value.content);
      setSavedRule(value.content);
      setRuleCustom(value.is_custom);
      setEditingRule(false);
      notify(zh ? "已恢复默认规则" : "Default rule restored");
    } catch (caught) {
      setRuleError(errorText(caught));
    } finally {
      setSavingRule(false);
    }
  }

  async function openRuleModal() {
    setRuleModalOpen(true);
    setRuleLoading(true);
    setRuleError("");
    setEditingRule(false);
    try {
      const value = await api.briefRule();
      setRule(value.content);
      setSavedRule(value.content);
      setRuleCustom(value.is_custom);
    } catch (caught) {
      setRuleError(errorText(caught));
    } finally {
      setRuleLoading(false);
    }
  }

  async function removeBrief(item: Brief) {
    const confirmed = window.confirm(
      zh
        ? `确定删除简报“${item.title}”？此操作无法撤销。`
        : `Delete “${item.title}”? This cannot be undone.`,
    );
    if (!confirmed) return;
    setDeletingBriefId(item.id);
    setError("");
    try {
      await api.deleteBrief(item.id);
      const remaining = briefs.filter((brief) => brief.id !== item.id);
      setBriefs(remaining);
      if (active?.id === item.id) setActive(remaining[0] ?? null);
      notify(zh ? "简报已删除" : "Brief deleted");
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setDeletingBriefId(null);
    }
  }

  async function chooseConnection(value: string) {
    setConnectionId(value);
    setError("");
    try {
      setConfiguration(
        await api.setBriefConfiguration(value ? Number(value) : null),
      );
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  async function createSchedule(event: FormEvent) {
    event.preventDefault();
    try {
      await api.createBriefSchedule({
        name: scheduleName,
        period,
        timezone,
        cutoff_time: cutoffTime,
        weekday: period === "weekly" ? 0 : null,
        month_day: period === "monthly" || period === "yearly" ? 1 : null,
        year_month: period === "yearly" ? 1 : null,
        domain_ids: [],
        feed_ids: [],
        tag_ids: [],
        domain_match: "any",
        enabled: true,
      });
      setScheduleName("");
      await load();
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  const resetRange = () => {
    const range = naturalPeriodRange(period);
    setStartAt(range.start);
    setEndAt(range.end);
  };

  return (
    <main className="brief-workspace">
      <header className="brief-workspace__header">
        <div>
          <span className="eyebrow">LLM SYNTHESIS</span>
          <h1>{t(locale, "briefs")}</h1>
          <p>{zh ? "基于自定义时间范围生成综合分析，不逐篇罗列文章。" : "Synthesize a custom time range without listing articles one by one."}</p>
        </div>
        <button className="button button--secondary" onClick={onBack}>
          {zh ? "返回阅读" : "Back to reader"}
        </button>
      </header>
      {error && <ErrorNotice message={error} compact />}
      <div className="brief-toolbar">
        <div className="modal-tabs">
          {(["daily", "weekly", "monthly", "yearly"] as BriefPeriod[]).map(
            (value) => (
              <button
                className={period === value ? "is-active" : ""}
                onClick={() => setPeriod(value)}
                key={value}
              >
                {value}
              </button>
            ),
          )}
        </div>
        <div className="brief-toolbar__actions">
          <button
            className="button button--secondary"
            onClick={() => void openRuleModal()}
          >
            {zh ? "查看规则" : "View rule"}
          </button>
          <SelectMenu
            compact
            value={connectionId}
            onChange={(value) => void chooseConnection(value)}
            label={zh ? "简报 LLM 连接" : "Brief LLM connection"}
            placeholder={zh ? "选择 LLM 连接" : "Choose LLM connection"}
            options={connections.map((connection) => ({
              value: String(connection.id),
              label: `${connection.name} · ${connection.model}`,
            }))}
          />
          <button
            className="button button--primary"
            disabled={
              !connectionId ||
              generating ||
              generationProgress?.status === "running"
            }
            onClick={() => void generate()}
          >
            {generating
              ? (zh ? "正在生成…" : "Generating…")
              : t(locale, "manualBrief")}
          </button>
        </div>
      </div>
      {generationProgress && (
        <section
          className={`brief-generation-progress brief-generation-progress--${generationProgress.status}`}
          aria-live="polite"
        >
          <div className="brief-generation-progress__copy">
            <strong>
              {generationProgress.status === "completed"
                ? (zh ? "简报生成完成" : "Brief complete")
                : generationProgress.status === "failed"
                  ? (zh ? "简报生成失败" : "Brief generation failed")
                  : generationProgress.stage === "summarizing_batches"
                    ? (zh ? "正在分批总结条目" : "Summarizing article batches")
                    : generationProgress.stage === "consolidating"
                      ? (zh ? "正在合并各批结果" : "Consolidating batch results")
                      : generationProgress.stage === "finalizing"
                        ? (zh ? "正在生成最终简报" : "Writing the final brief")
                        : (zh ? "正在准备输入内容" : "Preparing source content")}
            </strong>
            <span>
              {generationProgress.message ||
                (generationProgress.status === "running"
                  ? `${generationProgress.completed} / ${Math.max(generationProgress.total, 1)}`
                  : "")}
            </span>
          </div>
          <progress
            aria-label={zh ? "简报生成进度" : "Brief generation progress"}
            max={Math.max(generationProgress.total, 1)}
            value={
              generationProgress.status === "completed"
                ? Math.max(generationProgress.total, 1)
                : generationProgress.completed
            }
          />
          {generationProgress.status === "failed" && (
            <div className="brief-generation-progress__actions">
              {generationProgress.can_retry ? (
                <button
                  className="button button--primary button--small"
                  disabled={retryingGeneration}
                  onClick={() => void retryGeneration()}
                >
                  {retryingGeneration
                    ? (zh ? "正在继续…" : "Resuming…")
                    : (zh ? "从断点重试" : "Retry from checkpoint")}
                </button>
              ) : (
                <span>
                  {zh
                    ? "这是旧版生成任务，没有保存批次检查点；请重新生成。"
                    : "This older task has no saved checkpoints; start a new generation."}
                </span>
              )}
            </div>
          )}
        </section>
      )}
      <section className="brief-range-panel" aria-label={zh ? "简报时间范围" : "Brief time range"}>
        <label className="field">
          <span>{zh ? "开始时间" : "Start time"}</span>
          <input type="datetime-local" required value={startAt} onChange={(event) => setStartAt(event.target.value)} />
        </label>
        <label className="field">
          <span>{zh ? "结束时间" : "End time"}</span>
          <input type="datetime-local" required value={endAt} onChange={(event) => setEndAt(event.target.value)} />
        </label>
        <button className="button button--secondary" onClick={resetRange}>
          {zh ? "恢复当前周期" : "Use current period"}
        </button>
      </section>
      {connections.length === 0 && (
        <p className="brief-connection-hint">
          {zh
            ? "请先在设置中添加 LLM 连接，再生成简报。"
            : "Add an LLM connection in Settings before generating a brief."}
        </p>
      )}
      {briefs.length === 0 ? (
        <EmptyState
          title={locale === "zh-CN" ? "还没有简报" : "No briefs yet"}
          description={
            locale === "zh-CN"
              ? "选择一个 LLM 连接，生成当前周期的综合分析。"
              : "Choose an LLM connection to synthesize this period."
          }
        />
      ) : (
        <div className="brief-layout">
          <aside className="brief-list">
            {briefs.map((item) => (
              <button
                className={active?.id === item.id ? "is-active" : ""}
                key={item.id}
                onClick={() => setActive(item)}
              >
                <span>{item.title}</span>
                <small>
                  {formatDateTime(item.start_at, locale)} -{" "}
                  {formatDateTime(item.end_at, locale)}
                </small>
                <strong>
                  {item.item_count} {zh ? "篇输入" : "inputs"}
                </strong>
              </button>
            ))}
          </aside>
          {active && (
            <section className="brief-editor">
              <header>
                <div>
                  <h3>{active.title}</h3>
                  <p>
                    {zh
                      ? `综合分析 ${active.item_count} 篇输入内容`
                      : `Synthesis of ${active.item_count} inputs`}
                  </p>
                </div>
              </header>
              <BriefSummary markdown={active.notes} />
              <div className="brief-stats">
                {Object.entries(active.stats)
                  .slice(0, 4)
                  .map(([key, value]) => (
                    <div key={key}>
                      <strong>{value}</strong>
                      <span>
                        {key === "entries"
                          ? (zh ? "输入文章" : "Input articles")
                          : key === "feeds"
                            ? (zh ? "来源" : "Sources")
                            : key === "analyzed_entries"
                              ? (zh ? "纳入分析" : "Analyzed")
                              : key}
                      </span>
                    </div>
                  ))}
              </div>
              <footer>
                <button
                  className="button button--danger-quiet"
                  disabled={deletingBriefId === active.id}
                  onClick={() => void removeBrief(active)}
                >
                  {deletingBriefId === active.id
                    ? (zh ? "正在删除…" : "Deleting…")
                    : (zh ? "删除简报" : "Delete brief")}
                </button>
                <button
                  className="button button--secondary"
                  onClick={() =>
                    void api
                      .exportBrief(active.id)
                      .then((blob) =>
                        downloadBlob(
                          blob,
                          `affogato-rss-reader-${active.period}-${active.id}.md`,
                        ),
                      )
                  }
                >
                  {t(locale, "exportMarkdown")}
                </button>
              </footer>
            </section>
          )}
        </div>
      )}
      {ruleModalOpen && (
        <Modal
          title={zh ? "生成规则" : "Generation rule"}
          eyebrow="RULE.MD"
          onClose={closeRuleModal}
          wide
        >
          <section className="brief-rule-dialog">
            {ruleLoading ? (
              <Spinner label={zh ? "正在加载生成规则…" : "Loading generation rule…"} />
            ) : (
              <>
                {ruleError && <ErrorNotice message={ruleError} compact />}
                <header>
                  <p>
                    {ruleCustom
                      ? (zh ? "当前使用你保存的自定义规则。" : "Your saved custom rule is active.")
                      : (zh ? "当前使用项目默认规则。" : "The project default rule is active.")}
                  </p>
                  <div>
                    {ruleCustom && (
                      <button className="button button--ghost" disabled={savingRule} onClick={() => void restoreRule()}>
                        {zh ? "恢复默认" : "Restore default"}
                      </button>
                    )}
                    <button
                      className="button button--secondary"
                      onClick={() => {
                        if (editingRule) setRule(savedRule);
                        setEditingRule((value) => !value);
                      }}
                    >
                      {editingRule ? (zh ? "取消修改" : "Cancel editing") : (zh ? "修改规则" : "Edit rule")}
                    </button>
                    {editingRule && (
                      <button className="button button--primary" disabled={savingRule} onClick={() => void saveRule()}>
                        {savingRule ? (zh ? "保存中…" : "Saving…") : (zh ? "保存规则" : "Save rule")}
                      </button>
                    )}
                  </div>
                </header>
                {editingRule ? (
                  <textarea
                    className="brief-rule-editor"
                    aria-label={zh ? "简报生成规则" : "Brief generation rule"}
                    value={rule}
                    onChange={(event) => setRule(event.target.value)}
                  />
                ) : (
                  <pre className="brief-rule-reader">{rule}</pre>
                )}
              </>
            )}
          </section>
        </Modal>
      )}
      <hr />
      <section className="settings-section brief-schedules">
        <span className="eyebrow">SCHEDULES</span>
        <h3>{t(locale, "schedules")}</h3>
        <p className="brief-schedules__intro">
          {zh ? "按当前简报周期自动生成内容，生成时间以所选时区为准。" : "Generate this brief period automatically using the selected timezone."}
        </p>
        <form className="brief-schedule-form" onSubmit={createSchedule}>
          <label className="field">
            <span>{zh ? "计划名称" : "Schedule name"}</span>
            <input
              required
              value={scheduleName}
              placeholder={zh ? `例如：${periodLabel[period]}晨报` : `e.g. ${periodLabel[period]} digest`}
              onChange={(event) => setScheduleName(event.target.value)}
            />
          </label>
          <label className="field">
            <span>{zh ? "生成时间" : "Run time"}</span>
            <input
              required
              type="time"
              value={cutoffTime}
              onChange={(event) => setCutoffTime(event.target.value)}
            />
          </label>
          <label className="field">
            <span>{zh ? "时区" : "Timezone"}</span>
            <input
              required
              value={timezone}
              onChange={(event) => setTimezone(event.target.value)}
            />
          </label>
          <button type="submit" className="button button--primary brief-schedule-form__submit">
            <span aria-hidden="true">+</span>
            {zh ? "添加计划" : "Add schedule"}
          </button>
        </form>
        {schedules.length === 0 ? (
          <div className="brief-schedules__empty">
            <strong>{zh ? "还没有自动计划" : "No schedules yet"}</strong>
            <span>{zh ? "填写上方信息，添加第一个简报计划。" : "Use the form above to add your first brief schedule."}</span>
          </div>
        ) : (
          <div className="brief-schedule-list">
            {schedules.map((schedule) => (
              <div className="brief-schedule-row" key={schedule.id}>
                <span className="brief-schedule-row__mark" aria-hidden="true">AUTO</span>
                <div className="brief-schedule-row__main">
                  <strong>{schedule.name}</strong>
                  <small>
                    {periodLabel[schedule.period]} · {schedule.cutoff_time} · {schedule.timezone}
                  </small>
                </div>
                <Toggle
                  checked={schedule.enabled}
                  label={schedule.enabled ? (zh ? "已启用" : "Enabled") : (zh ? "已停用" : "Disabled")}
                  onChange={(enabled) =>
                    void api
                      .updateBriefSchedule(schedule.id, { enabled })
                      .then(load)
                  }
                />
              </div>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
