---
type: integration-guide
title: 聊天平台连接器
description: 说明 Feishu/Telegram/Slack 入站连接器：适配器接口（验签/归一化/发送）、去重与每会话串行队列管线、线程映射、凭据掩码与 webhook URL 派生。
tags: [connectors, feishu, telegram, slack, webhook, dedup, queue]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-d63887ef5316c9268a327d9e
    resource: repo://src/a2a_gateway/connectors/feishu.py
  - id: openwiki-source-d165432686ca5f4c2a6f65ba
    resource: repo://src/a2a_gateway/connectors/pipeline.py
  - id: openwiki-source-4aadfd8b6e41a836711f114e
    resource: repo://src/a2a_gateway/connectors/slack.py
  - id: openwiki-source-0340879b564de49693aa0bc7
    resource: repo://src/a2a_gateway/connectors/telegram.py
  - id: openwiki-source-1b02fe6de5a4ab57b8b89deb
    resource: repo://src/a2a_gateway/routes/connectors.py
  - id: openwiki-source-c906c556d0d86b9ccfe8ed8b
    resource: repo://src/a2a_gateway/schemas.py
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# 聊天平台连接器

入站连接器把 Feishu / Telegram / Slack 消息桥接到网关 Agent。分层：

- **适配器接口** `connectors/base.py`：验签 / 归一化 / 握手 / 发送，平台差异全部封装在实现内；
- **管线** `connectors/pipeline.py`：去重 → 会话级串行队列 → 图调用 → 回复推送；
- **路由** `routes/connectors.py`：管理端 CRUD（JWT）+ 平台 webhook（无 admin 鉴权，安全依赖平台验签）。

## 适配器接口（PlatformAdapter）

三个抽象方法（`base.py:56-83`），均为 async：

| 方法 | 职责 |
|---|---|
| `build_challenge` | 接入握手应答（`{"challenge": ...}`）；非握手返回 `None`。Slack 的 `url_verification` **先验签**，失败同样抛 `VerifyError` |
| `verify_and_parse` | 验签 + 解析为 `list[InboundMessage]`；失败抛 `VerifyError`（webhook 路由捕获后 401）。**仅产出通过过滤的消息**：私聊文本直收；群聊仅收 @机器人 文本；bot 自身 / 其它 bot / 非文本一律忽略 |
| `send` | 发送文本，超长自动 `chunk_text` 分段；失败抛异常由调用方告警 |

`InboundMessage`（`base.py:16-26`）归一化字段：`platform/chat_id/chat_type(private|group)/user_id/user_name/text/event_id`（`event_id` 即去重键）。工具函数：`normalize_headers`（键小写化，签名头查找不依赖 Mapping 类型）、`chunk_text`（优先换行断开，`<50% limit` 时硬切）。

## 各平台验签差异

| 平台 | 验签方式 | 握手 | 关键凭据 |
|---|---|---|---|
| **Feishu** | 事件体 `verification_token` 比对；配了 `encrypt_key` 则先 AES-256-CBC 解密（key=sha256(encrypt_key)，IV=密文前 16 字节，PKCS7）；**不依赖请求头**（`feishu.py:32-69`、`126-130`） | `type=url_verification` → 回 challenge | `app_id/app_secret/verification_token/encrypt_key` |
| **Telegram** | `X-Telegram-Bot-API-Secret-Token` 头 `hmac.compare_digest` 常量时间比较；secret 未配置或不匹配即 401（`telegram.py:44-52`） | **无握手**（`setWebhook` 注册，`build_challenge` 恒 `None`，`telegram.py:39-42`） | `bot_token/secret_token/bot_username` |
| **Slack** | Events API HMAC：`v0=HMAC-SHA256(secret, "v0:{ts}:{body}")`，`X-Slack-Request-Timestamp` 与当前时间偏移 > 300s 拒绝（防重放），`hmac.compare_digest` 比对（`slack.py:19-45`） | `url_verification`（**先验签再回** challenge，`slack.py:51-58`） | `signing_secret/bot_token` |

群聊 @ 过滤差异：Feishu 经 `GET /bot/v3/info` 取机器人 `open_id`（进程内缓存）比对 mentions；Telegram 扫 `entities` 中 `mention` 片段匹配 `@bot_username`（大小写不敏感）；Slack 只收 `app_mention` 事件并去掉 `<@Uxxx>` 占位。Feishu 的 `tenant_access_token` 与 `bot_open_id` 均进程内缓存、过期前 60s 刷新（`feishu.py:26-29`、`107-124`）。发送分段上限：Feishu/Telegram 4000 字符、Slack 39000。

## Webhook 管线（立即 200 + 后台处理）

`platform_webhook`（`connectors.py:244-287`）顺序：

1. 平台适配器不存在 → 404；连接器不存在 / platform 不匹配 / 未启用 → **统一 404**（停用与不存在不暴露差异）；
2. `build_challenge`：`VerifyError` → 401；返回非 `None` → **立即回 challenge**（不进管线）；
3. `verify_and_parse`：`VerifyError` → 401；
4. 构造 `ConnectorRef`（后台任务最小引用，避免跨请求持有 ORM 实例，`pipeline.py:38-47`），逐条：
   - **去重键** `{platform}:{connector_id}:{event_id}` → `seen_recently` 命中则跳过；
   - `enqueue_message` 失败（队满）→ 后台 `asyncio.create_task(_busy_reply)` 回「消息较多，请稍后再试」；
5. **立即返回 `{"ok": true}`**——平台要求 ~3s 内确认而 Agent 调用可能数十秒，处理在管线后台（`pipeline.py:1-8`）。

### 进程内 LRU 去重

`seen_recently`（`pipeline.py:53-67`）：`OrderedDict` TTL LRU，容量 `_DEDUP_CAPACITY=4096`、TTL `_DEDUP_TTL_SECONDS=600`（覆盖平台超时重试）。登记时先清过期项（`time.monotonic`），命中 `move_to_end` 并返回 `True`，超容 `popitem(last=False)` 淘汰最旧。进程内状态：多副本部署需注意各副本独立（见 [可观测性](/openwiki/operations/failure-and-observability.md)）。

### 每会话串行队列

- 队列键 `(connector_id, chat_id)`，`asyncio.Queue(maxsize=_QUEUE_LIMIT=5)`，懒创建并 `_ensure_worker`（`pipeline.py:79-93`）；
- `enqueue_message` `put_nowait`，`QueueFull` 返回 `False` 并 `logger.warning`（`pipeline.py:95-103`）——**队满丢弃新消息**，由调用方回忙提示；
- worker 单循环消费，`asyncio.wait_for(_process, timeout=_PROCESS_TIMEOUT_SECONDS=120)`（`pipeline.py:106-125`）：
  - `TimeoutError` → 回 `REPLY_FAILURE`（`logger.error`）；
  - 其它异常 → 回 `REPLY_FAILURE` + `notify_alert` 告警；
  - `finally: queue.task_done()`。

串行保序：同一 `(connector, chat)` 上下文不交叉（`pipeline.py:5`）。常量汇总（`pipeline.py:28-35`）：4096 / 600s / 队长 5 / 120s；固定文案 `REPLY_UNPUBLISHED`、`REPLY_BUSY`、`REPLY_FAILURE`。

## 单条处理与线程映射

`_process`（`pipeline.py:128-157`）：

1. `upsert_connector_conversation` → 会话映射，取 `conversation.thread_id`；
2. Agent 不存在或非 `PUBLISHED` → 回 `REPLY_UNPUBLISHED`；
3. 群聊消息前缀 `[user_name]: `（区分群内发言人），私聊原文；
4. `get_agent_instance(agent).ainvoke(..., config={"configurable": {"thread_id": conversation.thread_id}})`——**连接器会话 thread_id 进 LangGraph Checkpointer**，与网页聊天同一持久化机制；
5. `_extract_reply` 取最后一条 AI 消息（兼容 `str` 与内容块列表），非空则 `adapter.send` 推回原 `chat_id`。

`_safe_reply` 尽力回复兜底文案，失败只记日志、绝不影响主流程（`pipeline.py:177-183`）。会话表与 thread 语义见 [身份与会话](/openwiki/concepts/identity-and-conversations.md)。

## 管理端：凭据掩码与 webhook URL 派生

- **webhook URL**：`_webhook_url` = `{base}/api/connectors/{platform}/{id}/webhook`；基址 `_connector_base_url` 优先 `PUBLIC_BASE_URL` 环境配置，未配置回退 `public_base_url(request)` 入口自适应（`connectors.py:53-60`）——与 Agent Card 公网 URL 同源（见 [A2A 服务端](/openwiki/integrations/a2a-server.md)）。
- **凭据掩码** `mask_connector_credentials`（`schemas.py:519-522`）：已配置字段 → `"••••"`，空字段 → `""`，**仅提示配置状态、不含明文**。
- **凭据合并** `merge_connector_credentials`（`schemas.py:509-516`）：更新时新值为空的字段保留原值——**回显脱敏值、留空 = 不修改**，防止掩码串覆盖真凭据。
- Telegram 创建时若无 `secret_token` 自动生成 `secrets.token_urlsafe(32)`（`connectors.py:139-140`）；启用/更新时 `_maybe_register_telegram` 自动 `setWebhook` + `getMe` 补全 `bot_username`，**失败只返回 warning、不阻断保存**（`telegram.py:93-126`、`connectors.py:96-107`）。
- 主动推送 `POST /{id}/send`：启用校验；`chat_id` 缺省取最近一条会话（无会话 404）（`connectors.py:210-231`）。

## 不变量

- Webhook 路径无 admin 鉴权，安全=验签（`VerifyError`→401）→ 连接器定位（404 不区分停用/不存在）→ LRU 去重 → 串行队列。
- 平台 ~3s 确认约束：路由恒立即 200/`{"ok":true}` 或 challenge，重活全在后台 worker。
- 同一会话严格串行（保序），队满只丢新消息并回忙提示；单条 120s 超时兜底。
- 凭据对外只出掩码视图；更新留空字段保留原值。
- 处理失败/超时/队满均尽力回复用户文案，告警走 `notify_alert`，绝不打断 webhook 应答。

## 代表性测试

- `tests/test_connectors_pipeline.py`：去重 TTL/容量、队满、超时、未发布、回复提取。
- `tests/test_connector_routes.py`：webhook 验签 401、404 不区分、立即 200、掩码与合并。
- `tests/test_connector_adapters.py`（或分平台文件）：Feishu 解密/token、Slack 签名与 300s 偏移、Telegram secret 头与 mention 过滤、`register_webhook` 警告路径。

相关页：[聊天生命周期](/openwiki/workflows/chat-lifecycle.md)、[身份与会话](/openwiki/concepts/identity-and-conversations.md)、[认证与安全面](/openwiki/concepts/security.md)、[失败与可观测性](/openwiki/operations/failure-and-observability.md)。
