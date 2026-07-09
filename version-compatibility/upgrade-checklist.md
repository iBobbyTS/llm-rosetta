# Codex 升级审查与测试清单

每次更新 Codex CLI、同步 `../openai-codex-src`，或修改 Codex-facing gateway/
converter 行为时执行本清单。未完成的项目必须作为兼容声明中的明确限制。

## 权威执行顺序

升级必须按以下顺序完成，不能用后一步替代前一步：

1. 记录升级前 Codex CLI 版本、Codex 源码 commit、Codex-Rosetta 版本和 commit；
2. 确认 `../openai-codex-src` 没有未提交的 tracked 修改，然后 fetch 并
   fast-forward-only pull 到远端最新版本；
3. 记录目标 Codex 发布版本和新的源码 commit，运行 contract diff；
4. 对 `compatibility-points.md` 中**每一个兼容点**逐条分类为：高置信度没有变化、
   可能没有变化、有变化；
5. 为所有“有变化”项制定修复方案；为“可能没有变化”项制定人工审查和真实测试方案；
6. 完成修复并运行所有自动化测试，包括兼容专项、lint 和全仓非集成测试；
7. 对所有“可能没有变化”和“有变化”的兼容点调用真实 API 测试，默认使用
   `deepseek-v4-flash`，不适用时记录替代模型和原因；
8. 所有门禁通过后，更新 contract baseline、升级报告和文档，把 Codex-Rosetta 包
   版本提升到目标 Codex 发布版本，并记录精确源码 commit。

`make check-codex-compat` 输出的是 contract group 分类，只是第 4 步的证据之一；它
不能代替逐条兼容点分类，也不能覆盖真实客户端行为。

## 1. 记录升级基线

先保留升级前状态：

```bash
codex --version
git -C ../openai-codex-src status --short --branch
git -C ../openai-codex-src rev-parse HEAD
git -C ../openai-codex-src log -1 --date=iso-strict --format='%H%n%ad%n%s'
git status --short --branch
git rev-parse HEAD
```

把旧 CLI 版本、Codex 源码完整 commit、Codex-Rosetta 版本/commit 和日期写入升级
报告。确认源码 checkout 没有 tracked 修改后再更新：

```bash
test -z "$(git -C ../openai-codex-src status --porcelain --untracked-files=no)"
git -C ../openai-codex-src fetch origin
git -C ../openai-codex-src pull --ff-only
git -C ../openai-codex-src rev-parse HEAD
git -C ../openai-codex-src log -1 --date=iso-strict --format='%H%n%ad%n%s'
```

如果 tracked checkout 不干净或 pull 不能 fast-forward，停止升级并先处理源码仓库
状态。不要 reset、stash 或覆盖现有工作。不要用 Codex 源码 manifest 中的
`0.0.0`/`0.0.0-dev` 代替 commit，也不能把未执行 fetch 的本地 `origin/main` 当成
远端最新状态。

## 2. 审查 Codex 源码契约 diff

至少检查升级前后以下文件：

```text
codex-rs/codex-api/src/common.rs
codex-rs/codex-api/src/endpoint/responses.rs
codex-rs/codex-api/src/sse/responses.rs
codex-rs/core/src/client.rs
codex-rs/core/src/responses_metadata.rs
codex-rs/core/src/session/turn.rs
codex-rs/core/src/context_manager/
codex-rs/core/src/compact_remote.rs
codex-rs/core/src/tools/spec_plan.rs
codex-rs/core/src/tools/handlers/apply_patch_spec.rs
codex-rs/core/src/tools/handlers/tool_search.rs
codex-rs/protocol/src/models.rs
codex-rs/protocol/src/openai_models.rs
codex-rs/tools/src/tool_spec.rs
codex-rs/models-manager/models.json
```

重点确认：

- HTTP `/responses`、SSE 与 WebSocket 的选择/fallback 是否改变；
- request body 新增、删除或改变的字段；
- `client_metadata`、`x-codex-*`、session/thread/window/turn 的来源和生命周期；
- `response.output_item.*`、text/tool/reasoning delta 和 terminal event；
- `MessagePhase` 枚举与缺失 phase 的 fallback；
- function/custom/namespace/tool_search/web_search 等工具 wire shape；
- `apply_patch` 的 tool type、grammar、call/output item；
- reasoning effort、summary、encrypted content 和 response headers；
- compact、resume、fork、subagent 后的历史与 window generation；
- `ModelInfo` 新字段、enum/default 和未知模型 fallback。

## 3. 对照 Rosetta 所有权边界

对每个 Codex contract diff，明确落到以下所有权处置类别之一：

1. **Direct passthrough 已自然保留**：补测试证明未知字段或原始 SSE 未被改写；
2. **Bridge 必须适配**：修改 converter/gateway，并添加 request、stream 和多轮测试；
3. **当前不支持**：记录禁用方式、fallback 依赖和启用前的测试条件。

重点复查：

```text
src/codex_rosetta/gateway/app.py
src/codex_rosetta/gateway/headers.py
src/codex_rosetta/gateway/proxy.py
src/codex_rosetta/gateway/stream_phase_buffer.py
src/codex_rosetta/gateway/tool_adaptation.py
src/codex_rosetta/gateway/web_search.py
src/codex_rosetta/converters/openai_responses/
src/codex_rosetta/converters/openai_chat/
src/codex_rosetta/converters/base/helpers/tool_orphan_fix.py
src/codex_rosetta/types/openai/responses/
```

### 逐条兼容点分类与修复方案

源码 diff 和自动 contract 报告完成后，复制 `compatibility-points.md` 的全部兼容点，
逐条填写以下字段；兼容点数量必须与源清单一致。报告保存到
`version-compatibility/reports/YYYYMMDD-codex-vX.Y.Z.md`：

| 兼容点 | 分类 | 源码/contract 证据 | 修复或审查方案 | 自动化结果 | 真实 API 结果 |
| --- | --- | --- | --- | --- | --- |
| `<兼容点名称>` | 高置信度没有变化 / 可能没有变化 / 有变化 | `<commit/diff/代码位置>` | `<无需修复、审查步骤或修复方案>` | `<命令和结果>` | `<模型、route、结果或未触发原因>` |

分类规则：

- **高置信度没有变化**：相关源码语义和 Rosetta 所有权边界都能由完整 diff/hash/
  自动测试证明未变；仅“字段名相同”不足以进入本类；
- **可能没有变化**：名称或表面结构一致，但类型、default、serde、调用时序、客户端
  消费或模型行为没有被完整证明；
- **有变化**：源码、wire contract、默认值、行为、Rosetta 适配或测试期望任一变化。

“有变化”必须先写修复方案再实施；“可能没有变化”必须写清未知点和真实测试如何
消除不确定性。两个类别都必须有真实 API 结果，不能用 mock/fixture 代替。

## 4. 测试分层

升级测试分成两类，必须分别记录结果：

1. **可通过自动化完成的测试**：可以完全使用 fixture、fake upstream、本地 gateway、
   静态检查或确定性断言完成，不要求真实模型参与。
2. **必须实际测试的项目**：必须启动真实 Codex 客户端并连接真实模型/上游。可以用
   脚本或 agentabi 编排，但不能用 mock 或录制响应替代实际模型行为。

当前阶段不要求下面列出的自动化测试都已经实现。清单同时承担自动化建设 backlog：
未实现的项目标记为“可自动化、待实现”，而不是误记为“无需测试”。

### 4.1 可通过自动化完成

#### A. Codex 源码 contract 变化检测

以下检查可以做成升级前后 commit 的自动 diff/snapshot job：

- 监测第 2 节所列 Codex 源码文件是否发生变化；
- 导出并比较 `ResponsesApiRequest` 的字段集合和默认值；
- 导出并比较 `ResponseItem`、`ResponseInputItem`、`MessagePhase` 和 SSE event 名；
- 导出并比较 function/custom/namespace/tool_search/web_search 的 tool schema；
- 导出并比较 `apply_patch` freeform grammar 和 call/output wire shape；
- 导出并比较 `ModelInfo` 字段、enum 值、默认值和 bundled model 配置；
- 监测 `x-codex-*`、session/thread/window/turn metadata 的 header/body key；
- 监测 `/responses`、Responses WebSocket、`/responses/compact` 和 Responses Lite 的
  endpoint、feature flag 与 fallback 变化。

自动 diff 负责提示“哪里变了”，不能替代维护者对语义和 Rosetta 影响的审查。

首批已实现：

```bash
make check-codex-compat
```

`scripts/check_codex_compatibility.py` 当前会从 `../openai-codex-src` 定点提取并比较：

- Codex 源码 commit；
- Responses HTTP/WebSocket/compact request 字段集合；
- Response/Input/Content item、phase 和内部 response event 的 Rust variant 集合；
- 实际 SSE parser 处理的 event 名；
- Codex HTTP header、turn metadata、WebSocket metadata key、endpoint 和 beta value；
- `apply_patch` 名称、format、syntax 和 grammar SHA-256；
- tool spec wire type、`ModelInfo` 字段和关键 model enum variant。

提取器的 parser、deterministic baseline 和 diff 语义由普通 pytest 覆盖；跨仓的当前
源码比较是显式升级门禁。源码路径/锚点缺失会返回错误，不会 skip 或误报通过。

每次执行检查时必须保留三类结果：

- **高置信度没有变化的**：当前自动化完整比较的具体契约值一致；
- **可能没有变化的**：只证明当前可提取的名称/成员集合一致，仍需人工审查未覆盖
  的类型、default、serde 和行为语义；
- **有变化的**：commit 或已提取值发生变化，必须结合详细 diff 判断 Rosetta 影响。

不能把“可能没有变化”合并进通过项，也不能因为 unified diff 为空就省略这三类。
自动化的最终 exit code 只表达是否存在阻断变化/提取错误，不替代对第二类的审查。

仍待实现：字段类型、serde rename/default/skip 策略、SSE match arm digest、完整通用
tool schema、tool_search 默认值、bundled model 能力子集和 model fallback initializer。
因此当前 baseline 只证明上述已提取集合没有漂移，不能宣称 A 节所有项目已完成。

#### B. Fixture 和单元/组件测试

以下行为可以使用固定 Codex request/SSE fixture 自动验证：

- Responses→Responses direct path 保留未知字段、原始 JSON 和原始 SSE bytes；
- header allowlist、`x-codex-window-id` 提取及缺失 header 时的 fallback；
- Responses request → IR/adapter → Chat/Anthropic/Google upstream request；
- non-streaming/streaming upstream response → Codex Responses output；
- `response.created`、item added/delta/done、completed/failed/incomplete 的顺序；
- message `commentary`/`final_answer` 在 added、done、completed 中保持一致；
- 文本后接 function/custom/MCP/shell/computer/tool_search/web_search call 的 phase 推断；
- custom `apply_patch` 定义、delta 拼接、call/output 和 fallback command；
- native/localized 工具历史映射、TTL、持久化、失败结果和后续一轮重放；
- namespace defer、`tool_search_call/output`、多次搜索和 window 隔离；
- `multi_agent_v1`、`multi_agent_v2/collaboration` 的已捕获 wire fixture；
- code mode `exec/wait`、nested call 和 wait continuation 的已捕获 wire fixture；
- web search 多轮事件重建和禁用/缺少 key 的降级路径；
- reasoning effort/summary/content/encrypted state 的跨格式往返；
- compact 后 orphan call/result、残留 tool choice/config 和 history trimming；
- 并发 window、缓存过期、正常 EOF、异常 EOF、上游 4xx/5xx 和重试边界；
- `/v1/models` 当前通用响应，以及未来单独实现的 Codex `ModelInfo` catalog contract；
- 配置/admin UI 对 Codex tool-adaptation 开关的保存、默认值和运行时加载。

本轮新增的 fixture/组件覆盖：

- `tool_search_call`/`web_search_call` 的流式 item event 和 completed-only phase fallback；
- 两个 Codex window 的 deferred namespace 搜索双向隔离；
- Codex source contract 提取器对 Rust comment/string/braces、enum wire rename、commit 与
  contract drift 分离，以及 baseline canonical serialization 的测试。

其余项目仍按本节清单作为自动化 backlog；特别是 window 隔离目前是 store-level
组件测试，完整本地 gateway 并发回放尚未实现。

现有专项测试命令：

```bash
CONDA_ENV="${CODEX_ROSETTA_CONDA_ENV:-llm-rosetta}"
conda run -n "$CONDA_ENV" python -m pytest \
  tests/gateway/test_app_headers.py \
  tests/gateway/test_responses_passthrough.py \
  tests/gateway/test_stream_phase_buffer.py \
  tests/gateway/test_window_tool_search_store.py \
  tests/gateway/test_tool_adaptation.py \
  tests/gateway/test_web_search_bridge.py \
  tests/gateway/test_config.py \
  tests/gateway/test_admin_config_routes.py \
  tests/converters/openai_chat/test_message_ops.py \
  tests/converters/openai_chat/test_tool_ops.py \
  tests/converters/openai_responses/test_tool_ops.py \
  tests/converters/openai_responses/test_stream.py \
  tests/converters/test_strip_orphaned_tool_config.py \
  tests/test_codex_source_contract.py \
  tests/test_pipeline.py -q
```

当前开发机沿用历史环境名 `llm-rosetta`；如果环境已按项目重命名，通过
`CODEX_ROSETTA_CONDA_ENV` 覆盖。不要在环境不存在时把测试记为通过。

#### C. 本地集成和质量门禁

以下项目也可以完全自动化，不需要真实模型：

- 启动本地 gateway，对 fake Responses/Chat upstream 回放完整多轮 fixture；
- 并发发送两个 window，验证 phase、tool mapping 和 deferred tools 不串状态；
- 回放升级前后捕获的脱敏 Codex request，比较转换结果和 SSE transcript；
- 对每个新增 wire shape 运行 request、response、stream、history 四方向回归；
- lint、format、type/build、全仓非集成测试；
- 检查兼容基线、源码 commit 和升级记录是否同步更新；
- 检查日志/trace fixture 不包含 API key 或未脱敏敏感 header。

当前全仓门禁：

```bash
make lint
make test
```

转换、gateway 或 public type 发生行为变化时，不得只运行新增测试文件。

### 4.2 必须连接真实 Codex 和模型测试

这些项目依赖模型是否理解工具说明、是否形成正确多轮行为，以及 Codex 客户端实际
如何消费事件，不能由 fixture 或 mock 证明。

#### 推荐实际测试模型

优先使用 `deepseek-v4-flash`：成本低，并且能覆盖本项目最重要的
Responses→Chat bridge、reasoning 和多轮工具调用路径。

- 默认实际测试先用 `deepseek-v4-flash` 跑完整矩阵；
- 如果某项只适用于 same-format Responses、特定 hosted tool 或模型专属能力，再补充
  对应上游；
- 如果 `deepseek-v4-flash` 暂时不可用，使用能力相近、支持工具调用的低成本模型，
  并在结果中记录替代模型，不能无说明地跳过；
- 实际测试必须记录 model、provider/route、Codex CLI 版本、Codex 源码 commit 和
  Codex-Rosetta commit。

#### A. 基础会话和真实请求

- 启动真实 Codex CLI/Desktop，经 Rosetta 连接 `deepseek-v4-flash`；
- 完成单轮文本和多轮对话，确认没有重复、截断或无法结束的 turn；
- 捕获真实 HTTP headers 和 body `client_metadata`，确认 identity/turn metadata；
- 验证同一 turn、compact、resume、fork、subagent 的 window/thread 变化；
- 确认 direct path 不丢字段，bridge path 不把 Rosetta 内部 metadata 泄漏给上游；
- 确认实际流在 `response.completed` 前不会异常结束，failed/incomplete 能正确呈现。

#### B. UI、phase 和 steering 行为

- commentary 在 Codex 中显示/折叠为工作过程，final answer 不被误折叠；
- commentary 完成后 mailbox/steering 能打断或补充当前任务；
- 多段 commentary、reasoning、工具调用和 final answer 的顺序符合实际 UI；
- 流式输出没有长时间被错误缓冲，也不会把中间工作误判为最终答案。

#### C. 真实工具闭环

- 读取真实文件并使用 native `apply_patch` 完成修改；
- 让一次 patch 失败，再确认模型能读取错误、修正 patch 并续轮；
- function tool 与 custom tool 并存时，模型选择和 Codex 执行都正确；
- `request_user_input`、Goal/Plan 和 Desktop/runtime-only tools 能按真实 schema 调用；
- plugin/MCP namespace 经 `tool_search` 找到工具、实际调用并消费结果；
- `multi_agent_v1` 和版本提供的 `multi_agent_v2/collaboration` 能 spawn、通信、等待并
  返回结果，parent/window/thread 不串扰；
- model catalog 启用 code mode 时，实际验证 `exec/wait` 和 nested tool continuation；
- 启用 web search 时，模型能发起搜索、读取结果并继续生成最终答案。

#### D. Reasoning、历史和恢复

- `deepseek-v4-flash` 的 `reasoning_content` 在工具前后和后续 turn 中保持可续轮；
- reasoning summary/encrypted state 不导致无效请求或重复思考内容；
- compact/resume 后不出现孤立 tool output、重复工具调用或历史缓存错乱；
- 长对话达到 compact 阈值后仍能继续完成文件工具任务；
- 关闭并恢复 Codex session 后，历史工具映射和最终行为仍一致。

#### E. 真实 transport 和故障行为

- HTTP SSE 是当前基本必测路径；
- WebSocket、Responses Lite、remote compact 只有在明确启用后才能声明支持；
- 如果这些能力未实现，必须实际确认 Codex 会安全 fallback 到 HTTP/SSE；
- 实际制造一次上游限流、鉴权失败或中断，确认 Codex 能显示可理解错误且不会进入
  无限重试/重复工具执行。

实际测试可以由 agentabi 或脚本重复执行，但通过条件必须来自真实 Codex + 真实上游
的结果和 trace，不能用本地 fake upstream 代替。

## 5. 完成记录与版本升级

完成后更新：

- `version-compatibility/README.md` 的基线；
- `version-compatibility/compatibility-points.md` 的实现/限制；
- 受影响的测试路径和结果；
- 未完成的 live test、凭据限制或明确不支持能力。

记录时分别列出：

- 已实现并通过的自动化测试；
- 可自动化但尚未实现的项目；
- 已使用 `deepseek-v4-flash` 或其他真实模型完成的实际测试；
- 因能力未启用、缺少凭据或环境限制而未执行的实际测试。

确认逐条报告没有未解决项后：

1. 更新 `version-compatibility/codex-source-contract.json` 到已审查的新 commit；
2. 更新 `README.md` 的旧/新版本、commit、测试结果和逐条分类报告位置；
3. 将 `src/codex_rosetta/__init__.py` 的包版本提升到目标 Codex **发布版本**；
4. 再运行 `make check-codex-compat`、`make lint` 和 `make test`。

Codex 源码 manifest 若仍是 `0.0.0-dev`，不能据此生成包版本。必须使用与该源码
checkout 对应的目标 Codex release/CLI 版本作为包版本，并继续用完整 commit 标识
源码快照；映射无法确认时，升级不能标记完成。

只有源码审查、适用的自动化测试、全仓门禁和必须实际执行的 Codex 测试都完成后，
才能把新版本标记为已验证兼容。
