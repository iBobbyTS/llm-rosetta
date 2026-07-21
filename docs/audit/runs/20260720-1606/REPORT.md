# 第四轮独立遗漏审计报告

运行：`20260720-1606`  
模式：Periodic  
审计 HEAD：`26b7558b1b54160c201ed9cedb1e80a1aa188d95`  
分支状态：`main` 相对 `origin/main` ahead 6；本轮开始时实现工作树干净  
Profile：`docs/audit-profile.md`（Approved）  
修复授权：已授权 AUD-015/AUD-016；当前报告包含修复后的 phase-separated targeted re-audit

## 结论

本轮先确认两个新的 `Must Fix / Agent-Fixable` finding，随后在获授权的同一修复波中完成实现，并由主流程做阶段分离验证：

| ID | 结论 | 当前状态 |
| --- | --- | --- |
| AUD-015 | Provider、web-run sidecar 与 Admin model discovery 的不可信返回内容可反射已发送的配置凭据；恶意 JSON object key 也是返回面 | Closed (deterministic) |
| AUD-016 | Provider key rotation 与全局精确值脱敏清单原本使用不同 credential 解析结果 | Closed (deterministic) |

两项都不需要新的业务语义决定。Approved profile 已明确：provider key/sidecar token 是 crown jewel，配置 token 必须脱敏，恶意 provider/tool 内容在威胁模型内，且不容忍 credential leakage。

本轮没有发现新的 redirect policy 绕过或 real-call approval 绕过。所有复现均使用 dummy token 与进程内 fake transport；没有运行任何可能产生真实 provider、Codex、Tavily、sidecar 或 agent API 调用的命令。

## AUD-015 - Provider 与 sidecar 返回边界可反射配置凭据

严重度：`Must Fix`  
决策类：`Agent-Fixable`  
置信度：High

### 根因

Credential-bearing outbound clients 没有共同的返回边界不变量。请求路径会把配置凭据放入 auth header，但 provider raw passthrough、converted error、SSE、sidecar success/error/exception 返回路径在进入客户端、模型或诊断链路前没有一致地删除该凭据。Sidecar transport exception 还用 `raise ... from exc` 保留了含 secret 的原始 cause。

日志的 `SecretRedactor` 只能保护已交给日志状态的副本，不能保护原样返回给 downstream 的 response。

### 可达路径与精确证据

Provider 路径：

1. `src/codex_rosetta/gateway/transport/provider_info.py:91-93` 从 key ring 选择 wire key 并构造 auth header。
2. `src/codex_rosetta/gateway/proxy.py:1495-1535` 在 Responses passthrough 的成功和错误路径直接返回 `resp.raw_content`。
3. `src/codex_rosetta/gateway/proxy.py:1623-1648` 在 converted error 路径同样返回 raw upstream body。
4. `src/codex_rosetta/gateway/proxy.py:2138-2165` 原样 yield upstream SSE bytes；`:2315-2367` 原样返回 stream-header error body。

Web-run sidecar 路径：

1. `src/codex_rosetta/gateway/web_run_sidecar.py:82-100`、`:135-153` 发送 Bearer，并把 transport exception 文本嵌入保留 cause 的新异常。
2. `src/codex_rosetta/gateway/web_run_sidecar.py:102-120`、`:155-184` 原样返回 success data 或使用不可信 error/detail。
3. `src/codex_rosetta/gateway/codex_auxiliary.py:313-341` 把结果或异常送入 Codex Search response/trace。
4. `src/codex_rosetta/gateway/codex_search.py:100-104`、`:354-364`、`:527-544`、`:870-897` 把 sidecar 输出转为模型/客户端可见数据。

本地 fake probe 结果：

```text
sidecar_success_reflects_token True
sidecar_error_reflects_token True
sidecar_exception_chain_reflects_token True cause_retained True
provider_200_reflects_token True
provider_401_reflects_token True
```

Provider 401 的诊断日志副本显示 `[REDACTED]`，而同一 response body 仍包含 dummy token，直接证明日志脱敏不是 downstream 边界控制。

### 最小验收标准

1. Provider 与 web-run sidecar 实际发送的每个配置凭据，在 success、HTTP error、stream、transport exception 和 cause 链进入任何客户端、模型、trace、log、metrics 或 persistence 前都被删除。
2. Provider passthrough/converted、streaming/non-streaming 全部覆盖；SSE 必须覆盖 token 被任意拆在多个 chunk 的情况。
3. Sidecar `execute`/`search` 的成功对象、结构化/纯文本错误与 transport exception 全部覆盖；含 secret 的原始 cause 必须脱敏或断开。
4. 除 secret 精确值外，status、error schema、SSE framing/order 和非 secret 内容保持兼容。
5. 使用 adversarial fake fixtures 增加回归测试，并同时覆盖 AUD-016 的每个 rotated wire key。

## AUD-016 - Rotated wire key 未进入全局脱敏清单

严重度：`Must Fix`  
决策类：`Agent-Fixable`  
置信度：High

### 根因

同一个 provider `api_key` 字段存在两个独立解析器：

- `collect_token_values()` 把完整 CSV 当作一个 exact token；
- `KeyRing` 按逗号拆分、trim，并逐个把单独 key 发送到 wire。

`SecretRedactor` 只做已登记 exact-value replacement，因此只知道 `"key-A, key-B"` 时无法删除普通字段里的 `key-A`。这个不完整集合会同时传播给 error/body logs、stream trace、metrics、persistence/error dump，以及未来 AUD-015 的返回边界脱敏器。

### 可达路径与精确证据

1. `src/codex_rosetta/observability/redaction.py:43-60` 把 provider `api_key` 作为单一字符串加入集合。
2. `src/codex_rosetta/observability/redaction.py:69-94` 只替换集合中登记的 exact values。
3. `src/codex_rosetta/gateway/transport/provider_info.py:28-45` 独立拆分 CSV；`:91-93` 发送其中一个 key。
4. `src/codex_rosetta/gateway/config.py:716-717` 在构建 provider 前采集 token；`gateway/app.py:1092-1097` 把集合交给运行时日志状态。
5. `src/codex_rosetta/gateway/admin/routes/_shared.py:145-215` 在 hot reload 时把同一集合传播给 trace、error/body log、persistence 与 metrics。

本地真实 `GatewayConfig` + `ProviderInfo` + `UpstreamErrorLogState` probe：

```text
registered_exact_individual_keys False False
registered_raw_csv True
active_rotated_key rotate-secret-A
diagnostic_reflects_active_key True
```

### 最小验收标准

1. Rotation 与 redaction 使用同一个 canonical credential parser；trim、空项、重复项和顺序语义明确，且每个可能被发送的 wire key 都进入脱敏清单。
2. 单 key 和现有 rotation 顺序不变；raw CSV 可继续登记，但不能代替 individual keys。
3. 启动与 atomic hot reload 在新 provider 状态生效前，把完整 key set 一致更新到 logs、trace、metrics、persistence 和 AUD-015 return redactors；失败回滚不得形成新旧状态混用。
4. 回归测试覆盖所有 rotation positions、空白/空段、重复/前缀重叠 key、普通嵌套字段反射和 hot reload 后旧 key 的移除。
5. Admin、日志或持久化工件不得暴露 canonical parsed key list。

## 旁路反证

### Real-call / agent gate

独立语义 inventory 检查了 dotenv/credential reads、SDK/httpx/urllib client、`agentabi`、`subprocess.run/Popen`、Python main entry points 和 executable shell scripts，而不是只依赖文件名。当前 integration runners、24 个 examples、`dev_scripts/test_roundtrip_live.py`、GPT relay、两类 nested live-agent launchers 和 Codex/Claude/OpenCode shell launchers 都在敏感操作前调用 shared exact-marker gate。

`scripts/rosetta-test-kilo.sh` 虽为 executable，但只输出 IDE 操作提示，不读凭据、不启动进程、不发网络请求。当前 HEAD 结论为 `No Action`；新增 atypically named runner 仍会 invalidate AGENT-01/CTRL-06。

### Redirect / direct HTTP

Shared bounded helper 仍默认 `max_redirects=0`；provider pool 按 redirect policy 分池；provider/model-discovery 只有 explicit provider opt-in 才跟随；sidecar、Tavily、health、Admin internal tasks 与其它 auxiliary direct callers没有开启 redirect。Focused loopback tests 通过，没有发现新的 AUD-012 绕过。

## 运行的检查

```text
conda run -n llm-rosetta pytest -q \
  tests/gateway/test_web_run_sidecar.py \
  tests/gateway/test_responses_passthrough.py \
  tests/observability/test_redaction.py \
  tests/live_agent/test_live_agent_configuration_contract.py \
  tests/gateway/test_http_transport_limits.py

125 passed in 11.89s
```

现有测试全绿只说明原有 oracle 满足；它们没有断言 sidecar/provider reflected credential，也没有把 CSV key rotation 与 redactor inventory 绑定。三个 sidecar probe、两个 provider response probe 和一个 rotated-key diagnostic probe 均为进程内 fake/dummy 数据。

## 修复与 targeted re-audit 结论

AUD-015 的控制边界现在覆盖 provider passthrough/converted、streaming/non-streaming、parsed/raw、HTTP error、transport exception、sidecar execute/search、Codex auxiliary provider 返回和 Admin model discovery。原始 SSE credential 可在任意 chunk 边界拆分；除精确 credential replacement 外，非 secret bytes、顺序和 framing 保持不变。异常文本在重新暴露前脱敏，并断开含 secret 的 cause/context。

独立 review 进一步发现配置凭据可作为恶意 JSON object key 泄漏。`SecretRedactor.redact()` 与 `redact_exact()` 现在同时处理 string/bytes keys 和 values；非字符串 key 保持原行为。脱敏后 key collision 采用确定性的普通 dict 语义，即后出现的 source item 覆盖先前项，优先保证原始 secret key 不留存。

AUD-016 现在由 `KeyRing` 的单次 canonical parsing 同时拥有 rotation order 与 `ProviderInfo.credential_values`。`GatewayConfig` 保留 raw CSV 并登记每个实际可发送的 trimmed key；startup 与 atomic hot reload/rollback 将完整集合一致传播到 logs、trace、metrics、persistence 和 return redactors。

修复后补漏包括：Admin `fetch_upstream_models` 以 `pinfo.credential_values` 建立边界，测试先推进 rotation，再证明第二个 dummy key 是实际 wire key；成功 model ID、连接异常与日志均无 secret。Provider 集成回归还证明 credential 作为 object key 时不会进入 downstream body。

阶段分离验证结果：

```text
focused remediation and adversarial re-audit: 158 passed
conda run -n llm-rosetta make lint: passed
conda run -n llm-rosetta make test: 3542 passed, 5 skipped, 11 warnings
```

因此 AUD-015 的六项和 AUD-016 的五项冻结 acceptance criteria 均满足，两个 finding 关闭。该关闭仅代表当前 working tree 的 deterministic fake/unit/integration-with-fakes 证据，不代表真实 provider、sidecar、外部 sink 或生产部署行为。

## 未覆盖与证据边界

- 未运行新的 wheel/Docker build；本次边界修复已通过 full deterministic suite、lint、format/type 组合检查。
- 未运行真实 provider、Codex、Tavily、web-run sidecar、agentabi 或任何 live runner。
- 未验证真实 provider 对 key 的反射行为、真实 SSE timing/chunking、外部日志 sink、生产/Compose runtime、DNS rebinding 或 configured proxy。
- Public deployment、HA、restore、SLO、GitHub external settings 与 provider quality 仍为 profile 排除项或 `Unknown`，本报告不作安全声明。

## 下一步

AUD-015 在新增 credential-bearing client/return path、认证方式、raw/parsed stream、dict-key serialization、异常传播或 diagnostic sink 变化时重开。AUD-016 在 credential syntax/parser、environment substitution、key selection、provider construction、startup/hot-reload activation/rollback 或 redactor-consumer 变化时重开。下一轮优先级回到真实 provider/sidecar 与外部 sink 的经批准 evidence，以及现有 Unknown deployment/recovery 面。
