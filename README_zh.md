# Codex-Rosetta

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

[English Version](README_en.md) | [中文版](README_zh.md)

**Codex-Rosetta** — 一个基于LLM-Rosetta的大模型网关，致力于将第三方大模型API服务接入Codex并优化工具调用、Plugin。

## Fork 定位

本项目 fork 自 [Oaklight/llm-rosetta](https://github.com/Oaklight/llm-rosetta)。这个 fork 聚焦于将 Chat Completions 兼容接口转换到 Responses API，并适配工具调用语义，让开源模型更好地适配 Codex，同时也聚焦于多 Provider 的网关聚合。面向 agent 的生成接口只暴露 OpenAI Responses；Chat Completions、Anthropic Messages 和 Google GenAI 格式只作为上游目标格式保留，不再作为下游客户端接口。

## 安装

克隆仓库，进入目录并安装：

```bash
git clone https://github.com/iBobbyTS/codex-rosetta.git
cd codex-rosetta
python -m pip install -U '.[gateway]'
```

`gateway` extra 会安装用于精确、跨重启工具历史持久化的成熟 AEAD 依赖。只使用核心
转换库、不启动 gateway 的使用方仍可直接安装 `.`。

## 使用

首次使用时初始化网关配置（仅需执行一次）：

```bash
codex-rosetta-gateway init
```

初始化会在仅当前用户可读的配置文件中生成必填的 Admin 密码和网关访问密钥。
请安全保存两者；所有受保护的 `/v1` 请求都必须把生成的访问密钥作为 Bearer
token 发送。详见[网关安全与认证](docs/zh-cn/gateway-security.md)。

每次使用时启动本地网关：

```bash
codex-rosetta-gateway --host 127.0.0.1 -v
```

### 本地模式

本地模式默认开启。它使用项目内预设的模型配置，自动匹配配置进网关的模型并注入
Codex，具体通过维护 `<codex-home>/model_catalog.json` 以及
`<codex-home>/config.toml` 中的 `model_catalog_json` 实现。在 WebUI 中修改模型时，
网关配置和 Codex 模型目录会同步更新。模型发生变化后需要重启 Codex，Codex 才会
重新加载目录。

第一次开启本地模式时，网关会先询问是否允许替换已有的 `model_catalog_json`
配置。可以通过 CLI 显式开启并持久化该状态，交互环境同样支持：

```bash
codex-rosetta-gateway --local-mode
```

非交互启动时，需要显式确认允许替换目录配置：

```bash
codex-rosetta-gateway --confirm-clear-existing-catalog
```

`--confirm-clear-existing-catalog` 只记录确认，不会单独开启已关闭的本地模式。如果
需要同时开启本地模式和跳过确认，请与 `--local-mode` 一起使用。

使用过 `--local-mode` 后，开启状态会写入网关配置，后续启动即使不再传入该参数也
会保持开启。若要关闭本地模式、删除 Rosetta 管理的目录并从 Codex
`config.toml` 中清除 `model_catalog_json`，运行：

```bash
codex-rosetta-gateway local-mode clear
```

目标 Codex Home 依次由 `--codex-home`、`CODEX_HOME` 决定，缺省为 `~/.codex`。
本地模式开启且网关监听地址不是 `127.0.0.1` 或 `localhost` 时，网关会提示：
“远程使用这个网关必须手动配置config.toml和model_catalog_json。”本地模式只会
修改网关所在机器上选定的 Codex Home，远程 Codex 客户端仍需手动配置。

## Codex 模型名称与内置功能模型

不建议把 `deepseek-v4-pro`、`glm-5.2` 等第三方模型名直接暴露给 Codex。
未知模型名会使用 fallback 模型元数据，进而改变 Codex 启用的工具、Responses
请求形态、推理控制、上下文限制和多 Agent 行为。建议在 Rosetta 模型组中使用
Codex 内置模型名作为公开名称，并用 `upstream_model` 保存服务商的真实模型 ID。

建议使用以下公开模型名：

- `gpt-5.6-sol`
- `gpt-5.6-terra`
- `gpt-5.5`
- `gpt-5.4`
- `gpt-5.4-mini`
- `gpt-5.2`

公开名称决定 Codex 选择的模型元数据和工具表；Rosetta 会在向服务商发送请求前
将其替换为 `upstream_model`。应选择上下文窗口、输入模态、推理行为和工具模式
与真实上游模型相符的内置名称。名称映射不会让上游模型获得其本身不支持的能力。

以下内置名称具有特殊用途：

- `gpt-5.6-luna` 使用旧版 multi-agent v1 工具，Subagent 可能工作异常；Rosetta
  内置的 **Chat Default** Tool Profile 还会禁用 `multi_agent_v1` Namespace。
- `gpt-5.4` 默认用于合并、整理记忆。如果服务商使用其他公开模型名，可在 Codex
  `config.toml` 中通过 `memories.consolidation_model` 覆盖。
- `gpt-5.4-mini` 默认用于从历史 Thread 提取记忆，可通过
  `memories.extract_model` 覆盖。
- `codex-auto-review` 默认用于自动审批审查，包括命令执行批准。可通过当前模型在
  Codex 模型目录中的 `auto_review_model_override` 字段覆盖；它是模型元数据，
  不是 `config.toml` 顶层配置项。

例如，可在 Codex `config.toml` 中覆盖记忆模型：

```toml
[memories]
consolidation_model = "your-consolidation-model"
extract_model = "your-extraction-model"
```

### 为未固定版本的模型启用 v2 Collaboration

`gpt-5.6-sol` 和 `gpt-5.6-terra` 已通过内置模型元数据选择 v2
`collaboration`。`gpt-5.6-luna` 明确选择旧版 v1，因此下面的 Feature 配置
不会把 Luna 升级为 v2。对于模型目录中没有指定 multi-agent 版本的内置模型，
例如 `gpt-5.5`、`gpt-5.4`、`gpt-5.4-mini` 和 `gpt-5.2`，可在 Codex
`config.toml` 中启用 v2：

```toml
[features]
multi_agent_v2 = true
```

修改后请新建 Codex 任务。已有任务会保留首次选择的 multi-agent 版本，因此在
同一任务中切换模型或修改 Feature，不一定会替换当前工具表。稳定版
`multi_agent` Feature（也接受旧名称 `collab`）会在未启用 v2 时选择旧版 v1
工具；新配置在目标模型能够使用 `collaboration` Namespace 时应优先启用
`multi_agent_v2`。

## 完整文档

- [中文用户文档](docs/zh-cn/README.md)
- [网关安全与认证](docs/zh-cn/gateway-security.md)
- [开发者文档（英文）](docs/dev/README.md)

## 解决的问题

在Codex中使用第三方大模型时通常会遇到以下问题：
- 服务商只提供Chat Completions API
- 模型不会用apply_patch改文件，导致只能用sed, python等工具执行修改
- 内置功能如Goal, subagent等工作异常
- 不主动调用Plugin
- 少数模型没有多模态图像理解
- Computer use, Browser use异常
- 模型推理深度无法匹配
本项目会逐步解决，让Deepseek V4 Pro, GLM-5.x, Qwen3.7等优秀模型在Codex中能像GPT-5.5那样丝滑，享受低价的同时使用Codex的高级Agent能力。

目前已解决（尚未大规模投入生产）：
- Responses API转换：[Oaklight/llm-rosetta](https://github.com/Oaklight/llm-rosetta)提供项目基底、基础协议转换以及简易WebUI。
- 代码编辑工具翻译：由于这些模型基本都会推荐Claude Code作为首选Coding Agent，本项目参考了Claude Code的工具定义，让模型生成它们熟悉的CC工具调用，Rosetta再转换为apply_patch为Codex提供原生工具调用体验。
- 输入缓存能力保留：由于中途拦截了工具，服务商那里的缓存和Codex本地session记录的不一致，Rosetta会自动在请求里进行工具替换，确保输入缓存命中。
- Goal, TODO, Plan, Subagent已测试正常。
- 工作过程折叠（代价是失去流式传输，可自由开关）。
- 模型推理深度映射


## 支持的提供商

- 下游客户端生成请求应调用 `/v1/responses`。`/v1/chat/completions`、`/v1/messages` 和 Google GenAI 生成端点不再作为客户端生成路由暴露。
- DeepSeek、Opencode Go等提供OpenAI Chat Completions上游接口的服务。Rosetta会做翻译和工具层翻译。
- OpenAI、API 中转站等提供OpenAI Responses上游接口的服务。Rosetta会直接透传请求，不做任何解码和重封装。
- Anthropic Messages 和 Google GenAI 上游 Provider 仍然通过转换管线保留。

## 引用
[![LLM-Rosetta: A Hub-and-Spoke Intermediate Representation for Cross-Provider LLM API Translation (arXiv)](https://img.shields.io/badge/arXiv-2604.09360-b31b1b.svg)](https://arxiv.org/abs/2604.09360)

## 贡献

欢迎贡献！请访问 [GitHub 仓库](https://github.com/iBobbyTS/codex-rosetta) 开始参与。

## 许可证

本项目沿用 MIT 许可证——详见 [LICENSE](LICENSE) 文件。
