import { type FormEvent, useState } from "react";
import { api } from "../api";
import type { AuthStatus, Locale } from "../types";
import { t } from "../i18n";
import { errorText } from "../utils";
import { Brand } from "./Common";

export function AuthScreen({
  status,
  locale,
  onAuthenticated,
}: {
  status: AuthStatus;
  locale: Locale;
  onAuthenticated: (status: AuthStatus) => void;
}) {
  const [initialPassword, setInitialPassword] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const setup = status.setup_required;
  const activation = Boolean(status.activation_required);
  const choosingPassword = setup || activation;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (password.length < 8) {
      return setError(locale === "zh-CN" ? "新密码至少需要 8 个字符。" : "Use at least 8 characters for the new password.");
    }
    if (choosingPassword && password !== confirm) {
      return setError(locale === "zh-CN" ? "两次输入的新密码不一致。" : "The new passwords do not match.");
    }
    setBusy(true);
    try {
      const next = activation
        ? await api.activate(initialPassword, password)
        : setup
          ? await api.setup(password)
          : await api.login(password);
      api.setCsrfToken(next.csrf_token);
      onAuthenticated({
        ...next,
        authenticated: true,
        setup_required: false,
        activation_required: false,
      });
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  const title = activation
    ? locale === "zh-CN" ? "激活 owner" : "Activate the owner"
    : t(locale, setup ? "setupTitle" : "loginTitle");

  return (
    <main className="auth-page">
      <section className="auth-story">
        <Brand />
        <div className="auth-story__content">
          <span className="eyebrow eyebrow--light">YOUR PRIVATE READING INBOX</span>
          <h1>{locale === "zh-CN" ? "让重要的内容，抵达你的阅读队列。" : "A calm home for everything worth reading."}</h1>
          <p>{locale === "zh-CN" ? "订阅任何 RSS 或 Atom 源，在多个设备间同步阅读状态和标签。" : "Follow any RSS or Atom source and keep reading state and tags in sync across your devices."}</p>
        </div>
        <footer>Self-hosted · Single owner · Your data</footer>
      </section>
      <section className="auth-form-wrap">
        <form className="auth-form" onSubmit={submit}>
          <div className="auth-form__mobile-brand"><Brand /></div>
          <span className="eyebrow">{activation ? "ONE-TIME ACTIVATION" : setup ? "INITIAL SETUP" : "WELCOME BACK"}</span>
          <h2>{title}</h2>
          {activation && (
            <>
              <p className="auth-form__note">
                {locale === "zh-CN"
                  ? "输入安装时生成的一次性初始密码，然后设置你的长期密码。"
                  : "Enter the one-time password generated during installation, then choose your permanent password."}
              </p>
              <label className="field">
                <span>{locale === "zh-CN" ? "一次性初始密码" : "One-time initial password"}</span>
                <input
                  type="password"
                  autoFocus
                  minLength={8}
                  required
                  value={initialPassword}
                  onChange={(event) => setInitialPassword(event.target.value)}
                  autoComplete="current-password"
                />
              </label>
            </>
          )}
          <label className="field">
            <span>{activation ? (locale === "zh-CN" ? "新密码" : "New password") : t(locale, "password")}</span>
            <input
              type="password"
              autoFocus={!activation}
              minLength={8}
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={choosingPassword ? "new-password" : "current-password"}
            />
          </label>
          {choosingPassword && (
            <label className="field">
              <span>{t(locale, "confirmPassword")}</span>
              <input
                type="password"
                minLength={8}
                required
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                autoComplete="new-password"
              />
            </label>
          )}
          {error && <div className="field-error" role="alert">{error}</div>}
          <button type="submit" className="button button--primary button--large" disabled={busy}>
            {busy ? "…" : t(locale, "continue")}
          </button>
          <p className="auth-form__note">HttpOnly session cookie · CSRF protected</p>
        </form>
      </section>
    </main>
  );
}
