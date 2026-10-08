# Affogato RSS Reader

A private, self-hosted RSS/Atom reader for one owner and multiple devices.

> [!IMPORTANT]
> 
>本项目目前仅在 Windows 10 22H2 + Docker Desktop（Linux 容器模式）环境中通过
> `docker compose` 完成测试；其他操作系统、容器运行时及源码安装方式尚未验证，
> 暂时无法保证可用。
> 
>The project has currently been tested only with `docker compose` on Windows 10
> 22H2 using Docker Desktop in Linux container mode. Other operating systems,
>container runtimes, and source-based installation methods have not been
> verified and are not currently guaranteed to work.

[中文说明](#中文说明) 

## 中文说明

Affogato RSS Reader 是一个自部署通用RSS阅读器。你可以手动添加 RSS/Atom 地址、从网站自动发现 feed，或导入 OPML。领域、文件夹和标签是三个独立维度：多领域 `ANY` 显示并集，`ALL` 显示真正的交叉内容。

主要能力：

- 多源去重、ETag/Last-Modified、304、退避、故障隔离和手动刷新
- 全字段搜索、服务端阅读状态、领域继承与文章手动领域
- 可选 Custom LLM、DeepL 或 Google Cloud 翻译，并可选择自动或手动回退到 Google GTX
- 受控自动标签：批量分类、候选主题阈值晋升、别名复用、来源保护与安全试跑
- 灵活自定义代理，适配不同网络环境
- 自定义LLM生成每日、每周、每月和年度简报，支持设置自动计划

### 受控自动标签

自动标签默认不会让一篇文章直接扩张正式标签库。封闭模式只允许选择已有且允许自动使用的标签；阈值模式会把新主题放入候选池，候选只有在最近 365 天内获得至少 10 个不同 Work 的支持后才会自动晋升。每篇文章最多附加 3 个置信度不低于 0.80 的自动标签，手动标签不受此上限影响，也不会被自动重打删除。

首次启用以及模型、策略或正式标签库变化后，应先在设置页检查旧标签清理预览，再运行跨订阅源抽样的 50 篇试跑。试跑本身不会改正式标签；只有 owner 明确批准后，系统才复用试跑结果并继续处理全部历史文章。约 2000 篇文章通常需要约 200 次 10 篇批量调用。

发送给所选 LLM 的文章内容字段只有标题和摘要，并使用批次内临时序号；作者、Feed、领域和 RSS categories 仅在本地用于检索相关标签。请求还会包含筛选后的受控标签及活跃候选的名称、描述和别名。新晋升标签使用英文正式名，中文名称、缩写及近义表达保存为别名；现有手动标签不会自动改名。

### 快速启动

需要 Docker Desktop 或 Docker Engine + Compose。下载 GitHub Release 中的`affogato-rss-reader-0.5.0.tar.gz` 并解压后：

从 0.3.1 或更早版本迁移时，替换 Compose 前先停止并删除旧版高权限更新助手：

```console
docker compose stop updater
docker compose rm -f updater
docker compose ps -a updater
```

Compose profile 只会跳过未启用的服务，不会自动删除已经创建的旧容器。

```console
docker compose up -d
```

安装时会自动注册 owner 并生成高强度的一次性初始密码。获取密码：

```console
docker compose exec reader affogato-rss-reader initial-password
```

也可以通过 `docker compose logs reader` 查看首次启动时输出的密码。浏览器打开`http://服务器IP:8787`，输入初始密码并设置长期密码；激活成功后初始密码立即失效，数据卷中的明文密码文件也会删除。

## Roadmap

Planned work, including reusable entry preprocessing cards for faster brief generation, is tracked in [docs/ROADMAP.md](docs/ROADMAP.md). Roadmap items are not implemented features unless they also appear in the changelog.

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
