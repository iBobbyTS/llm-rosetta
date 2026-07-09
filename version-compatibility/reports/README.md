# Codex 升级报告

每次 Codex 源码升级都必须在本目录保存独立报告，文件名格式：

```text
YYYYMMDD-codex-vX.Y.Z.md
```

在 pull 前先创建报告并写入旧 Codex CLI 版本、旧源码 commit、Codex-Rosetta 版本和
commit。pull 后再补目标 Codex 发布版本和新源码 commit。

每份报告至少包含：

1. 旧/新 Codex CLI 版本、源码 commit、日期和 Codex-Rosetta 版本/commit；
2. `make check-codex-compat` 的三类 contract-group 输出；
3. `compatibility-points.md` 中所有兼容点的逐条分类，行数必须与源清单一致；
4. 每个“有变化”项的修复方案和结果；
5. 所有自动化测试的命令和结果；
6. 每个“可能没有变化”或“有变化”项的真实 API 测试模型、route、场景和结果；
7. 未解决限制、是否允许升级，以及最终 Codex-Rosetta 包版本。

逐条分类表使用：

| 兼容点 | 分类 | 源码/contract 证据 | 修复或审查方案 | 自动化结果 | 真实 API 结果 |
| --- | --- | --- | --- | --- | --- |
| `<从 compatibility-points.md 复制>` | 高置信度没有变化 / 可能没有变化 / 有变化 | `<证据>` | `<方案>` | `<命令和结果>` | `<模型、route 和结果>` |

高置信度没有变化的行可以把真实 API 结果记录为“本次未触发”，但必须保留该兼容点
在 `compatibility-points.md` 中定义的实际测试场景。可能没有变化和有变化的行必须有
真实 API 结果，不能用 mock 或 fixture 代替。
