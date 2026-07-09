# Codex-Rosetta

[![PyPI version](https://img.shields.io/pypi/v/llm-rosetta?color=green)](https://pypi.org/project/llm-rosetta/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

[English Version](README_en.md) | [中文版](README_zh.md)

**Codex-Rosetta** — 一个基于LLM-Rosetta的大模型网关，致力于将第三方大模型API服务接入Codex并优化工具调用、Plugin。

## Fork 定位

本项目 fork 自 [Oaklight/llm-rosetta](https://github.com/Oaklight/llm-rosetta)。这个 fork 聚焦于将 Chat Completions 兼容接口转换到 Responses API，并适配工具调用语义，让开源模型更好地适配 Codex，同时也聚焦于多 Provider 的网关聚合。面向 agent 的生成接口只暴露 OpenAI Responses；Chat Completions、Anthropic Messages 和 Google GenAI 格式只作为上游目标格式保留，不再作为下游客户端接口。

## 安装

从 PyPI 安装最新预发布版本：

```bash
python -m pip install -U --pre "llm-rosetta"
```

启动本地网关：

```bash
llm-rosetta-gateway --host 127.0.0.1 -v
```

## 完整文档

暂无

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
