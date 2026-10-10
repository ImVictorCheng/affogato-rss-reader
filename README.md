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
- 受控自动标签：批量分类、候选主题自动或手动晋升、别名复用与手动标签保护
- 文章标签支持手动添加、拖动排序和按文章保存权重，卡片展示权重最高的两个标签
- 文章日期优先显示订阅源提供的更新时间，缺失时依次使用发布日期、收录日期
- 灵活自定义代理，适配不同网络环境
- 自定义LLM生成每日、每周、每月和年度简报，支持设置自动计划

### 受控自动标签

封闭模式只允许选择已有且允许自动使用的标签；阈值模式会把新主题放入候选池，默认在最近 365 天内获得至少 10 个不同 Work 的支持后自动晋升。Work 是去重后识别的独立作品，归并到同一 Work 的不同来源或版本只计一次。每篇文章最多附加 3 个置信度不低于 0.80 的自动标签；手动标签不受此上限影响，也不会被自动重打删除。手动移除的标签会受到保护，后续自动处理不会重新附加它。

在设置中选择已配置的 LLM 连接后，可以直接启用自动打标签，每批处理最多 10 篇文章。试跑、全量批准和旧标签清理预览目前已停用，相关代码保留以便恢复。已完成表示文章分类已结束，也可能没有匹配标签；之后模型、策略、正式标签库或别名变化都不会将这些文章标记为需要重建。新文章和文章内容变化的文章仍会进入处理队列，预计 LLM 调用次数按剩余未完成或内容变化的文章估算。

设置页中的正式标签使用复选框管理，支持全选、反选和删除。删除标签会移除它与文章、订阅源的关联；被启用中的简报计划引用时，须先调整计划。候选主题以同样的卡片样式列在标签设置最后，可以手动晋升，无需等待自动晋升阈值。晋升会复用别名，并在自动标签数量上限和手动移除保护范围内，为仍有效的支持文章附加该标签，无需再次调用 LLM。晋升记录写入后台应用日志。

发送给所选 LLM 的文章内容字段只有标题和摘要，并使用批次内临时序号；作者、Feed、领域和 RSS categories 仅在本地用于检索相关标签。请求还会包含筛选后的受控标签及活跃候选的名称、描述和别名。新晋升标签使用英文正式名，中文名称、缩写及近义表达保存为别名；现有手动标签不会自动改名。

### 手动标签与排序

在文章详情的“你的标签”中可以选择已有标签，也可以输入名称并按 Enter 创建、附加新标签。点击标签名称不会执行操作，右侧叉号只移除这篇文章上的标签关联。

标签按权重从高到低排列，权重相同时按名称字母顺序排列。未手动排序时，有模型置信度的标签使用该置信度作为权重，没有置信度的标签默认权重为 1。拖动标签或聚焦标签后按左右方向键可以调整顺序；保存后，最后一个标签的权重为 1，向前依次加 1。例如，四个标签依次获得 4、3、2、1 的权重。权重只属于这篇文章，不影响其他文章，也不修改原始模型置信度。文章卡片在领域右侧显示权重最高的两个标签。

### 快速启动

需要 Docker Desktop 或 Docker Engine + Compose。下载 GitHub Release 中的`affogato-rss-reader-0.5.1.tar.gz` 并解压后：

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

Planned work, including LLM Chat targeted for 0.6.0 and reusable entry preprocessing cards for faster brief generation, is tracked in [docs/ROADMAP.md](docs/ROADMAP.md). Roadmap items are not implemented features unless they also appear in the changelog.

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
