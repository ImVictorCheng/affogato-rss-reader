import { type ChangeEvent, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { composeSiteIdentity, customizeSiteIdentity, supportsGeneratedIdentity } from "../domainThemes";
import { t } from "../i18n";
import type { AppSettings, AuthStatus, CallLog, Feed, Job, LLMConnection, Locale, NetworkProxy, NetworkProxyTestResult, OnboardingProfile, ProxyMode, SiteIdentity, ThemeConfig, TranslationFallbackMode, TranslationProvider, TranslationProxyService, TranslationStatus, UpdateStatus } from "../types";
import { errorText, formatDateTime } from "../utils";
import { Brand, ErrorNotice, Modal, SelectMenu, Spinner, Toggle } from "./Common";

const MAX_LOGO_BYTES = 256 * 1024;
type SettingsPage = "home" | "appearance" | "llm" | "proxy" | "translation" | "activity" | "account";
const TRANSLATION_PROXY_TARGETS: { id: TranslationProxyService; name: string; meta: string }[] = [
  { id: "google-gtx", name: "Google GTX", meta: "translate.googleapis.com" },
  { id: "deepl", name: "DeepL API", meta: "api.deepl.com" },
  { id: "google-cloud", name: "Google Cloud Translation", meta: "translation.googleapis.com" },
];

export function SettingsModal({ locale, auth, onLocale, onClose, onLogout, onDebugReset, onBrandChanged, onInstallUpdate, notify }: {
  locale: Locale; auth: AuthStatus; onLocale: (locale: Locale) => void; onClose: () => void; onLogout: () => void;
  onDebugReset: () => void;
  onBrandChanged: (theme: ThemeConfig) => void;
  onInstallUpdate: () => Promise<void>;
  notify: (message: string, tone?: "success" | "error") => void;
}) {
  const [settingsPage, setSettingsPage] = useState<SettingsPage>("home");
  const [translation, setTranslation] = useState<TranslationStatus | null>(null);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [callLogs, setCallLogs] = useState<CallLog[]>([]);
  const [callLogFile, setCallLogFile] = useState("logs/llm-translation.jsonl");
  const [callLogCategory, setCallLogCategory] = useState<"all" | "llm" | "translation">("all");
  const [callLogsLoading, setCallLogsLoading] = useState(false);
  const [profile, setProfile] = useState<OnboardingProfile | null>(null);
  const [target, setTarget] = useState("zh-CN");
  const [translationProvider, setTranslationProvider] = useState<TranslationProvider>("google-gtx");
  const [fallbackMode, setFallbackMode] = useState<TranslationFallbackMode>("automatic");
  const [llmConnections, setLlmConnections] = useState<LLMConnection[]>([]);
  const [translationLlmConnectionId, setTranslationLlmConnectionId] = useState("");
  const [llmConnectionChoice, setLlmConnectionChoice] = useState("new");
  const [llmConnectionName, setLlmConnectionName] = useState("");
  const [llmBaseUrl, setLlmBaseUrl] = useState("https://api.openai.com/v1");
  const [llmModel, setLlmModel] = useState("gpt-4o-mini");
  const [llmApiKey, setLlmApiKey] = useState("");
  const [clearLlmApiKey, setClearLlmApiKey] = useState(false);
  const [llmSaving, setLlmSaving] = useState(false);
  const [llmTesting, setLlmTesting] = useState(false);
  const [llmTest, setLlmTest] = useState<{ tone: "success" | "error"; message: string } | null>(null);
  const [feeds, setFeeds] = useState<Feed[]>([]);
  const [networkProxy, setNetworkProxy] = useState<NetworkProxy | null>(null);
  const [proxyEnabled, setProxyEnabled] = useState(false);
  const [proxyUrl, setProxyUrl] = useState("");
  const [proxyUsername, setProxyUsername] = useState("");
  const [proxyPassword, setProxyPassword] = useState("");
  const [clearProxyPassword, setClearProxyPassword] = useState(false);
  const [proxyGlobalMode, setProxyGlobalMode] = useState<ProxyMode>("direct");
  const [proxyFeedModes, setProxyFeedModes] = useState<Record<number, ProxyMode>>({});
  const [proxyLlmConnectionModes, setProxyLlmConnectionModes] = useState<Record<number, ProxyMode>>({});
  const [proxyTranslationServiceModes, setProxyTranslationServiceModes] = useState<Record<TranslationProxyService, ProxyMode>>({
    "google-gtx": "direct",
    deepl: "direct",
    "google-cloud": "direct",
  });
  const [proxySaving, setProxySaving] = useState(false);
  const [proxyTesting, setProxyTesting] = useState(false);
  const [proxyTest, setProxyTest] = useState<(NetworkProxyTestResult & { error?: string }) | null>(null);
  const [appUpdate, setAppUpdate] = useState<UpdateStatus | null>(null);
  const [updateChecking, setUpdateChecking] = useState(false);
  const [updateInstalling, setUpdateInstalling] = useState(false);
  const [deeplEndpoint, setDeeplEndpoint] = useState("https://api-free.deepl.com/v2/translate");
  const [deeplApiKey, setDeeplApiKey] = useState("");
  const [clearDeeplApiKey, setClearDeeplApiKey] = useState(false);
  const [googleCloudApiKey, setGoogleCloudApiKey] = useState("");
  const [clearGoogleCloudApiKey, setClearGoogleCloudApiKey] = useState(false);
  const [translationSaving, setTranslationSaving] = useState(false);
  const [translationTesting, setTranslationTesting] = useState(false);
  const [translationRetrying, setTranslationRetrying] = useState(false);
  const [translationTest, setTranslationTest] = useState<{ tone: "success" | "error"; message: string } | null>(null);
  const [brandName, setBrandName] = useState("");
  const [brandLogo, setBrandLogo] = useState("");
  const [brandLogoName, setBrandLogoName] = useState("");
  const [brandSaving, setBrandSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (settingsPage === "appearance") {
      setLoading(true);
      void Promise.all([api.settings(), api.onboarding()])
        .then(([settings, profile]) => {
          const identity = profile.theme?.identity || composeSiteIdentity(profile.selected_domains, locale);
          setSettings(settings); setProfile(profile);
          setBrandName(identity.name);
          setBrandLogo(identity.logo_data_url || "");
          setBrandLogoName(identity.logo_kind === "upload" ? (locale === "zh-CN" ? "当前自定义 Logo" : "Current custom logo") : "");
        })
        .catch((caught) => setError(errorText(caught)))
        .finally(() => setLoading(false));
      return;
    }
    if (settingsPage === "account") {
      setLoading(true);
      void Promise.all([api.settings(), api.updateStatus()])
        .then(([settings, update]) => { setSettings(settings); setAppUpdate(update); })
        .catch((caught) => setError(errorText(caught)))
        .finally(() => setLoading(false));
      return;
    }
    if (settingsPage === "llm") {
      void api.llmConnections().then(applyLlmConnections).catch((caught) => setError(errorText(caught)));
      return;
    }
    if (settingsPage === "proxy") {
      void Promise.all([api.networkProxy(), api.feeds(), api.llmConnections()])
        .then(([proxy, feeds, connections]) => {
          applyNetworkProxy(proxy);
          setFeeds(feeds);
          applyLlmConnections(connections);
        })
        .catch((caught) => setError(errorText(caught)));
      return;
    }
    if (settingsPage !== "translation") return;
    const refresh = async () => {
      try {
        const translation = await api.translationStatus();
        applyTranslationStatus(translation);
        applyLlmConnections(translation.llm_connections, translation.llm_connection_id);
      } catch {
        // Keep the last known settings during a transient refresh failure.
      }
    };
    void refresh();
    const timer = window.setInterval(() => {
      void api.translationStatus().then(applyTranslationStatus).catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [settingsPage, locale]);
  useEffect(() => {
    if (settingsPage !== "activity") return;
    void api.jobs(20).then(setJobs).catch((caught) => setError(errorText(caught)));
  }, [settingsPage]);
  useEffect(() => {
    if (settingsPage !== "activity") return;
    void loadCallLogs(true);
    const timer = window.setInterval(() => void loadCallLogs(), 10000);
    return () => window.clearInterval(timer);
  }, [settingsPage, callLogCategory]);
  const brandFallback = useMemo(
    () => composeSiteIdentity(profile?.selected_domains || [], locale),
    [locale, profile?.selected_domains],
  );
  const draftIdentity = useMemo(
    () => customizeSiteIdentity(brandFallback, brandName, brandLogo),
    [brandFallback, brandLogo, brandName],
  );
  const selectedLlmConnection = llmConnectionChoice === "new"
    ? null
    : llmConnections.find((connection) => String(connection.id) === llmConnectionChoice) || null;
  const llmKeyConfigured = selectedLlmConnection?.api_key_configured ?? false;
  function applyTranslationStatus(value: TranslationStatus) {
    setTranslation(value);
    setTarget(value.target_language);
    setTranslationProvider(value.provider || "google-gtx");
    setFallbackMode(value.fallback_mode || "automatic");
    setTranslationLlmConnectionId(value.llm_connection_id ? String(value.llm_connection_id) : "");
    setDeeplEndpoint(value.deepl_endpoint || "https://api-free.deepl.com/v2/translate");
  }
  function applyLlmConnections(connections: LLMConnection[], preferredId?: number | null) {
    setLlmConnections(connections);
    const selected = connections.find((connection) => connection.id === preferredId)
      || connections.find((connection) => String(connection.id) === llmConnectionChoice)
      || connections[0];
    if (selected) {
      setLlmConnectionChoice(String(selected.id));
      setLlmConnectionName(selected.name);
      setLlmBaseUrl(selected.base_url);
      setLlmModel(selected.model);
    }
  }
  function applyNetworkProxy(proxy: NetworkProxy) {
    setNetworkProxy(proxy);
    setProxyEnabled(proxy.enabled);
    setProxyUrl(proxy.url);
    setProxyUsername(proxy.username || "");
    setProxyGlobalMode(proxy.global_mode || "direct");
    setProxyFeedModes(proxy.feed_modes);
    setProxyLlmConnectionModes(proxy.llm_connection_modes);
    setProxyTranslationServiceModes(proxy.translation_service_modes);
  }
  async function loadCallLogs(showLoading = false) {
    if (showLoading) setCallLogsLoading(true);
    try {
      const result = await api.callLogs({
        category: callLogCategory === "all" ? undefined : callLogCategory,
        limit: 100,
      });
      setCallLogs(result.items);
      setCallLogFile(result.host_path_hint || result.file_path);
    } catch (caught) {
      if (showLoading) setError(errorText(caught));
    } finally {
      if (showLoading) setCallLogsLoading(false);
    }
  }
  function chooseLlmConnection(value: string) {
    setLlmConnectionChoice(value);
    setLlmApiKey("");
    setClearLlmApiKey(false);
    setLlmTest(null);
    if (value === "new") {
      setLlmConnectionName("");
      setLlmBaseUrl("https://api.openai.com/v1");
      setLlmModel("gpt-4o-mini");
      return;
    }
    const connection = llmConnections.find((item) => String(item.id) === value);
    if (connection) {
      setLlmConnectionName(connection.name);
      setLlmBaseUrl(connection.base_url);
      setLlmModel(connection.model);
    }
  }
  async function saveLlmConnection() {
    setLlmSaving(true);
    try {
      const saved = llmConnectionChoice === "new"
        ? await api.createLlmConnection({
          name: llmConnectionName.trim(),
          base_url: llmBaseUrl,
          model: llmModel.trim(),
          api_key: llmApiKey,
        })
        : await api.updateLlmConnection(Number(llmConnectionChoice), {
          name: llmConnectionName.trim(),
          base_url: llmBaseUrl,
          model: llmModel.trim(),
          api_key: llmApiKey || undefined,
          clear_api_key: clearLlmApiKey,
        });
      const connections = await api.llmConnections();
      setLlmConnections(connections);
      setLlmConnectionChoice(String(saved.id));
      setLlmConnectionName(saved.name);
      setLlmBaseUrl(saved.base_url);
      setLlmModel(saved.model);
      setLlmApiKey("");
      setClearLlmApiKey(false);
      if (!translationLlmConnectionId) setTranslationLlmConnectionId(String(saved.id));
      notify(locale === "zh-CN" ? "LLM 连接已保存。" : "LLM connection saved.");
    } catch (caught) {
      notify(errorText(caught), "error");
    } finally {
      setLlmSaving(false);
    }
  }
  async function testLlmConnection() {
    setLlmTesting(true);
    setLlmTest(null);
    try {
      const result = await api.testLlmConnection({
        connection_id: llmConnectionChoice === "new" ? undefined : Number(llmConnectionChoice),
        base_url: llmBaseUrl,
        model: llmModel,
        api_key: llmApiKey || undefined,
      });
      setLlmTest({
        tone: "success",
        message: locale === "zh-CN"
          ? `调用成功（${result.elapsed_ms} ms）：${result.response_text}`
          : `Call succeeded (${result.elapsed_ms} ms): ${result.response_text}`,
      });
    } catch (caught) {
      setLlmTest({
        tone: "error",
        message: locale === "zh-CN" ? `调用失败：${errorText(caught)}` : `Call failed: ${errorText(caught)}`,
      });
    } finally {
      setLlmTesting(false);
    }
  }
  async function removeLlmConnection() {
    if (!selectedLlmConnection) return;
    const warning = locale === "zh-CN"
      ? `删除 LLM 连接“${selectedLlmConnection.name}”？此操作无法撤销。`
      : `Delete LLM connection “${selectedLlmConnection.name}”? This cannot be undone.`;
    if (!window.confirm(warning)) return;
    try {
      await api.deleteLlmConnection(selectedLlmConnection.id);
      const connections = llmConnections.filter((connection) => connection.id !== selectedLlmConnection.id);
      setLlmConnections(connections);
      setProxyLlmConnectionModes((modes) => {
        const next = { ...modes };
        delete next[selectedLlmConnection.id];
        return next;
      });
      chooseLlmConnection(connections[0] ? String(connections[0].id) : "new");
      if (translationLlmConnectionId === String(selectedLlmConnection.id)) setTranslationLlmConnectionId("");
      notify(locale === "zh-CN" ? "LLM 连接已删除。" : "LLM connection deleted.");
    } catch (caught) {
      notify(errorText(caught), "error");
    }
  }
  async function saveNetworkProxy() {
    setProxySaving(true);
    try {
      const updated = await api.setNetworkProxy({
        enabled: proxyEnabled,
        url: proxyUrl,
        username: proxyUsername || undefined,
        password: proxyPassword || undefined,
        clear_password: clearProxyPassword,
        global_mode: proxyGlobalMode,
        feed_modes: Object.fromEntries(feeds.map((feed) => [feed.id, proxyFeedModes[feed.id] || "direct"])),
        llm_connection_modes: Object.fromEntries(llmConnections.map((connection) => [connection.id, proxyLlmConnectionModes[connection.id] || "direct"])),
        translation_service_modes: Object.fromEntries(TRANSLATION_PROXY_TARGETS.map((service) => [service.id, proxyTranslationServiceModes[service.id] || "direct"])) as Record<TranslationProxyService, ProxyMode>,
      });
      setNetworkProxy(updated);
      setProxyPassword("");
      setClearProxyPassword(false);
      notify(locale === "zh-CN" ? "网络代理设置已保存。" : "Network proxy settings saved.");
    } catch (caught) {
      notify(errorText(caught), "error");
    } finally {
      setProxySaving(false);
    }
  }
  async function testNetworkProxy() {
    setProxyTesting(true);
    setProxyTest(null);
    try {
      const result = await api.testNetworkProxy({
        url: proxyUrl,
        username: proxyUsername || undefined,
        password: proxyPassword || undefined,
        use_saved_password: !clearProxyPassword,
      });
      setProxyTest(result);
    } catch (caught) {
      setProxyTest({
        results: [],
        error: locale === "zh-CN" ? `代理测试失败：${errorText(caught)}` : `Proxy test failed: ${errorText(caught)}`,
      });
    } finally {
      setProxyTesting(false);
    }
  }
  async function checkForUpdates() {
    setUpdateChecking(true);
    try {
      const update = await api.checkForUpdates();
      setAppUpdate(update);
      notify(update.downloaded
        ? (locale === "zh-CN" ? `版本 ${update.latest_version} 已下载。` : `Version ${update.latest_version} is downloaded.`)
        : (locale === "zh-CN" ? "更新检查已完成。" : "Update check completed."));
    } catch (caught) {
      notify(errorText(caught), "error");
    } finally {
      setUpdateChecking(false);
    }
  }
  async function installUpdate() {
    setUpdateInstalling(true);
    try {
      await onInstallUpdate();
    } finally {
      setUpdateInstalling(false);
    }
  }
  async function saveTranslation(enabled = translation?.enabled ?? false) {
    if (enabled && !(translation?.enabled) && !window.confirm(t(locale, "translationWarning"))) return;
    setTranslationSaving(true);
    try {
      const updated = await api.setTranslation({
        enabled,
        target_language: target,
        provider: translationProvider,
        fallback_mode: fallbackMode,
        llm_connection_id: translationProvider === "custom-llm" && translationLlmConnectionId ? Number(translationLlmConnectionId) : undefined,
        deepl_endpoint: deeplEndpoint,
        deepl_api_key: deeplApiKey || undefined,
        clear_deepl_api_key: clearDeeplApiKey,
        google_cloud_api_key: googleCloudApiKey || undefined,
        clear_google_cloud_api_key: clearGoogleCloudApiKey,
      });
      setTranslation(updated);
      setLlmConnections(await api.llmConnections());
      setTranslationLlmConnectionId(updated.llm_connection_id ? String(updated.llm_connection_id) : "");
      setDeeplApiKey(""); setGoogleCloudApiKey("");
      setClearDeeplApiKey(false); setClearGoogleCloudApiKey(false);
      notify(locale === "zh-CN" ? "翻译设置已保存。" : "Translation settings saved.");
    } catch (caught) {
      notify(errorText(caught), "error");
    } finally {
      setTranslationSaving(false);
    }
  }
  async function retryFailedTranslations() {
    setTranslationRetrying(true);
    try {
      await api.retryTranslations();
      setTranslation(await api.translationStatus());
      notify(locale === "zh-CN" ? "失败任务已重新排队。" : "Failed translations queued.");
    } catch (caught) {
      notify(errorText(caught), "error");
    } finally {
      setTranslationRetrying(false);
    }
  }
  async function testTranslationConnection() {
    setTranslationTesting(true);
    setTranslationTest(null);
    try {
      const result = await api.testTranslation({
        provider: translationProvider,
        target_language: target,
        llm_connection_id: translationProvider === "custom-llm" && translationLlmConnectionId ? Number(translationLlmConnectionId) : undefined,
        deepl_endpoint: deeplEndpoint,
        deepl_api_key: deeplApiKey || undefined,
        google_cloud_api_key: googleCloudApiKey || undefined,
      });
      setTranslationTest({
        tone: "success",
        message: locale === "zh-CN"
          ? `调用成功（${result.elapsed_ms} ms）：${result.translated_text}`
          : `Call succeeded (${result.elapsed_ms} ms): ${result.translated_text}`,
      });
    } catch (caught) {
      setTranslationTest({
        tone: "error",
        message: locale === "zh-CN"
          ? `调用失败：${errorText(caught)}`
          : `Call failed: ${errorText(caught)}`,
      });
    } finally {
      setTranslationTesting(false);
    }
  }
  async function resetDebugOwner() {
    const warning = locale === "zh-CN"
      ? "确定要删除当前 owner 吗？这会注销所有设备，并删除 owner 密码、阅读状态和个性化设置。订阅源、文章、标签和领域会保留。此操作无法撤销。"
      : "Delete the current owner? This signs out every device and removes the owner password, reading states, and personalization. Feeds, articles, tags, and domains are kept. This cannot be undone.";
    if (!window.confirm(warning)) return;
    setResetting(true);
    try {
      await api.debugDeleteOwner();
      onDebugReset();
    } catch (caught) {
      notify(errorText(caught), "error");
      setResetting(false);
    }
  }
  function chooseBrandLogo(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      notify(locale === "zh-CN" ? "Logo 仅支持 PNG、JPEG 或 WebP 图片。" : "Logo must be a PNG, JPEG, or WebP image.", "error");
      return;
    }
    if (file.size > MAX_LOGO_BYTES) {
      notify(locale === "zh-CN" ? "Logo 图片不能超过 256 KB。" : "Logo image must be 256 KB or smaller.", "error");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      if (!/^data:image\/(?:png|jpeg|webp);base64,/.test(result)) {
        notify(locale === "zh-CN" ? "无法读取这个 Logo 文件。" : "This logo file could not be read.", "error");
        return;
      }
      setBrandLogo(result);
      setBrandLogoName(file.name);
    };
    reader.onerror = () => notify(locale === "zh-CN" ? "无法读取这个 Logo 文件。" : "This logo file could not be read.", "error");
    reader.readAsDataURL(file);
  }
  async function saveBrand(identity: SiteIdentity = draftIdentity) {
    if (!profile?.theme || !profile.primary_domain) return;
    setBrandSaving(true);
    try {
      const updated = await api.completeOnboarding({
        selected_domains: profile.selected_domains,
        primary_domain: profile.primary_domain,
        theme: { ...profile.theme, identity },
        ai_personalized: profile.ai_personalized,
        ai_provider: profile.ai_provider || undefined,
      });
      setProfile(updated);
      setBrandName(identity.name);
      setBrandLogo(identity.logo_data_url || "");
      setBrandLogoName(identity.logo_kind === "upload" ? brandLogoName : "");
      onBrandChanged(updated.theme!);
      notify(locale === "zh-CN" ? "站点品牌已保存。" : "Site branding saved.");
    } catch (caught) {
      notify(errorText(caught), "error");
    } finally {
      setBrandSaving(false);
    }
  }
  async function restoreBrand() {
    setBrandName(brandFallback.name);
    setBrandLogo("");
    setBrandLogoName("");
    await saveBrand(brandFallback);
  }
  return <Modal title={t(locale, "settings")} eyebrow="APPLICATION" onClose={onClose} wide>{loading ? <Spinner /> : error ? <ErrorNotice message={error} /> : <div className="settings-layout">
    {settingsPage === "home" && <div className="settings-home">
      <p className="settings-home__intro">{locale === "zh-CN" ? "选择一项设置。每个类别会在独立页面中打开。" : "Choose a category. Each group opens on its own page."}</p>
      <div className="settings-card-grid">
        {([
          ["appearance", "APPEARANCE", locale === "zh-CN" ? "外观与语言" : "Appearance & language", locale === "zh-CN" ? "界面语言、站点名称和 Logo" : "Interface language, site name, and logo"],
          ["llm", "LLM", locale === "zh-CN" ? "LLM 连接" : "LLM connections", locale === "zh-CN" ? "添加、测试和管理模型连接" : "Add, test, and manage model connections"],
          ["proxy", "NETWORK", locale === "zh-CN" ? "网络代理" : "Network proxy", locale === "zh-CN" ? "设置应用全局代理与各功能独立路由" : "Set the application-wide route and feature overrides"],
          ["translation", "TRANSLATION", locale === "zh-CN" ? "翻译" : "Translation", locale === "zh-CN" ? "翻译服务、目标语言和回退方式" : "Provider, target language, and fallback"],
          ["activity", "ACTIVITY", locale === "zh-CN" ? "活动、日志与快捷键" : "Activity, logs & shortcuts", locale === "zh-CN" ? "最近任务、LLM/翻译调用日志与键盘说明" : "Recent jobs, LLM/translation call logs, and keyboard controls"],
          ["account", "ACCOUNT", locale === "zh-CN" ? "账户与系统" : "Account & system", locale === "zh-CN" ? "应用更新、版本、退出登录与调试工具" : "Updates, version, sign out, and debug tools"],
        ] as const).map(([page, eyebrow, title, description]) => <button type="button" className="settings-nav-card" key={page} onClick={() => setSettingsPage(page)}>
          <span className="eyebrow">{eyebrow}</span><strong>{title}</strong><small>{description}</small><span className="settings-nav-card__arrow" aria-hidden="true">→</span>
        </button>)}
      </div>
    </div>}
    {settingsPage !== "home" && <div className="settings-subpage__header">
      <button type="button" className="settings-back-button" onClick={() => setSettingsPage("home")}>← {locale === "zh-CN" ? "返回设置" : "Back to settings"}</button>
    </div>}
    {settingsPage === "appearance" && <>
    {auth.mode === "none" && <section className="settings-section"><div className="security-warning"><strong>{t(locale, "security")}</strong><p>{t(locale, "noAuthWarning")}</p></div></section>}
    <section className="settings-section"><span className="eyebrow">LANGUAGE</span><h3>{t(locale, "interfaceLanguage")}</h3><div className="settings-select"><SelectMenu value={locale} onChange={(value) => onLocale(value as Locale)} label={t(locale, "interfaceLanguage")} options={[{ value: "zh-CN", label: "简体中文" }, { value: "en", label: "English" }]} /></div></section>
    {profile?.theme && <section className="settings-section brand-settings">
      <div className="section-heading"><div><span className="eyebrow">SITE IDENTITY</span><h3>{locale === "zh-CN" ? "站点名称与 Logo" : "Site name and logo"}</h3></div></div>
      <p className="provider-warning">{locale === "zh-CN" ? "模板品牌只是默认建议，名称和 Logo 可以随时分别修改。" : "Template branding is only a default suggestion. You can change the name and logo independently at any time."}</p>
      <div className="brand-settings__panel">
        <div className="brand-settings__preview"><span>{locale === "zh-CN" ? "实时预览" : "LIVE PREVIEW"}</span><Brand identity={draftIdentity} /></div>
        <div className="brand-settings__fields">
          <label className="field"><span>{locale === "zh-CN" ? "站点名" : "Site name"}</span><input value={brandName} onChange={(event) => setBrandName(event.target.value)} maxLength={120} placeholder={brandFallback.name} /></label>
          <label className="logo-upload"><span>{locale === "zh-CN" ? "Logo" : "Logo"}</span><input type="file" accept="image/png,image/jpeg,image/webp" onChange={chooseBrandLogo} /><span className="logo-upload__button">{brandLogoName || (locale === "zh-CN" ? "选择 PNG / JPEG / WebP" : "Choose PNG / JPEG / WebP")}</span><small>{locale === "zh-CN" ? "最大 256 KB；不上传则保留模板或默认 Logo" : "256 KB maximum; leave empty to keep the template or default logo"}</small></label>
          {brandLogo && <button type="button" className="text-button brand-settings__remove" onClick={() => { setBrandLogo(""); setBrandLogoName(""); }}>{locale === "zh-CN" ? "移除自定义 Logo" : "Remove custom logo"}</button>}
        </div>
      </div>
      <div className="brand-settings__actions">
        <button type="button" className="button button--secondary button--small" disabled={brandSaving} onClick={() => void restoreBrand()}>{supportsGeneratedIdentity(profile.selected_domains) ? (locale === "zh-CN" ? "恢复模板品牌" : "Restore template branding") : (locale === "zh-CN" ? "恢复默认品牌" : "Restore default branding")}</button>
        <button type="button" className="button button--primary button--small" disabled={brandSaving || (!brandName.trim() && !brandLogo)} onClick={() => void saveBrand()}>{brandSaving ? (locale === "zh-CN" ? "正在保存…" : "Saving…") : t(locale, "save")}</button>
      </div>
    </section>}
    </>}
    {settingsPage === "llm" && <section className="settings-section llm-settings">
      <div className="section-heading">
        <div><span className="eyebrow">LLM CONNECTIONS</span><h3>{locale === "zh-CN" ? "LLM 连接" : "LLM connections"}</h3></div>
      </div>
      <p className="provider-warning">{locale === "zh-CN" ? "集中管理 OpenAI-compatible LLM。翻译和未来的 LLM 功能可以复用同一连接，也可以使用不同连接。" : "Manage OpenAI-compatible LLMs centrally. Translation and future LLM features can reuse a connection or use separate ones."}</p>
      <div className="llm-settings__editor">
        <label className="field"><span>{locale === "zh-CN" ? "连接" : "Connection"}</span><SelectMenu value={llmConnectionChoice} onChange={chooseLlmConnection} label={locale === "zh-CN" ? "LLM 连接" : "LLM connection"} options={[...llmConnections.map((connection) => ({ value: String(connection.id), label: `${connection.name} · ${connection.model}` })), { value: "new", label: locale === "zh-CN" ? "＋ 添加 LLM 连接" : "+ Add LLM connection" }]} /></label>
        <label className="field"><span>{locale === "zh-CN" ? "连接名称" : "Connection name"}</span><input value={llmConnectionName} onChange={(event) => setLlmConnectionName(event.target.value)} maxLength={120} placeholder={locale === "zh-CN" ? "例如：通用 OpenAI" : "e.g. Shared OpenAI"} /></label>
        <label className="field"><span>Base URL</span><input type="url" value={llmBaseUrl} onChange={(event) => setLlmBaseUrl(event.target.value)} placeholder="https://api.openai.com/v1" /></label>
        <label className="field"><span>Model</span><input value={llmModel} onChange={(event) => setLlmModel(event.target.value)} placeholder="gpt-4o-mini" /></label>
        <label className="field translation-key-field"><span>API Key</span><input type="password" autoComplete="off" value={llmApiKey} onChange={(event) => { setLlmApiKey(event.target.value); setClearLlmApiKey(false); }} placeholder={clearLlmApiKey ? (locale === "zh-CN" ? "保存后移除" : "Will be removed on save") : llmKeyConfigured ? `${selectedLlmConnection?.api_key_hint || "••••"} · ${locale === "zh-CN" ? "留空保持不变" : "leave blank to keep"}` : "sk-…"} />{llmKeyConfigured && <button type="button" className="text-button" onClick={() => { setLlmApiKey(""); setClearLlmApiKey(true); }}>{locale === "zh-CN" ? "移除已保存 Key" : "Remove saved key"}</button>}</label>
      </div>
      {selectedLlmConnection && <p className="translation-settings__fallback-note">{selectedLlmConnection.used_by.length
        ? (locale === "zh-CN" ? `正在用于：${selectedLlmConnection.used_by.join("、")}。被功能引用时不能删除。` : `Used by: ${selectedLlmConnection.used_by.join(", ")}. A connection cannot be deleted while in use.`)
        : (locale === "zh-CN" ? "当前没有功能使用此连接。" : "No feature currently uses this connection.")}</p>}
      <div className="translation-settings__actions">
        <button type="button" className="button button--primary button--small" disabled={llmSaving || !llmConnectionName.trim() || !llmBaseUrl || !llmModel.trim() || (llmConnectionChoice === "new" && !llmApiKey)} onClick={() => void saveLlmConnection()}>{llmSaving ? (locale === "zh-CN" ? "正在保存…" : "Saving…") : (llmConnectionChoice === "new" ? (locale === "zh-CN" ? "添加连接" : "Add connection") : t(locale, "save"))}</button>
        <button type="button" className="button button--secondary button--small" disabled={llmTesting || !llmBaseUrl || !llmModel.trim() || (!llmApiKey && !llmKeyConfigured) || clearLlmApiKey} onClick={() => void testLlmConnection()}>{llmTesting ? (locale === "zh-CN" ? "正在测试…" : "Testing…") : (locale === "zh-CN" ? "测试调用" : "Test call")}</button>
        {selectedLlmConnection && <button type="button" className="button button--danger-quiet" disabled={selectedLlmConnection.used_by.length > 0} onClick={() => void removeLlmConnection()}>{locale === "zh-CN" ? "删除连接" : "Delete connection"}</button>}
      </div>
      {llmTest && <p role="status" className={`translation-test-result translation-test-result--${llmTest.tone}`}>{llmTest.message}</p>}
    </section>}
    {settingsPage === "proxy" && <section className="settings-section proxy-settings">
      <div className="section-heading">
        <div><span className="eyebrow">NETWORK PROXY</span><h3>{locale === "zh-CN" ? "网络代理" : "Network proxy"}</h3></div>
        <Toggle checked={proxyEnabled} onChange={setProxyEnabled} label={proxyEnabled ? "On" : "Off"} />
      </div>
      <p className="provider-warning">{locale === "zh-CN" ? "此开关只控制自定义代理。每个订阅源、LLM 连接和翻译服务都可以独立选择自定义代理、系统代理或直连。" : "This switch controls only the custom proxy. Each feed, LLM connection, and translation provider can independently use the custom proxy, system proxy, or a direct connection."}</p>
      {networkProxy?.running_in_container && <div className="proxy-docker-notice"><strong>{locale === "zh-CN" ? "Docker 运行提示" : "Docker setup"}</strong><p>{locale === "zh-CN" ? "代理运行在宿主机时，地址使用 " : "When the proxy runs on the host, use "}<code>http://host.docker.internal:7890</code>{locale === "zh-CN" ? "，不要使用 127.0.0.1。" : ", not 127.0.0.1."}</p></div>}
      <div className="proxy-settings__connection">
        <label className="field"><span>{locale === "zh-CN" ? "代理地址" : "Proxy URL"}</span><input value={proxyUrl} onChange={(event) => setProxyUrl(event.target.value)} placeholder="http://127.0.0.1:7890" /></label>
        <label className="field"><span>{locale === "zh-CN" ? "用户名（可选）" : "Username (optional)"}</span><input autoComplete="off" value={proxyUsername} onChange={(event) => setProxyUsername(event.target.value)} /></label>
        <label className="field translation-key-field"><span>{locale === "zh-CN" ? "密码（可选）" : "Password (optional)"}</span><input type="password" autoComplete="new-password" value={proxyPassword} onChange={(event) => { setProxyPassword(event.target.value); setClearProxyPassword(false); }} placeholder={clearProxyPassword ? (locale === "zh-CN" ? "保存后移除" : "Will be removed on save") : networkProxy?.password_configured ? `${networkProxy.password_hint || "••••"} · ${locale === "zh-CN" ? "留空保持不变" : "leave blank to keep"}` : (locale === "zh-CN" ? "无需认证可留空" : "Leave blank if not required")} />{networkProxy?.password_configured && <button type="button" className="text-button" onClick={() => { setProxyPassword(""); setClearProxyPassword(true); }}>{locale === "zh-CN" ? "移除已保存密码" : "Remove saved password"}</button>}</label>
      </div>
      <p className="translation-settings__fallback-note">{locale === "zh-CN" ? "自定义代理支持 HTTP、HTTPS 和 SOCKS5。系统代理读取 HTTP_PROXY、HTTPS_PROXY 和 ALL_PROXY；直连会明确忽略这些环境变量。密码使用应用密钥加密保存。" : "The custom proxy supports HTTP, HTTPS, and SOCKS5. System proxy reads HTTP_PROXY, HTTPS_PROXY, and ALL_PROXY; direct mode explicitly ignores them. The password is encrypted with the application secret key."}</p>
      <div className="proxy-targets">
        <div className="proxy-targets__group proxy-global-route">
          <div className="proxy-targets__heading"><strong>{locale === "zh-CN" ? "应用全局代理" : "Application-wide proxy"}</strong></div>
          <div className="proxy-target-row"><span><strong>{locale === "zh-CN" ? "未单独配置的网络功能" : "Network features without an override"}</strong><small>{locale === "zh-CN" ? "更新检查、Release 资产下载及未来未提供独立路由的功能；镜像层使用 Docker 宿主代理" : "Update checks, Release assets, and future features without a dedicated route; image layers use the Docker host proxy"}</small></span><SelectMenu value={proxyGlobalMode} onChange={(value) => setProxyGlobalMode(value as ProxyMode)} label={locale === "zh-CN" ? "应用全局代理连接方式" : "Application-wide proxy route"} options={[{ value: "custom", label: locale === "zh-CN" ? "自定义代理" : "Custom proxy" }, { value: "system", label: locale === "zh-CN" ? "系统代理" : "System proxy" }, { value: "direct", label: locale === "zh-CN" ? "直连" : "Direct" }]} /></div>
          <p className="translation-settings__fallback-note">{locale === "zh-CN" ? "订阅源、LLM 和翻译服务仍优先使用各自的独立设置。" : "Feeds, LLM connections, and translation providers continue to use their own explicit routes first."}</p>
        </div>
        <div className="proxy-targets__group">
          <div className="proxy-targets__heading"><strong>{locale === "zh-CN" ? "订阅源" : "Feeds"}</strong></div>
          <div className="proxy-target-list">{feeds.length ? feeds.map((feed) => <div className="proxy-target-row" key={feed.id}><span><strong>{feed.title}</strong><small>{feed.url}</small></span><SelectMenu value={proxyFeedModes[feed.id] || "direct"} onChange={(value) => setProxyFeedModes((modes) => ({ ...modes, [feed.id]: value as ProxyMode }))} label={`${feed.title} · ${locale === "zh-CN" ? "连接方式" : "connection mode"}`} options={[{ value: "custom", label: locale === "zh-CN" ? "自定义代理" : "Custom proxy" }, { value: "system", label: locale === "zh-CN" ? "系统代理" : "System proxy" }, { value: "direct", label: locale === "zh-CN" ? "直连" : "Direct" }]} /></div>) : <p className="muted">{locale === "zh-CN" ? "暂无订阅源。" : "No feeds yet."}</p>}</div>
        </div>
        <div className="proxy-targets__group">
          <div className="proxy-targets__heading"><strong>{locale === "zh-CN" ? "LLM 连接" : "LLM connections"}</strong></div>
          <div className="proxy-target-list">{llmConnections.length ? llmConnections.map((connection) => <div className="proxy-target-row" key={connection.id}><span><strong>{connection.name}</strong><small>{connection.model} · {connection.base_url}</small></span><SelectMenu value={proxyLlmConnectionModes[connection.id] || "direct"} onChange={(value) => setProxyLlmConnectionModes((modes) => ({ ...modes, [connection.id]: value as ProxyMode }))} label={`${connection.name} · ${locale === "zh-CN" ? "连接方式" : "connection mode"}`} options={[{ value: "custom", label: locale === "zh-CN" ? "自定义代理" : "Custom proxy" }, { value: "system", label: locale === "zh-CN" ? "系统代理" : "System proxy" }, { value: "direct", label: locale === "zh-CN" ? "直连" : "Direct" }]} /></div>) : <p className="muted">{locale === "zh-CN" ? "暂无 LLM 连接。" : "No LLM connections yet."}</p>}</div>
        </div>
        <div className="proxy-targets__group">
          <div className="proxy-targets__heading"><strong>{locale === "zh-CN" ? "翻译服务" : "Translation providers"}</strong></div>
          <div className="proxy-target-list">{TRANSLATION_PROXY_TARGETS.map((service) => <div className="proxy-target-row" key={service.id}><span><strong>{service.name}</strong><small>{service.meta}</small></span><SelectMenu value={proxyTranslationServiceModes[service.id] || "direct"} onChange={(value) => setProxyTranslationServiceModes((modes) => ({ ...modes, [service.id]: value as ProxyMode }))} label={`${service.name} · ${locale === "zh-CN" ? "连接方式" : "connection mode"}`} options={[{ value: "custom", label: locale === "zh-CN" ? "自定义代理" : "Custom proxy" }, { value: "system", label: locale === "zh-CN" ? "系统代理" : "System proxy" }, { value: "direct", label: locale === "zh-CN" ? "直连" : "Direct" }]} /></div>)}</div>
          <p className="translation-settings__fallback-note">{locale === "zh-CN" ? "Custom LLM 翻译沿用所选 LLM 连接的代理设置。" : "Custom LLM translation uses the proxy setting of its selected LLM connection."}</p>
        </div>
      </div>
      <div className="translation-settings__actions"><button type="button" className="button button--primary button--small" disabled={proxySaving || (proxyEnabled && !proxyUrl.trim())} onClick={() => void saveNetworkProxy()}>{proxySaving ? (locale === "zh-CN" ? "正在保存…" : "Saving…") : t(locale, "save")}</button><button type="button" className="button button--secondary button--small" disabled={proxyTesting || !proxyUrl.trim()} onClick={() => void testNetworkProxy()}>{proxyTesting ? (locale === "zh-CN" ? "正在测试…" : "Testing…") : (locale === "zh-CN" ? "测试自定义代理" : "Test custom proxy")}</button></div>
      {proxyTest?.error && <p role="status" className="translation-test-result translation-test-result--error">{proxyTest.error}</p>}
      {proxyTest?.results.map((result) => <p role="status" key={result.target_url} className={`translation-test-result translation-test-result--${result.ok ? "success" : "error"}`}><strong>{new URL(result.target_url).hostname}</strong> · {result.ok ? (locale === "zh-CN" ? `可用（HTTP ${result.status_code}，${result.elapsed_ms} ms）` : `Reachable (HTTP ${result.status_code}, ${result.elapsed_ms} ms)`) : (locale === "zh-CN" ? `失败（${result.error || "未知错误"}，${result.elapsed_ms} ms）` : `Failed (${result.error || "unknown error"}, ${result.elapsed_ms} ms)`)}</p>)}
    </section>}
    {settingsPage === "translation" && <section className="settings-section translation-settings">
      <div className="section-heading">
        <div><span className="eyebrow">TRANSLATION</span><h3>{t(locale, "translation")}</h3></div>
        {translation && <Toggle checked={translation.enabled} onChange={(value) => void saveTranslation(value)} label={translation.enabled ? "On" : "Off"} />}
      </div>
      <p className="provider-warning">{t(locale, "translationWarning")}</p>
      <div className="translation-settings__grid">
        <label className="field">
          <span>{locale === "zh-CN" ? "主翻译服务" : "Primary provider"}</span>
          <SelectMenu value={translationProvider} onChange={(value) => { setTranslationProvider(value as TranslationProvider); setTranslationTest(null); }} label={locale === "zh-CN" ? "主翻译服务" : "Primary provider"} options={[
            { value: "custom-llm", label: "Custom LLM (OpenAI-compatible)" },
            { value: "deepl", label: "DeepL API" },
            { value: "google-cloud", label: "Google Cloud Translation" },
            { value: "google-gtx", label: "Google GTX" },
          ]} />
        </label>
        <label className="field">
          <span>{t(locale, "targetLanguage")}</span>
          <SelectMenu value={target} onChange={setTarget} label={t(locale, "targetLanguage")} options={[{ value: "zh-CN", label: "简体中文" }, { value: "zh-TW", label: "繁體中文" }, { value: "en", label: "English" }, { value: "ja", label: "日本語" }, { value: "de", label: "Deutsch" }, { value: "fr", label: "Français" }]} />
        </label>
        <label className="field">
          <span>{locale === "zh-CN" ? "回退方式" : "Fallback mode"}</span>
          <SelectMenu value={fallbackMode} onChange={(value) => setFallbackMode(value as TranslationFallbackMode)} label={locale === "zh-CN" ? "回退方式" : "Fallback mode"} options={[
            { value: "automatic", label: locale === "zh-CN" ? "自动回退" : "Automatic fallback" },
            { value: "manual", label: locale === "zh-CN" ? "手动回退" : "Manual fallback" },
          ]} />
        </label>
      </div>
      <p className="translation-settings__fallback-note">{fallbackMode === "automatic"
        ? (locale === "zh-CN" ? "主服务失败时自动尝试 Google GTX。" : "Google GTX is tried automatically when the primary provider fails.")
        : (locale === "zh-CN" ? "主服务失败后停止，不自动发送给 Google GTX。需要回退时，请把主服务切换为 Google GTX 并重试失败项。" : "Stop after a primary-provider failure. To fall back, select Google GTX and retry failed items.")}</p>
      {translationProvider === "custom-llm" && <div className="translation-credentials translation-credentials--single">
        {llmConnections.length ? <label className="field"><span>{locale === "zh-CN" ? "用于翻译的 LLM 连接" : "LLM connection for translation"}</span><SelectMenu value={translationLlmConnectionId} onChange={(value) => { setTranslationLlmConnectionId(value); setTranslationTest(null); }} label={locale === "zh-CN" ? "用于翻译的 LLM 连接" : "LLM connection for translation"} options={llmConnections.map((connection) => ({ value: String(connection.id), label: `${connection.name} · ${connection.model}` }))} /></label> : <p className="provider-warning">{locale === "zh-CN" ? "尚未添加 LLM 连接。请先在上方“LLM 连接”区域添加。" : "No LLM connection exists. Add one in the LLM connections section above."}</p>}
      </div>}
      {translationProvider === "deepl" && <div className="translation-credentials">
        <label className="field"><span>Endpoint</span><input type="url" value={deeplEndpoint} onChange={(event) => setDeeplEndpoint(event.target.value)} /></label>
        <label className="field translation-key-field"><span>DeepL API Key</span><input type="password" autoComplete="off" value={deeplApiKey} onChange={(event) => { setDeeplApiKey(event.target.value); setClearDeeplApiKey(false); }} placeholder={clearDeeplApiKey ? (locale === "zh-CN" ? "保存后移除" : "Will be removed on save") : translation?.deepl_api_key_configured ? (locale === "zh-CN" ? "已配置；留空保持不变" : "Configured; leave blank to keep") : "DeepL auth key"} />{translation?.deepl_api_key_configured && <button type="button" className="text-button" onClick={() => { setDeeplApiKey(""); setClearDeeplApiKey(true); }}>{locale === "zh-CN" ? "移除已保存 Key" : "Remove saved key"}</button>}</label>
      </div>}
      {translationProvider === "google-cloud" && <div className="translation-credentials translation-credentials--single">
        <label className="field translation-key-field"><span>Google Cloud API Key</span><input type="password" autoComplete="off" value={googleCloudApiKey} onChange={(event) => { setGoogleCloudApiKey(event.target.value); setClearGoogleCloudApiKey(false); }} placeholder={clearGoogleCloudApiKey ? (locale === "zh-CN" ? "保存后移除" : "Will be removed on save") : translation?.google_cloud_api_key_configured ? (locale === "zh-CN" ? "已配置；留空保持不变" : "Configured; leave blank to keep") : "Google Cloud API key"} />{translation?.google_cloud_api_key_configured && <button type="button" className="text-button" onClick={() => { setGoogleCloudApiKey(""); setClearGoogleCloudApiKey(true); }}>{locale === "zh-CN" ? "移除已保存 Key" : "Remove saved key"}</button>}</label>
      </div>}
      {translationProvider === "google-gtx" && <p className="translation-settings__fallback-note">{locale === "zh-CN" ? "Google GTX 是非正式、无需 Key 的接口，可靠性较低。" : "Google GTX is unofficial and keyless, with lower reliability."}</p>}
      <div className="translation-settings__actions">
        <button type="button" className="button button--primary button--small" disabled={translationSaving || (translationProvider === "custom-llm" && !translationLlmConnectionId)} onClick={() => void saveTranslation()}>{translationSaving ? (locale === "zh-CN" ? "正在保存…" : "Saving…") : t(locale, "save")}</button>
        <button type="button" className="button button--secondary button--small" disabled={translationTesting || (translationProvider === "custom-llm" && !translationLlmConnectionId) || (translationProvider === "deepl" && (clearDeeplApiKey || (!deeplApiKey && !translation?.deepl_api_key_configured))) || (translationProvider === "google-cloud" && (clearGoogleCloudApiKey || (!googleCloudApiKey && !translation?.google_cloud_api_key_configured)))} onClick={() => void testTranslationConnection()}>{translationTesting ? (locale === "zh-CN" ? "正在测试…" : "Testing…") : (locale === "zh-CN" ? "测试翻译服务" : "Test translation provider")}</button>
        <button type="button" className="button button--secondary button--small" disabled={translationRetrying || (translation?.failed_count ?? 0) === 0} onClick={() => void retryFailedTranslations()}>{translationRetrying ? (locale === "zh-CN" ? "正在重新排队…" : "Queueing…") : (locale === "zh-CN" ? "重试失败项" : "Retry failed")}</button>
      </div>
      {translationTest && <p role="status" className={`translation-test-result translation-test-result--${translationTest.tone}`}>{translationTest.message}</p>}
      <div className="translation-status-grid" aria-label={locale === "zh-CN" ? "翻译任务状态" : "Translation job status"}>
        <div className="translation-status-card translation-status-card--queued">
          <span>{locale === "zh-CN" ? "正在排队翻译中" : "Queued for translation"}</span>
          <strong>{translation?.pending_count ?? 0}</strong>
          <small>{locale === "zh-CN" ? "尚未开始，请等待" : "Not started yet; please wait"}</small>
        </div>
        <div className="translation-status-card translation-status-card--running">
          <span>{locale === "zh-CN" ? "正在翻译" : "Translating now"}</span>
          <strong>{translation?.running_count ?? 0}</strong>
          <small>{locale === "zh-CN" ? "服务正在处理，请等待" : "In progress; please wait"}</small>
        </div>
        <div className="translation-status-card translation-status-card--complete">
          <span>{locale === "zh-CN" ? "已完成" : "Completed"}</span>
          <strong>{translation?.completed_count ?? 0}</strong>
          <small>{locale === "zh-CN" ? "无需操作" : "No action needed"}</small>
        </div>
        <div className="translation-status-card translation-status-card--failed">
          <span>{locale === "zh-CN" ? "已失败" : "Failed"}</span>
          <strong>{translation?.failed_count ?? 0}</strong>
          <small>{locale === "zh-CN" ? "可点击“重试失败项”" : "Use “Retry failed”"}</small>
        </div>
      </div>
      <p className="muted">{locale === "zh-CN" ? "主服务" : "Primary"}: {translation?.provider} · {translation?.provider_healthy ? (locale === "zh-CN" ? "可用" : "ready") : (locale === "zh-CN" ? "未配置或异常" : "not configured or unhealthy")} · {fallbackMode === "automatic" ? (locale === "zh-CN" ? "自动回退 GTX" : "automatic GTX fallback") : (locale === "zh-CN" ? "手动回退" : "manual fallback")}</p>
    </section>}
    {settingsPage === "activity" && <>
    <section className="settings-section"><span className="eyebrow">ACTIVITY</span><h3>{t(locale, "recentJobs")}</h3><div className="job-list">{jobs.slice(0, 10).map((job) => <div className="job-row" key={job.id}><span className={`job-status job-status--${["completed", "success"].includes(job.status) ? "success" : ["failed", "error"].includes(job.status) ? "error" : "running"}`} /><div><strong>{job.feed_title || job.kind}</strong><span>{job.message || job.status}</span></div><span>{formatDateTime(job.started_at || job.created_at, locale)}</span></div>)}</div></section>
    <section className="settings-section call-logs-section">
      <div className="call-logs-section__header">
        <div><span className="eyebrow">CALL LOGS</span><h3>{locale === "zh-CN" ? "LLM 与翻译调用日志" : "LLM and translation call logs"}</h3></div>
        <div>
          <SelectMenu compact value={callLogCategory} onChange={(value) => setCallLogCategory(value as "all" | "llm" | "translation")} label={locale === "zh-CN" ? "日志类型" : "Log category"} options={[
            { value: "all", label: locale === "zh-CN" ? "全部调用" : "All calls" },
            { value: "llm", label: "LLM" },
            { value: "translation", label: locale === "zh-CN" ? "翻译" : "Translation" },
          ]} />
          <button type="button" className="button button--secondary button--small call-logs-section__refresh" disabled={callLogsLoading} onClick={() => void loadCallLogs(true)}>{callLogsLoading ? (locale === "zh-CN" ? "刷新中…" : "Refreshing…") : (locale === "zh-CN" ? "刷新" : "Refresh")}</button>
        </div>
      </div>
      <p className="call-logs-section__file">{locale === "zh-CN" ? "网页仅显示最近 100 条；完整日志文件" : "The web view shows the latest 100 entries; complete log file"}：<code>{callLogFile}</code></p>
      <div className="call-log-list">
        {callLogs.length > 0 ? callLogs.map((log) => <div className={`call-log-row call-log-row--${log.status}`} key={log.id}>
          <span className={`job-status job-status--${log.status === "success" ? "success" : "error"}`} />
          <div className="call-log-row__body">
            <div><strong>{log.category === "llm" ? "LLM" : (locale === "zh-CN" ? "翻译" : "Translation")} · {log.feature || log.operation}</strong><span>{log.cached ? (locale === "zh-CN" ? "缓存命中" : "cache hit") : log.status}</span></div>
            <p>{[log.connection_name, log.provider, log.model, log.target_language].filter(Boolean).join(" · ") || "—"}</p>
            {log.error && <p className="call-log-row__error">{log.error}</p>}
          </div>
          <div className="call-log-row__metrics">
            <time>{formatDateTime(log.timestamp, locale)}</time>
            <span>{log.duration_ms} ms · {log.input_chars}→{log.output_chars} chars</span>
          </div>
        </div>) : <p className="muted">{callLogsLoading ? (locale === "zh-CN" ? "正在加载日志…" : "Loading logs…") : (locale === "zh-CN" ? "还没有 LLM 或翻译调用日志。" : "No LLM or translation calls have been logged yet.")}</p>}
      </div>
    </section>
    <section className="settings-section keyboard-section"><span className="eyebrow">KEYBOARD</span><div className="shortcut-grid"><span><kbd>J</kbd>/<kbd>K</kbd> Navigate</span><span><kbd>M</kbd> Read</span><span><kbd>S</kbd> Star</span><span><kbd>L</kbd> Later</span><span><kbd>A</kbd> Archive</span><span><kbd>O</kbd> Open</span></div></section>
    </>}
    {settingsPage === "account" && <>
    <section className="settings-section update-settings">
      <div className="section-heading"><div><span className="eyebrow">APPLICATION UPDATE</span><h3>{locale === "zh-CN" ? "应用更新" : "Application update"}</h3></div><span className="update-settings__version">v{appUpdate?.current_version || settings?.version}</span></div>
      <p>{locale === "zh-CN" ? `应用会在启动时和每天 ${String(appUpdate?.check_hour ?? 5).padStart(2, "0")}:00 自动检查，校验 Release 资产，并让 Docker Engine 按摘要预拉镜像。安装始终需要你确认。` : `The application checks on startup and every day at ${String(appUpdate?.check_hour ?? 5).padStart(2, "0")}:00, verifies the Release asset, and asks Docker Engine to pre-pull the digest-pinned image. Installation always requires your confirmation.`}</p>
      {appUpdate && <div className={`update-status-card update-status-card--${appUpdate.status}`}>
        <strong>{appUpdate.downloaded
          ? (locale === "zh-CN" ? `版本 ${appUpdate.latest_version} 已下载` : `Version ${appUpdate.latest_version} is downloaded`)
          : appUpdate.status === "up_to_date"
            ? (locale === "zh-CN" ? "当前已是最新版本" : "You are up to date")
            : appUpdate.status === "checking"
              ? (locale === "zh-CN" ? "正在检查更新…" : "Checking for updates…")
              : (locale === "zh-CN" ? `更新状态：${appUpdate.status}` : `Update status: ${appUpdate.status}`)}</strong>
        <small>{locale === "zh-CN" ? "上次检查" : "Last checked"}：{formatDateTime(appUpdate.last_checked_at, locale)}</small>
        {appUpdate.error && <p className="update-status-card__error">{appUpdate.error}</p>}
        {appUpdate.downloaded && !appUpdate.install_supported && <p>{locale === "zh-CN" ? "当前部署未运行更新辅助服务，请从 Release 页面手动安装。" : "This deployment is not running the update helper; install manually from the Release page."}</p>}
      </div>}
      <div className="translation-settings__actions">
        <button type="button" className="button button--secondary button--small" disabled={updateChecking} onClick={() => void checkForUpdates()}>{updateChecking ? (locale === "zh-CN" ? "正在检查…" : "Checking…") : (locale === "zh-CN" ? "立即检查" : "Check now")}</button>
        {appUpdate?.downloaded && <button type="button" className="button button--primary button--small" disabled={updateInstalling || !appUpdate.install_supported} onClick={() => void installUpdate()}>{updateInstalling ? (locale === "zh-CN" ? "正在准备重启…" : "Preparing restart…") : (locale === "zh-CN" ? "安装并重启" : "Install and restart")}</button>}
        {appUpdate?.release_url && <a className="button button--secondary button--small" href={appUpdate.release_url} target="_blank" rel="noreferrer">{locale === "zh-CN" ? "查看 Release" : "View release"}</a>}
      </div>
    </section>
    {settings?.debug && auth.mode === "owner" && <section className="settings-section debug-section"><div><span className="eyebrow">DEBUG MODE</span><h3>{locale === "zh-CN" ? "重置首次设置" : "Reset first-run setup"}</h3><p>{locale === "zh-CN" ? "删除 owner、所有登录会话、阅读状态和个性化配置，然后返回首次部署流程。实例中的订阅与文章会保留。" : "Delete the owner, every login session, reading states, and personalization, then return to first-run setup. Instance feeds and articles are preserved."}</p></div><button type="button" className="button button--danger-quiet" disabled={resetting} onClick={() => void resetDebugOwner()}>{resetting ? (locale === "zh-CN" ? "正在重置…" : "Resetting…") : (locale === "zh-CN" ? "注销并删除 owner" : "Sign out and delete owner")}</button></section>}
    <section className="settings-section"><p className="muted">{settings?.app_name} v{settings?.version} · {settings?.timezone}</p>{auth.mode === "owner" && <button className="button button--danger-quiet" onClick={onLogout}>{t(locale, "logout")}</button>}</section>
    </>}
  </div>}</Modal>;
}
