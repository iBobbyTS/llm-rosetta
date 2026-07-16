# Codex-Rosetta 用户文档

## 兼容性

- [Codex 版本兼容性](version-compatibility.md)
- [Codex 模型目录字段参考](codex-model-catalog.md)

## 当前协议支持状态

目前已重点开发并保证的网关路径仅有：

- OpenAI Responses 到 OpenAI Chat Completions 的协议转换；
- 所有 Provider 都直接传输 OpenAI Responses；除模型组 Tool Profile 外，仅模型切换压缩使用 Rosetta 管理的明文交接。

Anthropic 和 Google 转换仍是内部选项，目前不作保证。管理界面只保留一个 OpenAI Responses 协议；Provider 类别只决定默认 Tool Profile，不会改变同协议 Responses 的处理路径。

## 网关运维

- [安全与认证](gateway-security.md)

终端支持四个日志级别：

```bash
codex-rosetta-gateway --log-level info
codex-rosetta-gateway --log-level stats
codex-rosetta-gateway --log-level warning
codex-rosetta-gateway --log-level error
```

运行 `codex-rosetta-gateway --with-web-run` 可以在宿主机 Gateway 启动时一并拉起
可选的浏览器/PDF sidecar。CLI 会从回环端口 `8766` 开始选择第一个空闲端口，等待
Chromium 就绪，并在退出时清理托管 service。

`warning` 是默认档位，不打印每个正常请求，但保留 warning 和 error；`info` 还会打印
请求摘要；`stats` 在同一行持续刷新各模型的请求数，例如
`model-1: 12, model-2: 7`。计数键使用 provider 的原始 upstream 模型名，不使用对外
暴露的模型别名。遇到 warning 或 error 时会先换行打印，下一次请求再继续刷新统计行；
`error` 只打印错误。完整请求历史请在 WebUI 的 **请求日志（Request Log）** 中查看；
流式 trace 诊断请使用 WebUI 的 **网关日志（Gateway Logs）**。

## Codex 工具本地化

- [基础对话](codex-tool-localization/basic-conversation.md)
- [代码编辑](codex-tool-localization/code-edit.md)
- [其他工具](codex-tool-localization/other-tools.md)
- [Agent 实机工具测试结果](tools/real-agent-test-results.md)

架构说明、源码契约和维护流程请参阅[开发者文档（英文）](../dev/README.md)。
