import { type ChangeEvent, type CSSProperties, type FormEvent, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import {
  applyTheme,
  composeDomainTheme,
  composeSiteIdentity,
  customizeSiteIdentity,
  DOMAIN_TEMPLATES,
  supportsGeneratedIdentity,
} from "../domainThemes";
import type { Locale, OnboardingProfile, ThemeConfig } from "../types";
import { errorText } from "../utils";
import { Brand } from "./Common";

type Step = "domains" | "ai" | "preview";
const MAX_LOGO_BYTES = 256 * 1024;

function providerLabel(baseUrl: string, model: string) {
  try {
    return `${new URL(baseUrl).host} · ${model}`;
  } catch {
    return model;
  }
}

export function OnboardingWizard({
  locale,
  onComplete,
}: {
  locale: Locale;
  onComplete: (profile: OnboardingProfile) => void;
}) {
  const zh = locale === "zh-CN";
  const [step, setStep] = useState<Step>("domains");
  const [selected, setSelected] = useState<string[]>([]);
  const [primary, setPrimary] = useState("");
  const [custom, setCustom] = useState("");
  const [useAI, setUseAI] = useState(false);
  const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [stylePrompt, setStylePrompt] = useState("");
  const [siteName, setSiteName] = useState("");
  const [logoDataUrl, setLogoDataUrl] = useState("");
  const [logoFileName, setLogoFileName] = useState("");
  const [customizeGenerated, setCustomizeGenerated] = useState(false);
  const [aiTheme, setAITheme] = useState<ThemeConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const secureForKey = window.isSecureContext || ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);

  const builtInTheme = useMemo(
    () => selected.length && primary ? composeDomainTheme(selected, primary, locale) : null,
    [locale, primary, selected],
  );
  const generatedIdentity = supportsGeneratedIdentity(selected);
  const suggestedIdentity = useMemo(
    () => composeSiteIdentity(selected, locale),
    [locale, selected],
  );
  const editingIdentity = !generatedIdentity || customizeGenerated;
  const identity = useMemo(
    () => editingIdentity
      ? customizeSiteIdentity(suggestedIdentity, siteName, logoDataUrl)
      : suggestedIdentity,
    [editingIdentity, logoDataUrl, siteName, suggestedIdentity],
  );
  const baseTheme = aiTheme || builtInTheme;
  const theme = useMemo(
    () => baseTheme ? { ...baseTheme, identity } : null,
    [baseTheme, identity],
  );

  useEffect(() => {
    if (step === "preview") applyTheme(theme);
  }, [step, theme]);

  function toggle(name: string) {
    setError("");
    setAITheme(null);
    setSelected((current) => {
      if (current.includes(name)) {
        const next = current.filter((item) => item !== name);
        if (primary === name) setPrimary(next[0] || "");
        return next;
      }
      if (current.length >= 12) {
        setError(zh ? "最多选择 12 个领域。" : "Choose up to 12 domains.");
        return current;
      }
      if (!primary) setPrimary(name);
      return [...current, name];
    });
  }

  function addCustom(event: FormEvent) {
    event.preventDefault();
    const name = custom.trim();
    if (!name) return;
    const existing = selected.find((item) => item.toLocaleLowerCase() === name.toLocaleLowerCase());
    if (!existing) toggle(name);
    setCustom("");
  }

  function chooseLogo(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError("");
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      setError(zh ? "Logo 仅支持 PNG、JPEG 或 WebP 图片。" : "Logo must be a PNG, JPEG, or WebP image.");
      return;
    }
    if (file.size > MAX_LOGO_BYTES) {
      setError(zh ? "Logo 图片不能超过 256 KB。" : "Logo image must be 256 KB or smaller.");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      if (!/^data:image\/(?:png|jpeg|webp);base64,/.test(result)) {
        setError(zh ? "无法读取这个 Logo 文件。" : "This logo file could not be read.");
        return;
      }
      setLogoDataUrl(result);
      setLogoFileName(file.name);
    };
    reader.onerror = () => setError(zh ? "无法读取这个 Logo 文件。" : "This logo file could not be read.");
    reader.readAsDataURL(file);
  }

  async function generate() {
    if (!secureForKey) {
      setError(zh ? "请在服务宿主机通过 localhost 打开 Affogato RSS Reader，或先配置 HTTPS，再提交 API Key。" : "Open Affogato RSS Reader through localhost on the host machine, or configure HTTPS before submitting an API key.");
      return;
    }
    if (!apiKey || !model.trim()) {
      setError(zh ? "调用 AI 时需要填写 API Key 和模型名称。" : "An API key and model are required to call AI.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api.generateAITheme({
        selected_domains: selected,
        primary_domain: primary,
        base_url: baseUrl,
        api_key: apiKey,
        model: model.trim(),
        style_prompt: stylePrompt.trim(),
      });
      setAITheme(result.theme);
      setStep("preview");
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function finish() {
    if (!theme) return;
    setBusy(true);
    setError("");
    try {
      const profile = await api.completeOnboarding({
        selected_domains: selected,
        primary_domain: primary,
        theme,
        ai_personalized: theme.source === "ai",
        ai_provider: theme.source === "ai" ? providerLabel(baseUrl, model) : undefined,
      });
      applyTheme(profile.theme);
      setApiKey("");
      onComplete(profile);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  const stepNumber = step === "domains" ? 1 : step === "ai" ? 2 : 3;
  return (
    <main className="onboarding-page">
      <header className="onboarding-header">
        <Brand identity={step === "domains" ? undefined : identity} />
        <div className="onboarding-progress" aria-label={zh ? `第 ${stepNumber} 步，共 3 步` : `Step ${stepNumber} of 3`}>
          {[1, 2, 3].map((value) => <span key={value} className={value <= stepNumber ? "is-active" : ""}>{value}</span>)}
        </div>
        <span className="onboarding-kicker">{zh ? "首次设置" : "FIRST-RUN SETUP"}</span>
      </header>

      {step === "domains" && (
        <section className="onboarding-stage onboarding-stage--domains">
          <div className="onboarding-intro">
            <span className="eyebrow">{zh ? "01 · 你的知识版图" : "01 · YOUR KNOWLEDGE MAP"}</span>
            <h1>{zh ? "你主要关注哪些领域？" : "What fields do you follow?"}</h1>
            <p>{zh ? "选择父领域、细分领域或交叉领域，也可以添加自己的领域。主领域决定整体气质，其他领域会参与混合。" : "Choose parent, specialized, or cross-disciplinary fields—or add your own. The primary field leads while the others blend in."}</p>
          </div>
          <div className="domain-template-grid">
            {DOMAIN_TEMPLATES.map((item) => {
              const name = item.name[locale];
              const active = selected.includes(name);
              return (
                <button
                  type="button"
                  key={item.id}
                  className={`domain-template ${active ? "is-selected" : ""}`}
                  onClick={() => toggle(name)}
                  aria-pressed={active}
                >
                  <span className="domain-template__swatch" style={{ "--template-accent": item.palette.accent, "--template-secondary": item.palette.secondary } as CSSProperties} />
                  <span className="domain-template__copy">
                    <small>{item.family[locale]}</small>
                    <strong>{name}</strong>
                  </span>
                  <span className="domain-template__check">{active ? "✓" : "+"}</span>
                </button>
              );
            })}
          </div>
          <form className="custom-domain-form" onSubmit={addCustom}>
            <label>
              <span>{zh ? "自定义领域" : "Custom field"}</span>
              <div>
                <input value={custom} onChange={(event) => setCustom(event.target.value)} maxLength={120} placeholder={zh ? "例如：量子计算、功率器件、计算金融" : "e.g. Quantum computing, power devices"} />
                <button className="button button--secondary" disabled={!custom.trim()}>{zh ? "添加" : "Add"}</button>
              </div>
            </label>
          </form>
          {selected.length > 0 && (
            <div className="selected-domains">
              <div>
                <strong>{zh ? "已选择" : "Selected"}</strong>
                <span>{zh ? "点击圆点设为主领域" : "Use the radio button to set the primary field"}</span>
              </div>
              <div className="selected-domain-list">
                {selected.map((name) => (
                  <label key={name} className={name === primary ? "is-primary" : ""}>
                    <input type="radio" name="primary-domain" checked={name === primary} onChange={() => { setPrimary(name); setAITheme(null); }} />
                    <span>{name}</span>
                    <button type="button" onClick={() => toggle(name)} aria-label={`${zh ? "移除" : "Remove"} ${name}`}>×</button>
                  </label>
                ))}
              </div>
            </div>
          )}
          {error && <div className="field-error" role="alert">{error}</div>}
          <footer className="onboarding-actions">
            <span>{zh ? "稍后仍可在设置中调整" : "You can change this later in Settings"}</span>
            <button type="button" className="button button--primary button--large" disabled={!selected.length || !primary} onClick={() => { setError(""); setStep("ai"); }}>
              {zh ? "继续个性化" : "Continue"}
            </button>
          </footer>
        </section>
      )}

      {step === "ai" && (
        <section className="onboarding-stage onboarding-stage--ai">
          <div className="onboarding-intro">
            <span className="eyebrow">{zh ? "02 · 个性化方式" : "02 · PERSONALIZATION"}</span>
            <h1>{zh ? "选择界面生成方式" : "Choose how to shape the interface"}</h1>
            <p>{zh ? "内置引擎会灵活组合领域模板。也可以用你自己的模型服务，更深入地理解细分方向和风格偏好。" : "The built-in engine blends field templates. Your own model can interpret a niche or aesthetic preference more specifically."}</p>
          </div>
          <div className="identity-panel">
            <div className="identity-preview">
              <span className="identity-preview__label">{zh ? "站点品牌预览" : "SITE IDENTITY"}</span>
              <Brand identity={identity} />
            </div>
            {generatedIdentity && !customizeGenerated ? (
              <div className="identity-copy">
                <strong>{zh ? "已根据领域自动生成" : "Generated from your fields"}</strong>
                <p>{selected.length === 1
                  ? (zh ? "这个内置领域使用独立站点名与 Logo。" : "This built-in field has its own site name and logo.")
                  : (zh ? "这两个领域使用专属交叉站点名与组合 Logo。" : "This pair has a dedicated cross-field name and combined logo.")}</p>
                <button
                  type="button"
                  className="text-button identity-copy__action"
                  onClick={() => { setSiteName(identity.name); setCustomizeGenerated(true); }}
                >
                  {zh ? "自定义站点名或 Logo" : "Customize name or logo"}
                </button>
              </div>
            ) : (
              <div className="identity-editor">
                <div className="identity-copy">
                  <strong>{generatedIdentity
                    ? (zh ? "自定义模板品牌" : "Customize template branding")
                    : (zh ? "为这个领域组合命名" : "Name this field collection")}</strong>
                  <p>{generatedIdentity
                    ? (zh ? "名称与 Logo 可分别替换；未替换的部分继续使用模板内容。" : "Replace the name, the logo, or both. Unchanged parts continue using the template.")
                    : (zh ? "三领域以上或含自定义领域时，可以使用自己的站点名与 Logo；留空则使用 Affogato RSS Reader 默认品牌。" : "Collections with three or more fields, or a custom field, can use your own name and logo. Leave both blank to use the Affogato RSS Reader default.")}</p>
                  {generatedIdentity && (
                    <button
                      type="button"
                      className="text-button identity-copy__action"
                      onClick={() => {
                        setSiteName("");
                        setLogoDataUrl("");
                        setLogoFileName("");
                        setCustomizeGenerated(false);
                      }}
                    >
                      {zh ? "恢复模板品牌" : "Restore template branding"}
                    </button>
                  )}
                </div>
                <div className="identity-editor__fields">
                  <label className="field">
                    <span>{zh ? "站点名（可选）" : "Site name (optional)"}</span>
                    <input
                      value={siteName}
                      onChange={(event) => setSiteName(event.target.value)}
                      maxLength={120}
                      placeholder={zh ? "例如：量子计算观察" : "e.g. Quantum Computing Observer"}
                    />
                  </label>
                  <label className="logo-upload">
                    <span>{zh ? "上传 Logo（可选）" : "Upload logo (optional)"}</span>
                    <input type="file" accept="image/png,image/jpeg,image/webp" onChange={chooseLogo} />
                    <span className="logo-upload__button">{logoFileName || (zh ? "选择 PNG / JPEG / WebP" : "Choose PNG / JPEG / WebP")}</span>
                    <small>{zh ? "最大 256 KB" : "256 KB maximum"}</small>
                  </label>
                  {logoDataUrl && (
                    <button
                      type="button"
                      className="text-button identity-editor__remove"
                      onClick={() => { setLogoDataUrl(""); setLogoFileName(""); }}
                    >
                      {zh ? "移除已上传 Logo" : "Remove uploaded logo"}
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
          <div className="personalization-options">
            <button type="button" className={`personalization-card ${!useAI ? "is-selected" : ""}`} onClick={() => { setUseAI(false); setAITheme(null); }}>
              <span className="personalization-card__mark">◇</span>
              <span><small>{zh ? "无需密钥" : "NO KEY REQUIRED"}</small><strong>{zh ? "内置领域引擎" : "Built-in field engine"}</strong><p>{zh ? "主领域主导，父领域和交叉领域按权重融合；快速、离线、可重复。" : "Weighted blending across primary, parent, and cross-fields. Fast, offline, and repeatable."}</p></span>
              <span className="option-radio">{!useAI ? "●" : "○"}</span>
            </button>
            <button type="button" className={`personalization-card ${useAI ? "is-selected" : ""}`} onClick={() => setUseAI(true)}>
              <span className="personalization-card__mark personalization-card__mark--ai">✦</span>
              <span><small>{zh ? "使用你自己的模型" : "USE YOUR OWN MODEL"}</small><strong>{zh ? "AI 深度个性化" : "AI-tailored interface"}</strong><p>{zh ? "适合更具体的交叉方向或自然语言风格描述。Affogato RSS Reader 不提供或保存你的密钥。" : "Best for specific intersections or natural-language aesthetics. Affogato RSS Reader does not provide or save your key."}</p></span>
              <span className="option-radio">{useAI ? "●" : "○"}</span>
            </button>
          </div>

          {useAI && (
            <div className="ai-config-panel">
              <div className={`ai-privacy-note ${secureForKey ? "" : "ai-privacy-note--warning"}`}><span>{secureForKey ? "⌁" : "!"}</span><p>{secureForKey
                ? (zh ? "API Key 会经你自己的 Affogato RSS Reader 后端发送到指定模型服务，仅用于本次生成，不会写入数据库。发送内容仅包括领域名称与下方风格偏好。" : "Your self-hosted Affogato RSS Reader backend sends the API key to the chosen model service for this generation only. It is not written to the database; only field names and the preference below are sent.")
                : (zh ? "当前页面使用局域网明文 HTTP。为避免泄露 API Key，请在服务宿主机通过 localhost 打开，或先配置 HTTPS；内置领域引擎不受影响。" : "This page uses plain HTTP over the LAN. To protect your API key, open it through localhost on the host machine or configure HTTPS. The built-in field engine remains available.")}</p></div>
              <div className="ai-config-grid">
                <label className="field"><span>API Base URL</span><input type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} required /></label>
                <label className="field"><span>{zh ? "模型名称" : "Model"}</span><input value={model} onChange={(event) => setModel(event.target.value)} placeholder="e.g. your-model-name" required /></label>
                <label className="field ai-key-field"><span>API Key</span><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="off" placeholder="••••••••••••••••" required disabled={!secureForKey} /></label>
                <label className="field ai-style-field"><span>{zh ? "额外风格偏好（可选）" : "Additional style preference (optional)"}</span><textarea value={stylePrompt} onChange={(event) => setStylePrompt(event.target.value)} maxLength={1000} placeholder={zh ? "例如：深夜实验室的氛围，但保持论文阅读的克制和高可读性" : "e.g. A late-night lab mood with restrained, highly readable paper layouts"} /></label>
              </div>
            </div>
          )}
          {error && <div className="field-error" role="alert">{error}</div>}
          <footer className="onboarding-actions">
            <button type="button" className="text-button" onClick={() => setStep("domains")}>← {zh ? "返回领域" : "Back to fields"}</button>
            <button type="button" className="button button--primary button--large" disabled={busy || (useAI && !secureForKey)} onClick={() => useAI ? void generate() : setStep("preview")}>
              {busy ? (zh ? "正在生成…" : "Generating…") : useAI ? (zh ? "用 AI 生成预览" : "Generate with AI") : (zh ? "生成内置预览" : "Preview built-in theme")}
            </button>
          </footer>
        </section>
      )}

      {step === "preview" && theme && (
        <section className="onboarding-stage onboarding-stage--preview">
          <div className="theme-preview-copy">
            <span className="eyebrow">{zh ? "03 · 最终预览" : "03 · FINAL PREVIEW"}</span>
            <h1>{identity.name}</h1>
            <p>{zh ? "主题会应用于导航、文章列表和阅读视图。领域模板只改变表达方式，不改变信息架构和可访问性。" : "The theme carries across navigation, article lists, and reading views. It changes expression—not information architecture or accessibility."}</p>
            <dl className="theme-facts">
              <div><dt>{zh ? "领域" : "Fields"}</dt><dd>{selected.join(" · ")}</dd></div>
              <div><dt>{zh ? "视觉主题" : "Theme"}</dt><dd>{theme.label}</dd></div>
              <div><dt>{zh ? "信息密度" : "Density"}</dt><dd>{theme.density}</dd></div>
              <div><dt>{zh ? "排版" : "Typography"}</dt><dd>{theme.typography}</dd></div>
              <div><dt>{zh ? "生成方式" : "Source"}</dt><dd>{theme.source === "ai" ? "AI" : (zh ? "内置引擎" : "Built-in engine")}</dd></div>
            </dl>
          </div>
          <div className={`reader-theme-preview reader-theme-preview--${theme.motif}`}>
            <aside>
              <Brand identity={identity} />
              <small>LIBRARY</small>
              <strong>{zh ? "未读文章" : "Unread articles"} <span>24</span></strong>
              <span>{zh ? "稍后读" : "Read later"}</span>
              <small>DOMAINS</small>
              {selected.slice(0, 4).map((name) => <span key={name} className={name === primary ? "is-active" : ""}>{name}</span>)}
            </aside>
            <section>
              <header><small>{primary.toLocaleUpperCase()}</small><h2>{zh ? "今日研究动态" : "Today’s research signals"}</h2></header>
              <article className="is-active"><small>08:40 · PRIMARY</small><strong>{zh ? "一个与你的主领域相匹配的示例标题" : "An example signal aligned with your primary field"}</strong><p>{zh ? "列表强调信息层级、来源和阅读状态。" : "The list foregrounds hierarchy, source, and reading state."}</p></article>
              <article><small>07:15 · CROSS-FIELD</small><strong>{zh ? "交叉领域内容保留次要色彩线索" : "Cross-field reading keeps a secondary visual signal"}</strong></article>
            </section>
            <article className="preview-detail">
              <small>{zh ? "阅读视图" : "READING VIEW"}</small>
              <h2>{zh ? "为深度阅读保留安静、稳定的画布" : "A quiet, stable canvas for close reading"}</h2>
              <p>{zh ? "正文区域始终以清晰度为先。领域气质通过强调色、节奏与细微图案呈现，不干扰文章本身。" : "The article remains clarity-first. Field character appears through accents, rhythm, and subtle motifs without competing with the text."}</p>
              <div><span /><span /><span /></div>
            </article>
          </div>
          {error && <div className="field-error" role="alert">{error}</div>}
          <footer className="onboarding-actions">
            <button type="button" className="text-button" onClick={() => setStep("ai")}>← {zh ? "返回调整" : "Back to adjust"}</button>
            <button type="button" className="button button--primary button--large" disabled={busy} onClick={() => void finish()}>
              {busy ? (zh ? "正在保存…" : "Saving…") : (zh ? "使用这个界面" : "Use this interface")}
            </button>
          </footer>
        </section>
      )}
    </main>
  );
}
