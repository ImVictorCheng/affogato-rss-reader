import type { Locale } from "./types";

export type MessageKey = keyof typeof messages.en;

const messages = {
  en: {
    all: "All articles", unread: "Unread", starred: "Starred", later: "Read later", archived: "Archived",
    briefs: "Briefs", sources: "Sources", domains: "Domains", tags: "Tags", manage: "Manage",
    settings: "Settings", logout: "Sign out", search: "Search title, author, DOI or tag",
    original: "Original", translated: "Translated", bilingual: "Bilingual", noEntries: "Nothing here yet",
    emptyHelp: "Add an RSS or Atom feed to start your library.", retry: "Retry", requestFailed: "Request failed",
    selectArticle: "Select an article", openOriginal: "Open original", originalSummary: "Original",
    translatedSummary: "Translation", noSummary: "This feed did not provide a summary.",
    notTranslated: "Not translated", translationFailed: "Translation failed",
    addTag: "Add tag", yourTags: "Your tags", categories: "Categories", feedManager: "Subscriptions",
    addFeed: "Add feed", importExport: "Import / export", myFeeds: "My feeds", folder: "Folder",
    discover: "Discover", addAndSync: "Add and sync", url: "Feed or website URL", displayName: "Display name",
    interval: "Polling interval", importOpml: "Import OPML", exportOpml: "Export OPML",
    createDomain: "Create domain", matchAny: "Match ANY", matchAll: "Match ALL", generalMode: "General mode",
    translation: "Translation", targetLanguage: "Target language", recentJobs: "Recent jobs",
    interfaceLanguage: "Interface language", security: "Security", save: "Save", close: "Close",
    manualBrief: "Generate brief", schedules: "Schedules", notes: "Notes", exportMarkdown: "Export Markdown",
    setupTitle: "Create the owner password", loginTitle: "Sign in to Affogato RSS Reader", password: "Password",
    confirmPassword: "Confirm password", continue: "Continue", connectError: "Make sure the Affogato RSS Reader service is running.",
    selected: "selected", markRead: "Mark read", archive: "Archive", loadMore: "Load more",
    refreshSource: "Refresh source", showAll: "Show all", unreadOnly: "Unread only", markAllRead: "Mark all read",
    translationWarning: "Titles and summaries are sent to the selected provider. In automatic mode, Google GTX is used only after a primary-provider failure.",
    noAuthWarning: "Authentication is disabled. Only expose this service on a trusted network.",
  },
  "zh-CN": {
    all: "全部文章", unread: "未读", starred: "收藏", later: "稍后读", archived: "归档",
    briefs: "简报", sources: "订阅源", domains: "领域", tags: "标签", manage: "管理",
    settings: "设置", logout: "退出", search: "搜索标题、作者、DOI 或标签",
    original: "原文", translated: "译文", bilingual: "双语", noEntries: "这里暂时没有文章",
    emptyHelp: "添加 RSS 或 Atom 订阅源来建立你的阅读库。", retry: "重试", requestFailed: "请求失败",
    selectArticle: "选择一篇文章", openOriginal: "阅读原文", originalSummary: "原文",
    translatedSummary: "译文", noSummary: "这个订阅源没有提供摘要。",
    notTranslated: "未翻译", translationFailed: "翻译失败",
    addTag: "添加标签", yourTags: "你的标签", categories: "分类", feedManager: "订阅管理",
    addFeed: "添加订阅", importExport: "导入 / 导出", myFeeds: "我的订阅", folder: "文件夹",
    discover: "自动发现", addAndSync: "添加并同步", url: "Feed 或网站 URL", displayName: "显示名称",
    interval: "轮询间隔", importOpml: "导入 OPML", exportOpml: "导出 OPML",
    createDomain: "创建领域", matchAny: "匹配任一", matchAll: "匹配全部", generalMode: "通用模式",
    translation: "翻译", targetLanguage: "目标语言", recentJobs: "最近任务",
    interfaceLanguage: "界面语言", security: "安全", save: "保存", close: "关闭",
    manualBrief: "生成简报", schedules: "计划", notes: "备注", exportMarkdown: "导出 Markdown",
    setupTitle: "创建所有者密码", loginTitle: "登录 Affogato RSS Reader", password: "密码",
    confirmPassword: "确认密码", continue: "继续", connectError: "请确认 Affogato RSS Reader 服务正在运行。",
    selected: "篇已选", markRead: "标为已读", archive: "归档", loadMore: "加载更多",
    refreshSource: "刷新本订阅源", showAll: "显示全部", unreadOnly: "只显示未读", markAllRead: "全部标为已读",
    translationWarning: "标题和摘要会发送给所选服务；自动模式下，仅当主服务失败时才使用 Google GTX。",
    noAuthWarning: "当前已关闭身份认证。请只在可信局域网中开放本服务。",
  },
} as const;

export function browserLocale(): Locale {
  return navigator.language.toLowerCase().startsWith("zh") ? "zh-CN" : "en";
}

export function t(locale: Locale, key: MessageKey): string {
  return messages[locale][key] ?? messages.en[key];
}
