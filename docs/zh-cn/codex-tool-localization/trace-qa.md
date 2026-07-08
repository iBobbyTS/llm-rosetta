# Trace QA

## 不明的 `gpt-5.4` 或 `gpt-5.4-mini` 调用从哪里来？

当用于 DeepSeek/Codex 工具测试的 stream trace 文件中混入 `gpt-5.4` 或 `gpt-5.4-mini` 请求时，先按“同一 gateway 同时记录了其他 Codex 流量”来排查，不要直接判断为 DeepSeek 路由串模型。

常见判断口径：

- `gpt-5.4` 的不明调用大概率来自记忆创建或记忆整理任务。典型特征是 `cwd` 指向 `/Users/ibobby/.codex/memories`，用户输入包含 `Memory Writing Agent`、`Phase 2` 或 consolidation 相关文本，stage 多为 `raw_passthrough_request` / `raw_passthrough_chunk`。
- `gpt-5.4-mini` 的不明调用大概率来自 Codex 起 thread 标题或轻量后台整理。典型特征是它走 OpenAI Responses 同格式直通，通常不是 DeepSeek 的 Responses-to-Chat 转换链路。

排查顺序：

1. 按 `request_id` 分组，看 `source_provider`、`target_provider`、`provider_name` 和 stage。
2. 如果是 `openai_responses -> openai_responses` 且 stage 为 `raw_passthrough_*`，优先视为同 gateway 上的直通流量。
3. 抽取请求里的 `cwd` 和最后一条用户输入摘要，确认它是否属于当前测试目录。
4. 只有当 `deepseek-v4-flash` 请求本身出现非预期 upstream model 时，才按路由配置错误继续查。
