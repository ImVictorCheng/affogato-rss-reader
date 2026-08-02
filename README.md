# Affogato RSS Reader

A private, self-hosted RSS/Atom reader for one owner and multiple devices.

> [!IMPORTANT]
> 本项目完全由 OpenAI Codex 生成。
>
> 本项目目前仅在 Windows 10 22H2 + Docker Desktop（Linux 容器模式）环境中通过
> `docker compose` 完成测试；其他操作系统、容器运行时及源码安装方式尚未验证，
> 暂时无法保证可用。
>
> This project was generated entirely by OpenAI Codex.
>
> The project has currently been tested only with `docker compose` on Windows 10
> 22H2 using Docker Desktop in Linux container mode. Other operating systems,
> container runtimes, and source-based installation methods have not been
> verified and are not currently guaranteed to work.

[中文说明](#中文说明) · [English](#english)

## 中文说明

Affogato RSS Reader 是一个可独立发布的通用阅读器。空数据库不会预装任何订阅；你可以手动添加 RSS/Atom 地址、从网站自动发现 feed，或导入 OPML。领域、文件夹和标签是三个独立维度：多领域 `ANY` 显示并集，`ALL` 显示真正的交叉内容。

主要能力：

- 全部、未读、收藏、稍后读、归档与批量操作
- 桌面三栏和移动端堆叠布局，支持 `j/k`、`m`、`s`、`l`、`a`、`o`
- 多源去重、ETag/Last-Modified、304、退避、故障隔离和手动刷新
- 全字段搜索、服务端阅读状态、领域继承与文章手动领域
- 订阅添加后仍可修改文件夹和多领域分类；支持文件夹重命名/移除及领域新增、改名、改色和删除
- 可选 Custom LLM、DeepL 或 Google Cloud 翻译，支持流式超时、自动重试和分块续传，并可选择自动或手动回退到 Google GTX
- 每日、每周、每月和年度简报，支持实时进度、断点续传、编辑生成规则、删除历史简报与设置自动计划
- 单一 owner 登录，或由部署者显式设置无登录模式
- 同一服务层提供 Web、OpenAPI 和 `affogato-rss-reader` CLI

### 快速启动

需要 Docker Desktop 或 Docker Engine + Compose。下载 GitHub Release 中的
`affogato-rss-reader-0.3.0.tar.gz` 并解压后：

```console
docker compose up -d
```

安装时会自动注册 owner 并生成高强度的一次性初始密码。获取密码：

```console
docker compose exec reader affogato-rss-reader initial-password
```

也可以通过 `docker compose logs reader` 查看首次启动时输出的密码。浏览器打开
`http://服务器IP:8787`，输入初始密码并设置长期密码；激活成功后初始密码立即失效，
数据卷中的明文密码文件也会删除。

Compose 默认监听所有网络接口，以支持局域网服务器和 VPS。Windows 防火墙若拦截
8787，只为“专用网络”放行该端口。直接通过互联网访问时必须配置 HTTPS；按
[反向代理指南](docs/REVERSE_PROXY.md)配置可信代理与
`AFFOGATO_RSS_READER_COOKIE_SECURE=true`。如果只需本机访问，在 `.env` 中设置
`AFFOGATO_RSS_READER_BIND_ADDRESS=127.0.0.1`。

创建 owner 后，首次向导会要求选择一个或多个领域并指定主领域。内置模板覆盖自然科学、
物理学、量子物理、计算机科学、人工智能、数学、金融、工程技术、半导体、EDA、
通信与信号处理
和材料科学；父领域、细分领域、自定义领域与交叉领域会按权重组合，无需模型服务。
每个内置领域都有独立站点名与 Logo，任意两个内置领域也会生成专属交叉品牌。选择三个
以上领域或包含自定义领域时，可以自己填写站点名并上传不超过 256 KB 的 PNG、JPEG 或
WebP Logo；两项均留空则回退到 Affogato RSS Reader 默认品牌。模板生成的名称与 Logo 也可以在
首次设置中覆盖，或在设置页随时分别修改和恢复。
翻译设置支持 OpenAI-compatible Custom LLM、DeepL API 和 Google Cloud
Translation。回退策略可设为自动尝试 Google GTX，或在主服务失败后停止、由用户
手动切换到 GTX 并重试。设置页提供独立的“LLM 连接”区域，可新增、编辑、测试和删除
可复用的命名连接；翻译只需选择其中一个连接，未来的 LLM 功能既可以复用该连接，也可以
使用独立 Key。设置页保存的 Key 会加密写入数据库且不会由
状态 API 返回；Docker 将主密钥保存在独立的 `affogato-rss-reader-secrets` Compose 卷。也可以改用 `.env` 中的
`AFFOGATO_RSS_READER_TRANSLATION_LLM_API_KEY`、`AFFOGATO_RSS_READER_DEEPL_API_KEY` 或
`AFFOGATO_RSS_READER_GOOGLE_CLOUD_TRANSLATION_API_KEY`。保存前后均可使用“测试调用”
直接检查当前主服务的 Key、端点和模型；测试不会回退到 GTX。
设置页另有独立的“网络代理”子页面，支持 HTTP、HTTPS 与 SOCKS5 自定义代理。每个订阅源、
LLM 连接、Google GTX、DeepL 和 Google Cloud Translation 都可独立选择自定义代理、
系统代理或直连；Custom LLM 翻译沿用所选 LLM 连接的代理设置。系统代理读取 `HTTP_PROXY`、
`HTTPS_PROXY` 与 `ALL_PROXY`，直连会明确忽略这些环境变量。设置页可用当前表单值并发
测试 `https://google.com/` 与 `https://bing.com/`，并分别显示两个结果。代理认证密码与
API Key 使用同一套主密钥加密，API 不返回明文密码。应用全局代理用于更新检查、Release
资产下载以及没有提供独立代理选项的网络功能；订阅源、LLM 和翻译服务仍优先使用各自的
设置。GHCR 镜像层由 Docker Engine 拉取，因此使用 Docker 宿主机的代理配置。

应用在每次启动时以及实例时区每天 05:00 检查 GitHub Release。发现新版本后会通过应用
全局代理自动下载并校验该版本的 Compose 资产，但不会自行安装。owner 可在页面提示或
“账户与系统”中点击“安装并重启”；安装前会创建 SQLite 备份。发布版 Compose 中的更新
辅助服务不发布任何对外端口，主 Web 服务也不会获得 Docker Socket。辅助服务只接受固定仓库、
严格版本号和 SHA-256 已验证的 Compose 文件，更新失败时会恢复旧 Compose 并尝试回滚。

代理运行在 Docker 宿主机上时，地址使用
`http://host.docker.internal:7890`，不要使用 `127.0.0.1`。
可选的 AI 个性化支持 OpenAI-compatible API，并使用用户自己的 Base URL、API Key
和模型名称。API Key 仅用于当次生成且不会写入数据库。请只在宿主机 `localhost`
或配置了 HTTPS 的页面提交 API Key。

从 Git 源码检出构建（源码模板在创建远程仓库前不会猜测 GHCR 所有者）：

```console
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
```

Windows PowerShell、Linux 和 macOS 的命令相同；无需 `Copy-Item` 或手动复制配置文件。

### 配置

全部环境变量使用 `AFFOGATO_RSS_READER_*`。常用项见 [.env.example](.env.example)：

- `AFFOGATO_RSS_READER_AUTH_MODE=owner|none`
- `AFFOGATO_RSS_READER_DEBUG=false`
- `AFFOGATO_RSS_READER_TIMEZONE=UTC`
- `AFFOGATO_RSS_READER_TRANSLATION_ENABLED=false`
- `AFFOGATO_RSS_READER_TRANSLATION_TARGET=zh-CN`
- `AFFOGATO_RSS_READER_SYNC_ON_STARTUP=false`
- `AFFOGATO_RSS_READER_UPDATE_CHECK_ENABLED=true`
- `AFFOGATO_RSS_READER_UPDATE_CHECK_HOUR=5`
- `AFFOGATO_RSS_READER_LLM_SUMMARY_TIMEOUT_SECONDS=30`
- `AFFOGATO_RSS_READER_BRIEF_BATCH_CONCURRENCY=2`

`AUTH_MODE=none` 仍保留 Origin/CSRF 防护，但任何能访问服务的人都可读取或修改数据，仅适合可信网络。

`AFFOGATO_RSS_READER_DEBUG=true` 会在设置页启用“注销并删除 owner”。它会删除 owner 密码、
全部登录会话、阅读状态和个性化配置，并返回首次部署流程；订阅源、文章、标签和领域会保留。
该开关默认关闭，不应在生产部署中启用。

### 数据、备份与升级

Compose 使用 `affogato-rss-reader-data` 卷保存 SQLite WAL 数据库和备份。应用内备份也可通过 CLI 创建：

```console
docker compose exec reader affogato-rss-reader backup
docker compose exec reader affogato-rss-reader sync
docker compose exec reader affogato-rss-reader opml export /app/data/subscriptions.opml
```

Compose 的一次性 `log-init` 服务会为非 root 应用准备宿主机的 `./logs`
目录；不要从发布版 Compose 中删除它。完整 LLM/翻译调用日志保存在
`logs/llm-translation.jsonl`。默认日志轮转上限约为 60 MiB；SQLite 备份同时受
30 天、14 个文件和 2 GiB 软上限约束，并始终保留最近两个已校验备份。

升级前执行备份，然后：

```console
docker compose pull
docker compose up -d
```

也可以使用应用内的更新提示完成备份、安装和重启。若更新辅助服务未运行，页面会保留
Release 链接并要求按上面的命令手动安装。Docker Engine 拉取 GHCR 镜像时使用 Docker
自身的代理配置；GitHub 版本检查和 Compose 资产下载使用应用设置中的全局代理。

数据库文件固定为 `affogato-rss-reader.db`。恢复时先停止服务，将备份数据库复制回
`affogato-rss-reader-data` 卷中的该路径，再启动。详见
[docs/BACKUP_AND_RESTORE.md](docs/BACKUP_AND_RESTORE.md)。

## English

Affogato RSS Reader starts with an empty library. Add any RSS/Atom URL, discover feeds from a site, or import OPML. Optional domain spaces are independent from folders and tags; selecting multiple domains supports true `ANY` union and `ALL` intersection views.

Briefs support daily, weekly, monthly, and yearly LLM summaries with live
progress, resumable checkpoints, editable generation rules, reusable LLM
connections, deletion, and automatic schedules.

Feed classifications remain editable after subscription: each feed can move between
folders and multiple domains, while the category manager can rename or remove
folders and create, rename, recolor, or delete domains.

### Quick start

Download and extract the Compose bundle attached to a GitHub Release, then run
`docker compose up -d`.

Installation automatically creates the owner and generates a strong one-time
initial password. Retrieve it with:

```console
docker compose exec reader affogato-rss-reader initial-password
```

The password is also printed once in `docker compose logs reader`. Open
`http://SERVER_IP:8787`, enter the initial password, and choose a permanent
password. Activation immediately invalidates the initial password and removes
its plaintext file from the data volume.

Compose listens on all interfaces by default so LAN servers and VPS deployments
are reachable. Internet-facing deployments must use HTTPS; follow the
[reverse-proxy guide](docs/REVERSE_PROXY.md), including a narrowly scoped
`AFFOGATO_RSS_READER_FORWARDED_ALLOW_IPS` value and
`AFFOGATO_RSS_READER_COOKIE_SECURE=true`. Set
`AFFOGATO_RSS_READER_BIND_ADDRESS=127.0.0.1` to restrict access to the Docker
host. To build from a Git checkout before a GHCR owner has been configured:

```console
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
```

Never expose authentication-free mode directly to the public internet.

After owner creation, the first-run wizard asks for one or more fields and a primary
field. Built-in composable templates cover science, physics, quantum physics,
computing, AI, mathematics, finance, engineering, semiconductors, EDA,
communications and signal processing, and materials science. Parent, specialized,
custom, and cross-disciplinary fields blend
without a model service. Every built-in field and every pair of built-in fields has
its own generated site name and logo. Collections with three or more fields, or a
custom field, can use an optional site name and PNG, JPEG, or WebP logo up to
256 KB; leaving both blank falls back to the Affogato RSS Reader brand. Generated names
and logos can also be overridden during onboarding, changed independently later
in Settings, or restored to the template defaults. Optional AI
personalization accepts an OpenAI-compatible
Base URL, API key, and model. The key is used for that generation only and is never
stored; submit it only from `localhost` on the host machine or over HTTPS.

Translation supports an OpenAI-compatible custom LLM, DeepL API, or Google
Cloud Translation. Fallback can automatically try Google GTX, or stop after the
primary provider fails so the user can switch to GTX and retry manually. Transient
errors use bounded automatic retry, while completed text chunks are cached so a
later retry skips finished work. Translation
Settings has an independent LLM connections section for adding, editing,
testing, and deleting reusable named connections. Translation selects one of
those connections, while future LLM features can share it or use separate keys. Keys saved
in Settings are encrypted and are never returned by the status API. Docker
keeps the master key in a separate `affogato-rss-reader-secrets` Compose volume. The corresponding
`AFFOGATO_RSS_READER_TRANSLATION_*` environment variables remain available. “Test call” checks the selected
provider, key, endpoint, and model without falling back to GTX.
The independent Network proxy Settings page supports HTTP, HTTPS, and SOCKS5
custom proxies. Every feed, LLM connection, Google GTX, DeepL, and Google Cloud
Translation provider can independently use the custom proxy, the system proxy
(`HTTP_PROXY`, `HTTPS_PROXY`, or `ALL_PROXY`), or an explicit direct connection
that ignores those variables. Custom LLM translation follows the route selected
for its LLM connection. The current form values can test
`https://google.com/` and `https://bing.com/` concurrently, with both results
shown separately. Proxy passwords use the same encrypted secret store as API keys
without being returned by the API.

The application-wide proxy route covers update checks, GitHub Release asset
downloads, and other network features without their own override. Feed, LLM,
and translation routes remain more specific and therefore take precedence.
GHCR image layers are pulled by Docker Engine and therefore use the Docker
host's proxy configuration.

The application checks GitHub Releases on every startup and daily at 05:00 in
the configured instance timezone. A newer Compose asset is downloaded through
the application-wide route and verified against GitHub's SHA-256 asset digest,
but installation always waits for owner confirmation. The release Compose
bundle includes an isolated update helper with no published ports for one-click
backup, install, and restart. The helper has no network interface; it asks the
Docker Engine to pull only the exact digest-pinned release image, so registry
layer transfers use the Docker host's proxy configuration. GitHub metadata and
the Compose asset use the selected application-wide route. The Web service never
receives the Docker socket. If the
helper is unavailable, the UI links to the release for manual installation.

When a proxy runs on the Docker host, use
`http://host.docker.internal:7890`, not `127.0.0.1`.

The one-shot `log-init` Compose service prepares the host `./logs` directory
for the non-root application user. Keep that service in the release Compose
file; complete LLM and translation call logs are written to
`logs/llm-translation.jsonl`. Logs retain at most five 10 MiB rotations by
default. SQLite backups are atomically integrity-checked and bounded by age,
file count, and a soft total-byte limit while always retaining the newest two.

Set `AFFOGATO_RSS_READER_DEBUG=true` only on a development instance to expose the
“Sign out and delete owner” action in Settings. It removes the owner password,
all login sessions, reading states, and personalization before returning to
first-run setup; feeds, articles, tags, and domains remain. Debug mode is off by
default and should not be enabled in production.

### Development

Python 3.12 and Node 22 are supported.

```console
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-test.lock
.venv/Scripts/python -m pip install --no-deps -e backend
.venv/Scripts/python -m pytest backend/tests

cd web
npm ci
npm test
npm run build
```

On Linux/macOS use `.venv/bin/python`. API documentation is available at `/docs`. This project does not publish a standalone PyPI application in v0.3.0.

### Source-specific behavior

arXiv is an optional adapter, not a product dependency. arXiv identifiers, versions, DOI and announce type appear only when present. The UI shows arXiv attribution only while viewing arXiv content. See the [arXiv RSS documentation](https://info.arxiv.org/help/rss.html) and [API terms](https://info.arxiv.org/help/api/index.html).

## Roadmap

Planned work, including reusable entry preprocessing cards for faster brief
generation, is tracked in [docs/ROADMAP.md](docs/ROADMAP.md). Roadmap items are
not implemented features unless they also appear in the changelog.

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
