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
- 灵活自定义代理，适配不同网络环境
- 自定义LLM生成每日、每周、每月和年度简报，支持设置自动计划

### 快速启动

需要 Docker Desktop 或 Docker Engine + Compose。下载 GitHub Release 中的`affogato-rss-reader-0.3.1.tar.gz` 并解压后：

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
