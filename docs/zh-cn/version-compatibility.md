# Codex CLI 版本兼容性

Codex-Rosetta 的版本号跟随其兼容的 Codex CLI 版本，并附加 Rosetta 自身的补丁
后缀。发布版本格式为 `{codex_version}.r{patch_number}`：Codex 版本表示兼容的
Codex CLI 发布版本，`rN` 表示该 Codex 版本下的 Rosetta 补丁序号。每次采用新的
Codex 版本时从 `r0` 开始；只包含 Rosetta 修复的后续发布递增 `rN`。

例如，`0.144.0.r0` 是兼容 Codex CLI `0.144.0` 的第一个 Codex-Rosetta 发布。
源码保留 `rN` 写法；Python 包元数据会将其规范化为等价的 PEP 440 `.postN` 写法。

## 当前兼容性

Codex-Rosetta `0.144.0.r0` 兼容 Codex CLI `0.144.0`。
