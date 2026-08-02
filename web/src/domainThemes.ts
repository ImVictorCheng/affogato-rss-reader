import type { Locale, SiteIdentity, ThemeConfig, ThemeDensity, ThemeMotif, ThemeTypography } from "./types";

export interface DomainTemplate {
  id: string;
  name: Record<Locale, string>;
  description: Record<Locale, string>;
  family: Record<Locale, string>;
  aliases: string[];
  parents: string[];
  palette: Pick<ThemeConfig, "accent" | "secondary" | "nav" | "paper" | "surface" | "ink">;
  density: ThemeDensity;
  typography: ThemeTypography;
  motif: ThemeMotif;
}

const template = (
  value: Omit<DomainTemplate, "palette"> & { palette: [string, string, string, string?, string?, string?] },
): DomainTemplate => ({
  ...value,
  palette: {
    accent: value.palette[0],
    secondary: value.palette[1],
    nav: value.palette[2],
    paper: value.palette[3] || "#f7f8fb",
    surface: value.palette[4] || "#ffffff",
    ink: value.palette[5] || "#182237",
  },
});

export const DOMAIN_TEMPLATES: DomainTemplate[] = [
  template({
    id: "science",
    name: { en: "Science", "zh-CN": "自然科学" },
    description: { en: "A clear research-first foundation", "zh-CN": "清晰、克制的研究型基础风格" },
    family: { en: "Parent field", "zh-CN": "父领域" },
    aliases: ["science", "natural science", "research", "自然科学", "科学", "科研"],
    parents: [],
    palette: ["#168c8a", "#6573c3", "#0b1723"],
    density: "balanced", typography: "editorial", motif: "grid",
  }),
  template({
    id: "physics",
    name: { en: "Physics", "zh-CN": "物理学" },
    description: { en: "Measured structure for theory and experiment", "zh-CN": "适合理论与实验内容的精确结构" },
    family: { en: "Science", "zh-CN": "自然科学" },
    aliases: ["physics", "physical science", "物理", "物理学", "凝聚态", "高能物理"],
    parents: ["science"],
    palette: ["#3274c7", "#7d65c7", "#0b1428", "#f5f7fc"],
    density: "balanced", typography: "editorial", motif: "orbit",
  }),
  template({
    id: "quantum-physics",
    name: { en: "Quantum physics", "zh-CN": "量子物理" },
    description: { en: "Spectral accents with an experimental cadence", "zh-CN": "光谱色强调与实验节奏并存" },
    family: { en: "Physics · Science", "zh-CN": "物理学 · 自然科学" },
    aliases: ["quantum", "quantum physics", "quantum optics", "quantum information", "量子", "量子物理", "量子光学", "量子信息", "冷原子"],
    parents: ["physics", "science"],
    palette: ["#16a6a1", "#8568df", "#091329", "#f5f8fc"],
    density: "balanced", typography: "technical", motif: "orbit",
  }),
  template({
    id: "computing",
    name: { en: "Computing", "zh-CN": "计算机科学" },
    description: { en: "Structured information for systems and software", "zh-CN": "面向系统与软件的结构化信息界面" },
    family: { en: "Parent field", "zh-CN": "父领域" },
    aliases: ["computing", "computer science", "software", "systems", "计算机", "计算机科学", "软件", "系统"],
    parents: [],
    palette: ["#158a9a", "#5f70d7", "#0b1520"],
    density: "compact", typography: "technical", motif: "network",
  }),
  template({
    id: "artificial-intelligence",
    name: { en: "Artificial intelligence", "zh-CN": "人工智能" },
    description: { en: "Dense signals, model networks and clear hierarchy", "zh-CN": "高信息密度、网络信号与清晰层级" },
    family: { en: "Computing · Mathematics", "zh-CN": "计算机科学 · 数学" },
    aliases: ["ai", "artificial intelligence", "machine learning", "deep learning", "llm", "人工智能", "机器学习", "深度学习", "大模型", "神经网络"],
    parents: ["computing", "mathematics"],
    palette: ["#0b9d8c", "#7367dc", "#08191c", "#f4f9f8"],
    density: "compact", typography: "technical", motif: "network",
  }),
  template({
    id: "mathematics",
    name: { en: "Mathematics", "zh-CN": "数学" },
    description: { en: "Proof-oriented rhythm with quiet contrast", "zh-CN": "面向证明与推导的安静对比和节奏" },
    family: { en: "Formal science", "zh-CN": "形式科学" },
    aliases: ["math", "mathematics", "statistics", "geometry", "algebra", "数学", "统计", "几何", "代数", "拓扑"],
    parents: ["science"],
    palette: ["#3a72b8", "#a06748", "#121827", "#faf8f3", "#fffefb", "#252b37"],
    density: "relaxed", typography: "editorial", motif: "proof",
  }),
  template({
    id: "finance",
    name: { en: "Finance", "zh-CN": "金融" },
    description: { en: "Fast scanning with restrained market signals", "zh-CN": "快速扫描与克制的市场信号表达" },
    family: { en: "Economics · Business", "zh-CN": "经济学 · 商业" },
    aliases: ["finance", "economics", "markets", "investment", "fintech", "金融", "经济", "市场", "投资", "量化"],
    parents: [],
    palette: ["#168064", "#b48336", "#0c1b19", "#f5f8f5"],
    density: "compact", typography: "technical", motif: "market",
  }),
  template({
    id: "engineering",
    name: { en: "Engineering", "zh-CN": "工程技术" },
    description: { en: "Practical, structured and specification-friendly", "zh-CN": "实用、结构化，适合规范与工程信息" },
    family: { en: "Parent field", "zh-CN": "父领域" },
    aliases: ["engineering", "technology", "electrical engineering", "工程", "工程技术", "电子工程", "电气工程"],
    parents: ["science"],
    palette: ["#167f9f", "#bd7732", "#0c1823"],
    density: "compact", typography: "technical", motif: "grid",
  }),
  template({
    id: "semiconductor",
    name: { en: "Semiconductors", "zh-CN": "半导体" },
    description: { en: "Layered materials and process-aware hierarchy", "zh-CN": "体现材料层次与工艺流程的信息结构" },
    family: { en: "Engineering · Physics", "zh-CN": "工程技术 · 物理学" },
    aliases: ["semiconductor", "microelectronics", "chip", "integrated circuit", "半导体", "微电子", "芯片", "集成电路", "器件"],
    parents: ["engineering", "physics"],
    palette: ["#087f8c", "#c77d38", "#07191d", "#f4f8f7"],
    density: "compact", typography: "technical", motif: "silicon",
  }),
  template({
    id: "eda",
    name: { en: "EDA", "zh-CN": "EDA" },
    description: { en: "Circuit topology and design automation precision", "zh-CN": "电路拓扑与设计自动化精度驱动的界面" },
    family: { en: "Semiconductors · Computing", "zh-CN": "半导体 · 计算机科学" },
    aliases: ["eda", "electronic design automation", "design automation", "电子设计自动化"],
    parents: ["semiconductor", "engineering", "computing"],
    palette: ["#007f91", "#dd7046", "#07151f", "#f3f7f8"],
    density: "compact", typography: "technical", motif: "circuit",
  }),
  template({
    id: "communications-signal-processing",
    name: { en: "Communications & signals", "zh-CN": "通信与信号处理" },
    description: { en: "Waveforms, information flow and system-level clarity", "zh-CN": "兼顾波形、信息流与系统层级" },
    family: { en: "Engineering · Mathematics", "zh-CN": "工程技术 · 数学" },
    aliases: ["communications", "signal processing", "information theory", "wireless", "通信", "信号处理", "信息论", "无线通信"],
    parents: ["engineering", "mathematics", "computing"],
    palette: ["#147d9b", "#8c6dc0", "#0a1721", "#f4f7f9"],
    density: "compact", typography: "technical", motif: "network",
  }),
  template({
    id: "materials",
    name: { en: "Materials science", "zh-CN": "材料科学" },
    description: { en: "Texture, composition and experimental clarity", "zh-CN": "兼顾组分、结构与实验可读性" },
    family: { en: "Science · Engineering", "zh-CN": "自然科学 · 工程技术" },
    aliases: ["materials", "materials science", "nanotechnology", "材料", "材料科学", "纳米技术", "二维材料"],
    parents: ["science", "engineering", "physics"],
    palette: ["#347d76", "#aa6d54", "#101a20", "#f8f7f3"],
    density: "balanced", typography: "editorial", motif: "silicon",
  }),
];

const DOMAIN_BRANDS: Record<string, { single: Record<Locale, string>; stem: Record<Locale, string> }> = {
  science: {
    single: { en: "Science Brief", "zh-CN": "科学简报" },
    stem: { en: "Science", "zh-CN": "科学" },
  },
  physics: {
    single: { en: "Physics Review", "zh-CN": "物理评论" },
    stem: { en: "Physics", "zh-CN": "物理" },
  },
  "quantum-physics": {
    single: { en: "Quantum Physics Digest", "zh-CN": "量子物理汇编" },
    stem: { en: "Quantum", "zh-CN": "量子" },
  },
  computing: {
    single: { en: "Computing Dispatch", "zh-CN": "计算前沿" },
    stem: { en: "Computing", "zh-CN": "计算" },
  },
  "artificial-intelligence": {
    single: { en: "AI Signal", "zh-CN": "AI 信号" },
    stem: { en: "AI", "zh-CN": "人工智能" },
  },
  mathematics: {
    single: { en: "Theorem Digest", "zh-CN": "数学定理汇编" },
    stem: { en: "Mathematics", "zh-CN": "数学" },
  },
  finance: {
    single: { en: "Market Ledger", "zh-CN": "市场台账" },
    stem: { en: "Markets", "zh-CN": "金融" },
  },
  engineering: {
    single: { en: "Engineering Review", "zh-CN": "工程评论" },
    stem: { en: "Engineering", "zh-CN": "工程" },
  },
  semiconductor: {
    single: { en: "Silicon Brief", "zh-CN": "硅基简报" },
    stem: { en: "Silicon", "zh-CN": "半导体" },
  },
  eda: {
    single: { en: "EDA Dispatch", "zh-CN": "EDA 前沿" },
    stem: { en: "EDA", "zh-CN": "EDA" },
  },
  "communications-signal-processing": {
    single: { en: "Signal & Spectrum", "zh-CN": "信号与频谱" },
    stem: { en: "Signals", "zh-CN": "通信信号" },
  },
  materials: {
    single: { en: "Materials Review", "zh-CN": "材料评论" },
    stem: { en: "Materials", "zh-CN": "材料" },
  },
};

const PAIR_SUFFIXES: Record<Locale, string[]> = {
  en: ["Convergence", "Review", "Exchange", "Digest"],
  "zh-CN": ["交叉简报", "联合评论", "前沿汇编", "融合观察"],
};

const DEFAULT_PALETTES = [
  ["#168c8a", "#6573c3", "#0b1723"],
  ["#3775b5", "#a36b53", "#111827"],
  ["#59733a", "#9270b4", "#151b16"],
  ["#956327", "#3d8292", "#1c1711"],
];

interface ThemeContribution {
  palette: DomainTemplate["palette"];
  density: ThemeDensity;
  typography: ThemeTypography;
  motif: ThemeMotif;
  weight: number;
}

function normalize(value: string) {
  return value.trim().toLocaleLowerCase().replace(/[\s_/·—–-]+/g, " ");
}

function exactTemplate(name: string) {
  const input = normalize(name);
  return DOMAIN_TEMPLATES.find((item) =>
    [item.name.en, item.name["zh-CN"], ...item.aliases].some((alias) => normalize(alias) === input),
  );
}

export function supportsGeneratedIdentity(domains: string[]) {
  return domains.length > 0 && domains.length <= 2 && domains.every((name) => Boolean(exactTemplate(name)));
}

export function composeSiteIdentity(
  domains: string[],
  locale: Locale,
  customName = "",
  logoDataUrl = "",
): SiteIdentity {
  if (supportsGeneratedIdentity(domains)) {
    const templates = domains.map((name) => exactTemplate(name)!);
    if (templates.length === 1) {
      const item = templates[0];
      return {
        name: DOMAIN_BRANDS[item.id].single[locale],
        source: "builtin",
        logo_kind: "generated",
        primary_template: item.id,
      };
    }
    const [first, second] = templates;
    const suffixes = PAIR_SUFFIXES[locale];
    const suffix = suffixes[hash([first.id, second.id].sort().join("|")) % suffixes.length];
    return {
      name: `${DOMAIN_BRANDS[first.id].stem[locale]} × ${DOMAIN_BRANDS[second.id].stem[locale]} ${suffix}`,
      source: "builtin",
      logo_kind: "generated",
      primary_template: first.id,
      secondary_template: second.id,
    };
  }
  const name = customName.trim();
  if (name || logoDataUrl) {
    return {
      name: name || "Affogato RSS Reader",
      source: "custom",
      logo_kind: logoDataUrl ? "upload" : "default",
      logo_data_url: logoDataUrl || undefined,
    };
  }
  return { name: "Affogato RSS Reader", source: "default", logo_kind: "default" };
}

export function customizeSiteIdentity(
  fallback: SiteIdentity,
  customName = "",
  logoDataUrl = "",
): SiteIdentity {
  const name = customName.trim();
  const hasCustomName = Boolean(name && name !== fallback.name);
  if (!hasCustomName && !logoDataUrl) return fallback;
  if (logoDataUrl) {
    return {
      name: name || fallback.name,
      source: "custom",
      logo_kind: "upload",
      logo_data_url: logoDataUrl,
    };
  }
  return {
    name,
    source: "custom",
    logo_kind: fallback.logo_kind,
    primary_template: fallback.primary_template,
    secondary_template: fallback.secondary_template,
  };
}

function hash(value: string) {
  let result = 2166136261;
  for (const char of value) result = Math.imul(result ^ char.charCodeAt(0), 16777619);
  return result >>> 0;
}

function hexToRgb(value: string) {
  const raw = value.slice(1);
  return [0, 2, 4].map((index) => Number.parseInt(raw.slice(index, index + 2), 16));
}

function rgbToHex(rgb: number[]) {
  return `#${rgb.map((value) => Math.round(Math.max(0, Math.min(255, value))).toString(16).padStart(2, "0")).join("")}`;
}

function blend(values: Array<{ color: string; weight: number }>) {
  const total = values.reduce((sum, item) => sum + item.weight, 0) || 1;
  const channels = [0, 1, 2].map((channel) =>
    values.reduce((sum, item) => sum + hexToRgb(item.color)[channel] * item.weight, 0) / total,
  );
  return rgbToHex(channels);
}

function matches(name: string) {
  const input = normalize(name);
  return DOMAIN_TEMPLATES.filter((item) =>
    item.aliases.some((alias) => {
      const candidate = normalize(alias);
      return input === candidate || (candidate.length >= 3 && input.includes(candidate));
    }),
  );
}

function contribution(name: string, primary: boolean): ThemeContribution[] {
  const direct = matches(name);
  if (!direct.length) {
    const palette = DEFAULT_PALETTES[hash(name) % DEFAULT_PALETTES.length];
    return [{
      palette: { accent: palette[0], secondary: palette[1], nav: palette[2], paper: "#f7f8fb", surface: "#ffffff", ink: "#182237" },
      density: "balanced" as const, typography: "balanced" as const, motif: "grid" as const,
      weight: primary ? 4 : 1,
    }];
  }
  const expanded = [...direct];
  direct.forEach((item) => item.parents.forEach((parent) => {
    const parentTemplate = DOMAIN_TEMPLATES.find((candidate) => candidate.id === parent);
    if (parentTemplate && !expanded.includes(parentTemplate)) expanded.push(parentTemplate);
  }));
  return expanded.map((item, index) => ({
    palette: item.palette,
    density: item.density,
    typography: item.typography,
    motif: item.motif,
    weight: (primary ? 4 : 1) * (index < direct.length ? 1 : 0.28),
  }));
}

function weightedChoice<T extends string>(items: Array<{ value: T; weight: number }>): T {
  const scores = new Map<T, number>();
  items.forEach(({ value, weight }) => scores.set(value, (scores.get(value) || 0) + weight));
  return [...scores].sort((a, b) => b[1] - a[1])[0][0];
}

export function composeDomainTheme(domains: string[], primaryDomain: string, locale: Locale): ThemeConfig {
  const parts = domains.flatMap((name) => contribution(name, normalize(name) === normalize(primaryDomain)));
  const colors = (key: keyof DomainTemplate["palette"]) =>
    blend(parts.map((part) => ({ color: part.palette[key], weight: part.weight })));
  const primaryMatch = matches(primaryDomain)[0];
  return {
    id: `builtin-${hash([...domains].sort().join("|")).toString(36)}`,
    label: primaryMatch
      ? `${primaryMatch.name[locale]}${domains.length > 1 ? (locale === "zh-CN" ? " · 交叉" : " · Cross-field") : ""}`
      : `${primaryDomain}${locale === "zh-CN" ? " · 自定义" : " · Custom"}`,
    accent: colors("accent"),
    secondary: colors("secondary"),
    nav: colors("nav"),
    paper: colors("paper"),
    surface: colors("surface"),
    ink: colors("ink"),
    density: weightedChoice(parts.map((part) => ({ value: part.density, weight: part.weight }))),
    typography: weightedChoice(parts.map((part) => ({ value: part.typography, weight: part.weight }))),
    motif: weightedChoice(parts.map((part) => ({ value: part.motif, weight: part.weight }))),
    source: "builtin",
    identity: composeSiteIdentity(domains, locale),
  };
}

export function mixColor(foreground: string, background: string, foregroundWeight: number) {
  return blend([
    { color: foreground, weight: foregroundWeight },
    { color: background, weight: 1 - foregroundWeight },
  ]);
}

export function applyTheme(theme?: ThemeConfig | null) {
  const root = document.documentElement;
  const variableNames = [
    "--ink", "--ink-soft", "--ink-faint", "--navy", "--navy-soft", "--paper",
    "--surface", "--surface-alt", "--line", "--line-strong", "--cyan", "--cyan-dark",
    "--cyan-soft", "--violet", "--violet-soft",
  ];
  if (!theme) {
    variableNames.forEach((name) => root.style.removeProperty(name));
    delete root.dataset.themeDensity;
    delete root.dataset.themeTypography;
    delete root.dataset.themeMotif;
    return;
  }
  const values: Record<string, string> = {
    "--ink": theme.ink,
    "--ink-soft": mixColor(theme.ink, theme.paper, 0.68),
    "--ink-faint": mixColor(theme.ink, theme.paper, 0.5),
    "--navy": theme.nav,
    "--navy-soft": mixColor(theme.nav, theme.surface, 0.9),
    "--paper": theme.paper,
    "--surface": theme.surface,
    "--surface-alt": mixColor(theme.accent, theme.paper, 0.055),
    "--line": mixColor(theme.ink, theme.paper, 0.12),
    "--line-strong": mixColor(theme.ink, theme.paper, 0.22),
    "--cyan": theme.accent,
    "--cyan-dark": mixColor(theme.accent, theme.ink, 0.72),
    "--cyan-soft": mixColor(theme.accent, theme.paper, 0.1),
    "--violet": theme.secondary,
    "--violet-soft": mixColor(theme.secondary, theme.paper, 0.1),
  };
  Object.entries(values).forEach(([key, value]) => root.style.setProperty(key, value));
  root.dataset.themeDensity = theme.density;
  root.dataset.themeTypography = theme.typography;
  root.dataset.themeMotif = theme.motif;
}
