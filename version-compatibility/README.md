# Codex 版本兼容性

这个目录是 Codex-Rosetta 与 Codex CLI/源码之间的兼容性事实来源。它记录的
不是泛化的 OpenAI Responses API 支持，而是本项目为 Codex 客户端行为主动维护
的契约、当前限制，以及 Codex 升级时必须重新审查和测试的边界。

## Codex 源码位置

本项目使用的本地 Codex 源码位于：

```text
../openai-codex-src
```

路径相对于 Codex-Rosetta 仓库根目录。源码仓库自身的版本字段可能是
`0.0.0`/`0.0.0-dev`，因此兼容基线必须同时记录：

1. 本机安装的 Codex CLI 版本；
2. `../openai-codex-src` 的完整 commit；
3. Codex-Rosetta 的版本和 commit；
4. 实际通过的测试与尚未验证的能力。

不能只凭版本号相同就声明兼容。

## 包版本命名

Codex-Rosetta 使用 `{codex_version}.r{patch_number}`：前三段与目标 Codex CLI
release 对齐，`rN` 表示该 Codex release 下的 Rosetta 补丁序号。每次采用新的 Codex
release 时从 `r0` 开始；只有 Rosetta 自身发生后续修复时才递增 `rN`。源码保留
`rN` 字面量，Python 包元数据会将其规范化为等价的 PEP 440 `.postN`。

## 当前检查基线

检查日期：2026-07-09

| 项目 | 当前值 | 说明 |
| --- | --- | --- |
| 本机 Codex CLI | `codex-cli 0.144.0` | 来自 `codex --version` |
| Codex 源码分支 | `main` | `../openai-codex-src` |
| Codex 源码 commit | `2e8c3756f95789c215d9ea9a5ade6ec377934b3f` | `build: ratchet direct reqwest dependencies (#31431)` |
| Codex 源码时间 | `2026-07-09T11:53:18-07:00` | 源码 checkout 的最新 commit 时间 |
| Codex-Rosetta 包版本 | `0.144.0.r0` | Codex `0.144.0` 的首个 Rosetta patch |

这个表记录本次发布审查使用的精确源码快照；Codex CLI 发布版本和源码 commit 仍是
两个独立的兼容性标识。

## 本次验证结果

| 检查 | 结果 |
| --- | --- |
| Codex 源码契约检查 | 更新基线后通过；匹配 `2e8c3756f957…` |
| Codex 专项与定向回归 | `404 passed`；扩展回归 `425 passed, 6 warnings`；Responses converter `356 passed` |
| `make lint` | 通过；ruff check 与 format check 均通过 |
| `make test` | `2326 passed, 4 skipped, 27 warnings` |
| 真实 Codex/API | `deepseek-v4-flash` 经隔离 gateway 完成 Lite/code-mode、读取和多轮工具测试；GPT 原请求观测仍需真实 GPT route |

此前文档审查的完整测试第一次运行时，
`TestPipelineProfile::test_profile_populated_after_convert_request` 出现一次临时失败；该
测试单独复跑通过，随后完整 `make test` 复跑也通过。本轮新增自动化后的完整测试未
再次出现该失败；若后续重复出现，仍应作为测试隔离/稳定性问题单独调查。

## 文件说明

- [`compatibility-points.md`](compatibility-points.md)：本项目当前实现的 Codex
  强相关兼容点、所有权边界、证据路径和已知限制。
- [`upgrade-checklist.md`](upgrade-checklist.md)：Codex 版本更新时的源码 diff，
  并明确区分可自动化测试 backlog 与必须连接真实 Codex/模型执行的实测门禁。
- [`codex-source-contract.json`](codex-source-contract.json)：从当前 Codex 源码提取并经
  人工审查后保存的机器可比较契约基线。
- [`reports/`](reports/README.md)：每次 Codex 升级的旧/新版本、逐条分类、修复方案、
  自动化结果、真实 API 结果和最终版本决定。

首批自动化入口：

```bash
make check-codex-compat
```

该命令会严格比较源码 commit 和已提取契约。缺少 `../openai-codex-src`、提取锚点
消失或契约变化都会失败；只有审查完源码变化后才能运行
`make update-codex-compat-baseline` 更新基线。普通 `make test` 只测试提取器本身，
不会因为 CI 机器没有 sibling Codex checkout 而失败。

每次检查固定输出三个部分：

1. **高置信度没有变化的**：源码 commit 相同，或完整比较的常量、wire 映射、event
   名称、endpoint、metadata key、`apply_patch` grammar hash 等完全一致；
2. **可能没有变化的**：当前提取器只能证明字段名或 enum 成员集合一致，尚未完整比较
   字段类型、serde 策略、默认值或运行时语义，必须继续人工审查；
3. **有变化的**：源码 commit、任一已提取 contract group 或提取结构发生变化，并附
   详细 unified diff。

三类即使为空也会显示。默认情况下“有变化”会使检查返回 exit code 1；提取失败或
路径缺失返回 exit code 2。`--ignore-source-commit` 只允许 commit 变化不影响退出码，
报告仍会把这个事实列在“有变化的”中并注明已忽略。

## 维护规则

- 日常开发中发现或新增任何 Codex 专用适配时，必须在
  `compatibility-points.md` 增加或更新对应兼容点，并同时写清可自动化完成的检查、
  何时必须连接真实 Codex/API 测试，以及推荐的实际测试场景。
- Codex CLI 或 `../openai-codex-src` 更新时，必须执行升级检查清单。
- 升级必须先记录旧 commit，再把 `../openai-codex-src` fast-forward 到远端最新版本；
  不能只比较未 fetch 的本地 `origin/main`。
- `make check-codex-compat` 报告 commit 或 contract drift 时，必须先完成语义审查；
  不得直接刷新基线让检查变绿。
- `make check-codex-compat` 的三类 contract-group 输出只是证据输入。最终报告必须对
  `compatibility-points.md` 中每一个兼容点逐条给出“高置信度没有变化”“可能没有变化”
  或“有变化”，不能遗漏未被提取器覆盖的兼容点。
- 所有自动化测试必须执行；分类为“可能没有变化”或“有变化”的兼容点必须连接真实
  Codex/API 测试。修复和门禁全部通过后，才把 Codex-Rosetta 包版本提升为
  `{codex_version}.r{patch_number}`，并同时记录精确源码 commit。
- 新增、修改或删除 Codex 专用适配时，必须在同一任务更新兼容点清单。
- 直接 Responses 透传和 Responses→Chat 转换要分开判断：前者可以自然保留未知
  字段，后者经过 IR 和网关适配，新增 wire shape 必须显式审查。
- “未使用”不等于“已兼容”。WebSocket Responses、Responses Lite、远程 compact
  或动态 Codex model catalog 等能力只有在实际测试后才能标记为支持。
- 兼容声明必须保留测试证据；缺少凭据或真实上游时，明确标记为“未验证”，不能
  用单元测试替代端到端结论。
