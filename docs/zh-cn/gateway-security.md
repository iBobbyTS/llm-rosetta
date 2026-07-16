# 网关安全与认证

## 远程压缩留存

Rosetta Remote Compaction V2 会在 gateway SQLite 数据库中保存明文交接替换文本七天。
返回的 `encrypted_content` 是 `rskc_v1_` opaque handle，并非密文；数据库只保存其
SHA-256 和明文。映射按已鉴权 principal 隔离，清理时过期，重放时续期。缺失、过期或
跨 principal 的 handle 会静默丢弃。该逻辑 TTL 不会抹除 rollout 文件、显式开启的 raw
trace、测试 artifact、备份或 SQLite 已释放页中的历史字节。

在 Responses 到 Responses 的同协议路由上，`context_limit` 和 `user_requested`
compaction 仍原样交给上游。模型切换压缩（包括 `comp_hash_changed` 与
`model_downshift`）则使用旧模型执行 Rosetta 无工具摘要请求。Rosetta 将明文替换内容
保存七天并返回 opaque `rskc_v1_` handle，使下一个 Provider 收到可重放的明文，而不是
另一个 Provider 的加密 compaction payload。从 Responses 转换到其他协议的路由也继续
使用 Rosetta coordinator。

Codex-Rosetta 默认关闭未授权访问：每份网关配置都必须包含非空的 Admin 密码，
并至少包含一个网关访问密钥。默认监听地址为 `127.0.0.1`，API 凭证显示功能默认关闭。

## 初始化本地网关

```bash
codex-rosetta-gateway init
codex-rosetta-gateway --host 127.0.0.1
```

`init` 会在配置文件中生成高强度随机的 `server.admin_password` 和一条
`server.api_keys` 记录。配置、锁和备份文件均以仅当前用户可读写的权限保存。
向客户端分发前，请先把生成的凭证保存到密码管理器中。

首次以配置文件启动 Gateway 时，还会在 `config.jsonc` 同目录创建仅当前用户可读写的
`admin-session.key`。这个独立随机 secret 只用于派生浏览器 Admin token：普通 Gateway
重启会保留登录状态，修改 Admin 密码或删除该 secret 则会使现有浏览器会话失效。
Admin 模型测试使用的内部 Bearer token 仍然只存在于内存中，并在每次进程启动时轮换。
未提供配置路径的程序化 `create_app()` 调用会有意使用临时 Admin session secret，因此不
承诺在不同 app 实例之间保留登录状态。

所有 `/v1` 请求都使用网关访问密钥，而不是上游 Provider 密钥。认证先于路由执行，
因此未知、已移除和动态注册的 `/v1` 路径也会 fail closed。浏览器 `OPTIONS` 预检仍可
公开访问，但之后的实际请求仍必须通过认证：

```http
Authorization: Bearer rsk-...
```

入站解析还执行固定的进程级资源限制。request line 必须在单个 5 秒 monotonic deadline
内完成；每个 header 或 chunked trailer section 必须在 10 秒内完成，且最多包含 100 个
字段或 64 KiB（包含 framing）；完整 request body 必须在单个 30 秒 monotonic deadline
内到达。同一时间最多允许 64 条连接占用 request parser；第 65 条连接会立即收到 HTTP
503，不会排队等待容量。

对于受保护的 `/v1` 和 Admin API 请求，网关会在有界 headers 之后、读取 body bytes
之前检查凭证，因此无效凭证不会导致网关缓冲声明的大 body。有效请求继续执行
默认 128 MiB 的 body 上限。Admin 的“服务器设置”页面可以在运行时把
`server.request_body_limit_mb` 切换为 `64`、`128`、`256`、`512`、`1024` 或
`"unlimited"`，重新加载配置也会在不重启网关的情况下应用同一设置。公开的 Admin
login/auth-check endpoint 和浏览器 `OPTIONS` 预检仍按设计保持无需认证；即使这些请求
携带 body，也仍受同一 body deadline、已配置大小上限和 parser capacity 约束。
经过认证且带有 `Content-Encoding: zstd` 的 `/v1` 请求会在解析 JSON 前自动解压。
网关使用 WebUI 当前配置的同一个大小上限，分别检查压缩后的 wire body 和解压后的
body；没有该编码的请求继续使用原有的未压缩路径。
“不限制”会移除 Rosetta 实际可触发的 body 大小上限，但每个 body 仍会完整缓冲到内存，
因此只应在可信且内存受控的部署中使用。

每条访问密钥记录都必须有稳定且唯一的 `id`。Rosetta 使用该 ID 作为认证主体，
隔离跨轮状态；修改显示标签不会改变身份。Admin UI 会拒绝删除最后一条访问密钥。

Gateway model 标识符最多为 256 UTF-8 bytes。Codex 的 `x-codex-window-id` header
最多为 128 UTF-8 bytes；当前 Codex window ID 使用 `{UUID}:{window_number}`，通常约
40 bytes。超过任一语义上限的请求会在 routing 或 state allocation 之前收到对应格式的
HTTP 400。该限制避免 request error 回显和 state-key 内存绕过更大的 body/header 及
cache-value byte budget。
外部 `x-request-id` 仅作为 correlation metadata，必须由 1–128 个 visible ASCII bytes
（`!` 到 `~`）组成；缺失时由 Gateway 生成 UUID。空值、包含 control character、非 ASCII
或超长的 ID 会在 body parsing、logging、tracing、persistence、state allocation 和
upstream forwarding 之前收到对应格式的 HTTP 400，避免 terminal-control 注入以及 trace
中重复 metadata 放大诊断存储。

跨轮内存状态执行按 principal 公平的硬限制。Provider continuation metadata 每条上限
1 MiB、每个 scope 上限 8 MiB、每个 principal 上限 1,024 条/16 MiB，整个 app 上限
10,000 条/64 MiB。延迟工具发现状态每个 scope 上限 1,024 个嵌套 tool/16 MiB；loaded
与 deferred 两张 map 合并后，每个 principal 最多 256 个唯一 scope；每张 map 最多保留
1,000 个 scope，整个 app 上限 64 MiB。同一 scope 同时出现在两张 tool map 时只计一次
principal quota。达到 principal 上限会直接拒绝新状态；全局 count map 满时，只能替换
当前写入 principal 自己最旧的 entry 或 scope，绝不会驱逐其他 principal 的状态。
容量失败会在 cache 部分变更前以 HTTP 413 返回。

## 使用环境变量的示例配置

仓库中的示例配置要求设置以下环境变量：

```bash
export CODEX_ROSETTA_ADMIN_PASSWORD='replace-with-a-strong-secret'
export CODEX_ROSETTA_API_KEY='rsk-replace-with-a-strong-secret'
```

任一值为空或环境变量仍未解析时，网关都会拒绝启动。Provider API 密钥与网关密钥
相互独立，继续使用各 Provider 对应的环境变量。

## Docker 与远程访问

容器监听 `0.0.0.0`，以便 Docker 发布网关端口；这不会放宽认证要求。请保留生成的
Admin 密码和网关访问密钥，通过宿主机或网络防火墙限制发布端口，并为所有非回环
部署配置前置 TLS。`server.credential_visible` 只控制 Admin UI/API 是否显示原始的
Gateway/Provider API credential；除非确实需要在可信 Admin 会话中显示这些值，否则不要
启用。它不会遮盖 `server.proxy` 或 Provider `proxy` URL 中的 userinfo。此类连接 URL 对
已认证 Admin 仍然可见，因此应尽量避免把 proxy password 写入 URL，并严格保护 Admin
访问权限。

仓库不会发布 Docker 镜像。请在仓库根目录运行 `make compose-up`；该命令会重新构建
当前 checkout 的 wheel，并把这个精确 wheel 交给版本化 Compose 配置进行构建。若直接
运行 Compose，也必须显式提供 `LOCAL_WHEEL`，不能再依赖旧的 registry 镜像名。

当 Gateway 直接运行在宿主机时，启用浏览器版 `web.run` 最简单的方式是：

```bash
codex-rosetta-gateway --with-web-run
```

这个显式参数要求本机同时提供 Docker 和 `docker-compose`。CLI 会构建安装包内的
sidecar 上下文，创建隔离的 Compose project，并且只绑定 `127.0.0.1`。端口从候选值
`8766` 开始选择；遇到已占用端口或启动时的端口竞争会自动顺延。自动生成的 Bearer
Token 和选定 URL 只在当前进程中覆盖 `server.web_run`，Admin 热重载后仍然有效，退出
时恢复原环境。若 service 或 Chromium 未能就绪，Gateway 会 fail-closed，不会继续启动；
正常退出或 `Ctrl-C` 只删除本次调用托管的 Compose project。

当 Gateway 本身也运行在 Compose 中时，浏览器版 `web.run` 仍是可选 profile。提供
一个不少于 24 个字符的独立随机 Bearer Token，即可与网关一起启动：

```bash
CODEX_ROSETTA_WEB_RUN_TOKEN='<random-sidecar-token>' make compose-up-web-run
```

Make target 只是快捷封装。若要直接使用 Docker Compose，请先构建当前 checkout 的
wheel，导出 Compose 文件使用的变量，再启用 `web-run` profile：

```bash
python -m build --wheel
export LOCAL_WHEEL="$(basename "$(ls -t dist/*.whl | head -n 1)")"
export CODEX_ROSETTA_WEB_RUN_URL='http://web-run:8080'
export CODEX_ROSETTA_WEB_RUN_TOKEN='<random-sidecar-token>'

docker-compose -f docker/docker-compose.yaml \
  --profile web-run up --build -d
```

`LOCAL_WHEEL` 必须是仓库 `dist/` 目录下的 wheel 文件名。Gateway Dockerfile 会明确
安装这个 wheel，确保容器运行的就是当前本地 checkout。Gateway 和 sidecar 必须使用
同一个 Token，Compose 网络地址应保持为 `http://web-run:8080`。

日常查看日志、重启和只停止 sidecar 应使用 Compose service 名，并复用启动时的环境变量
和 profile：

```bash
docker-compose -f docker/docker-compose.yaml --profile web-run logs -f web-run
docker-compose -f docker/docker-compose.yaml --profile web-run restart web-run
docker-compose -f docker/docker-compose.yaml --profile web-run stop web-run
```

停止并删除完整 Compose stack 时，复用上述已导出的变量和 profile：

```bash
docker-compose -f docker/docker-compose.yaml \
  --profile web-run down
```

该命令会构建独立的 `web-run` service；Compose 自行分配 project-scoped 容器名，且不会
向宿主机发布端口。网关通过私有 Compose 网络访问，并收到
`CODEX_ROSETTA_WEB_RUN_URL=http://web-run:8080`。sidecar 不会挂载网关配置目录，也不会
收到 Provider credential。它的 Bearer Token 会在 Admin 配置 API 和 Gateway Logs 中
被遮盖。若既不使用 Compose，也不使用 CLI 托管参数，需要显式配置相互匹配的
`server.web_run.base_url`、`server.web_run.token`（或对应的 URL/Token 环境变量）。

Admin **联网搜索**页面允许基础搜索选择 Tavily 凭据，或现有 sidecar 内的
**Self-hosted (Google)**、**Self-hosted (Bing RSS)** 与
**Self-hosted (Bing Browser)**。self-hosted Provider 不会发送搜索 API 凭据，但搜索引擎可能限流、要求验证
或改变结果页；这类失败会作为有界的 `502` 搜索错误返回，不会静默切换 Provider。高级 Section
只读，并分别显示 sidecar 服务在线状态和浏览器就绪状态。状态端点以两秒超时、
有界响应访问 sidecar 的公共 `/health` 路由，不返回 sidecar URL、Bearer Token
或上游错误正文。页面进入后立即检查，仅在页面停留期间每五秒刷新，离开后停止。
模型请求复用同一个五秒健康缓存；Modified `web.run` 只有在缓存状态在线且
`browser_ready=true` 时才声明浏览器命令。并发刷新会合并，配置热重载会使缓存失效。

Self-hosted Bing RSS 读取 Bing 的 XML 结果表示；Self-hosted Bing Browser
则在 sidecar 的 Patchright 浏览器中加载交互式 HTML 结果页。两者可独立选择，
不会静默相互回退，并保持相同的结果数量与 domain 边界。运维方仍需确保使用方式
符合各搜索引擎的适用条款。

自托管搜索使用短生命周期、相互隔离的 browser context，并发上限为两个。搜索结果
URL、标题和摘要在返回网关前会被限制长度并规范化；domain filter 同时应用于搜索引擎
查询和返回结果 hostname。

容器固定使用 Patchright 及其 Chromium build，不再安装 Playwright runtime，也不再
使用 Playwright 基础镜像。Chromium 以 headful 模式运行在私有 Xvfb display 内，
browser context 不覆盖浏览器 User-Agent。此处 Patchright 仍仅使用 Chromium；Google
仍可能拒绝数据中心出口 IP，这种情况会返回有界错误，不会尝试绕过。

sidecar 以镜像内的非特权 `pwuser` 运行 Chromium，使用 Chromium user-namespace sandbox
所需的固定 Chromium seccomp profile 和只读根文件系统，并只保存有界的临时
浏览器/PDF 状态。每个 Codex Search request ID 都映射到独立 browser context；
context 会在 15 分钟后过期，最多保留 16 个页面/PDF 引用和 40 MiB PDF 数据；容器
本身也有明确的内存和进程数限制。导航、子资源、重定向和 PDF 下载都限制为公开
HTTP(S) 地址。这是一层防护边界，并不代表任意网页内容都可信：
不要把 sidecar 端口对外发布，使用独立 Token；如果部署策略需要更严格的网站 allowlist，
还应配置出站网络控制。

Admin 登录限流按直连 peer 地址统计。由于网关尚未提供可信代理 allowlist，客户端
IP 转发头会被忽略；因此反向代理后的请求会共享一个限流桶，建议同时在反向代理上
配置限流作为额外防护。Request log 的客户端归属采用同一规则，只记录 TCP 直连
peer，不把 `X-Forwarded-For` 或 `X-Real-IP` 当作可信来源。

每个 `create_app()` 实例都独立持有 Admin 登录限流器和 model-test task registry。
登录失败、task ID、容量、取消、过期清理和 shutdown 因此都只影响当前 app。通过另一
个 app 查询或取消 task ID 时，会与未知 ID 一样返回 HTTP 404，不会泄露该 ID 是否由
其他 app 持有。

每个 app 最多同时运行 4 个 Admin model test，并最多保留 128 条 task record。self-call
的成功和错误 response body 都使用独立的 4 MiB 增量读取上限；超限会在完整 JSON decode
前拒绝，并记录为稳定的 502 类诊断，不保留 partial body。完成结果在 poll 前始终以紧凑
JSON bytes 保存。每条 retained record（包含 metadata）上限为 4 MiB，每个 app 的全部
completed record 共用 32 MiB 预算。容量收敛只会驱逐当前 app 最旧的 completed result，
绝不会驱逐 active work。Running task 会计入 128 条数量上限，但不计入 completed-byte
预算。App shutdown 会取消并等待自身 active test，同时清空自身 completed result。

## 出站网络与响应上限

请求转换到 Google GenAI 时，公共 HTTP(S) 图片 URL 的下载使用一个 30 秒 monotonic
deadline，统一覆盖 DNS、连接、重定向、响应头和请求体读取。每个重定向目标以及直连
DNS 返回的所有地址都会重新验证；私有或不可路由地址会被拒绝，最多跟随三次重定向，
每个图片响应体上限为 10 MiB。网关为每个 app 单独持有一个四 worker 的有界池；排队、
请求取消和 shutdown 都不会在底层 worker 真正退出前提前释放容量。

网关对上游 HTTP 请求强制发送 `Accept-Encoding: identity`。如果响应仍声明 gzip、
deflate、Brotli 或其他 Content-Encoding，网关会关闭并拒绝该响应，而不会解压。这样可
观测的 wire payload bytes 与 decoded payload bytes 相同；HTTP chunk framing 不计入
payload。非流式成功响应体上限为 50,000,000 bytes，非流式错误响应体和流式 HTTP 错误
响应体上限均为 1,000,000 bytes。`Content-Length` 会在读取前检查，chunked 或未知长度
响应则逐块累计。peer 声明的 HTTP chunk 会按固定有界 payload 子块读取（Gateway 响应体
读取中每块最多 64 KiB），不会先按对端声明的 chunk size 整体物化，再检查 Gateway
budget。超限或非 identity 响应会被关闭，并作为稳定的 gateway upstream error 返回。

成功 SSE stream 继续保持增量转发，不设置整个 stream 的累计大小或持续时间上限；但
每条 SSE line 上限为 1 MiB，每个 event 累积的 `data:` payload 上限为 8 MiB，并在每个
delimiter 后重置 event 计数。转换后的 SSE 和保留原始字节的 Responses passthrough 都
执行同一限制；超限时会关闭 upstream，并返回稳定的 Gateway safety error。

转换型 Provider stream 接受 JSON `data:` event、显式 `[DONE]` marker、空 `data:`
keepalive 和普通 SSE comment。若非空 event 既不是 JSON 也不是 `[DONE]`，Rosetta 会把它
视为 upstream protocol failure：只关闭一次底层响应，并以稳定的 502 类错误终止转换流。
malformed event 正文绝不会进入 client-visible error 或普通/body logs。同协议 Responses
streaming 继续执行 byte-preserving passthrough；它只应用上述 wire-size limit，不解析
Provider event JSON。

## 诊断数据保留

错误诊断可能包含 prompt、源码和工具 payload。Rosetta 只脱敏已配置的 Gateway/
Provider API token、Bearer/Authorization token、明确的 token/API key 字段，以及与已配置
API token 值匹配的内容。其他 request、converted body、response、prompt、password、
secret、client secret、proxy password 和个人数据都会保留，因此应严格限制对数据目录的
访问权限。

实时 upstream error log 即使在关闭 request-body logging 时，也会使用当前 app/config
的 token 集合。错误文本会先做 token-only 脱敏，再把控制字符和换行分隔符转义到单行，
最终值最多保留 4,096 个字符。该边界会保留 prompt、个人数据以及普通 `password`、
`secret` 和 `client_secret` 值；它不是通用隐私清洗器。

Request/response body logging 是独立的 opt-in，由 `debug.log_bodies` 或
`CODEX_ROSETTA_LOG_BODIES` 控制。它使用专用的 DEBUG logger
`codex-rosetta-gateway.body`：启用 body logging 不会同时打开其他 Gateway DEBUG 噪音。
每个 app 独立持有实时 body-log policy 和 token 集合，Admin config hot reload 后也保持这
一隔离。Original、intermediate、converted 与 upstream body 会先对完整结构递归执行
token-only 脱敏，再做 JSON 序列化，随后转义为单行并限制为 20,000 characters。若序列
化失败，只记录固定占位文本，绝不会 fallback 到原始对象或 exception text。

Body log 会保留 prompt、源码、个人数据，以及普通 `password`、`secret`、
`client_secret` 和 proxy-password 值，因此必须严格限制 console/file log 的访问权限。
已配置的 Gateway/Provider token exact value、Bearer/Authorization 值、明确的 token/API
key 字段，以及 JSON encoded function arguments 内的这些字段都会被脱敏。

Request log 的 success/error 上限会在启动和 Admin 热更新时使用同一规则验证。
`server.request_log.success_max`、`error_max`、旧版 `max_entries`，以及环境变量
`REQUEST_LOG_SUCCESS_MAX` / `REQUEST_LOG_ERROR_MAX` 都必须是 0 到 1,000,000 之间的
整数；bool、负数和更大的值都会被拒绝。上限为 `0` 表示不保留该类 request row，配置
激活后会立即收敛。Request log 上限不会改变 error dump 独立的 10,000 条保留合同。

每个原始或转换后请求体在写入前都有 10 MiB 上限。错误诊断只沿用既有的 10,000 条
数量上限，不会按天数或总大小自动删除。数量清理和手动清空错误诊断时，都会删除不再
被引用的请求体 blob。

## 可执行工具历史存储

启用代码工具本地化后，原生/本地化调用映射属于可执行重放状态，而不是诊断数据。
Rosetta 使用 AES-256-GCM 把精确映射写入 SQLite；每行使用独立 nonce，并把完整 scope
作为认证数据。SQLite payload 列保存的是 ciphertext，而不是有损的 `[REDACTED]`
projection。Request log、stream trace、error dump、API 和 Admin UI 仍是独立的诊断
界面，继续执行上文的 token-only 脱敏规则。

默认情况下，首次持久化映射会在 `gateway.db` 同目录原子创建
`data/tool-mapping.key`。数据目录权限为 `0700`，key 文件权限为 `0600`；多个 gateway
并发启动时只会采用同一个已完整写入的 key。部署也可以通过持久化 secret manager
提供 `CODEX_ROSETTA_TOOL_MAPPING_KEY`，其值必须是一个 base64 编码的 32-byte key。
环境变量值不会写入 SQLite，也不会出现在错误信息中。

备份时必须把数据库和 key 当成一个整体。停止写入或使用一致的 SQLite backup，并把
`gateway.db` 与 `tool-mapping.key` 一起备份；若使用环境变量 override，则通过对应的
secret manager 备份外部 secret。恢复时两者必须来自同一代备份。目前没有实现 key
rotation；只要仍有加密行，就不要替换任一 key source。

若存在加密行但 key 缺失、格式损坏、不匹配，或任一行认证失败，gateway 会在启动时
fail closed，不会重新生成 key，也不会重放有损历史。旧 plaintext 或 `[REDACTED]`
映射不可恢复；schema migration 会发出 warning，并且只清除这些旧 mapping row。
已过期或当前请求不再使用的加密映射继续沿用已配置的 TTL 清理语义。

加密 mapping 存储还执行固定硬预算。ciphertext 加 ownership metadata 每行最多
16 MiB；每个 session 最多 2,048 行/64 MiB，每个 principal 最多 8,192 行/256 MiB，
整个数据库最多 32,768 行/512 MiB。Upsert 的过期清理、replacement-aware 行数/字节
accounting、预算校验和最终写入都在同一个 `BEGIN IMMEDIATE` transaction 中完成，
因此 replacement 被拒绝或写入失败时会保留旧 mapping。启动时先校验 accounting 和
全部分层预算，再解密行；session replay 也会在加载 ciphertext 前执行同样的
accounting 检查。已有 encrypted-v1 表会无损增加并回填 `mapping_bytes` 列，不需要
解密或删除有效历史。容量超限或 accounting 不一致均 fail closed。
