# 聊天连接器管理（Chat Connector）设计规格

- 日期：2026-09-22
- 状态：待实现
- 范围：架构级（新增子系统）

## 1. 背景与目标

让 a2a-gateway 上的 Agent 通过管理后台配置的「连接器」接入第三方聊天应用：用户在聊天应用里发消息，绑定 Agent 处理并回复；同时提供从系统侧主动推送消息的能力（为后续任务通知打基础）。

已确认的需求决策：

| 维度 | 决策 |
|---|---|
| 对接方式 | 公网 HTTPS + Webhook 回调（飞书/Telegram/Slack 三平台均为此模式） |
| 能力范围 | 双向**文本**对话 + 系统侧主动推送；不含图片/文件/卡片/流式 |
| 绑定关系 | 一个连接器绑一个 Agent（1:1） |
| 平台 | 第一版：飞书、Telegram、Slack；Discord/钉钉/企微等后续迭代 |
| 群聊 | 私聊直接回复；群聊仅处理 @机器人 的文本消息 |

## 2. 方案选型

采用「平台适配器抽象层 + 统一 Webhook 网关」（方案 A）：

- `connectors/` 包内每个平台一个适配器，实现验签、入站归一化、出站发送、文本分段四个职责；
- 会话映射、Agent 调用、去重、串行队列、错误告警等平台无关逻辑全局只有一份；
- 新增平台 = 新增一个适配器 + 表单字段，主链路零改动。

不引入各平台 SDK：出入站均为 httpx 直接调用 + 标准库验签（唯一新增显式依赖 `cryptography`，用于飞书加密事件解密，python-jose 已间接引入）。

## 3. 数据模型（`models.py` + Alembic 迁移）

### 3.1 `ChatConnector`（表 `chat_connectors`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `name` | String(128) unique | 展示名 |
| `platform` | Enum `ConnectorPlatform` | `feishu` / `telegram` / `slack`（按成员值建枚举，同 `AgentStatus` 处理） |
| `credentials` | JSONB | 平台凭据，按平台 schema 校验；接口返回时脱敏 |
| `agent_id` | FK → `agent_configs.id` CASCADE | 绑定的 Agent（1:1） |
| `enabled` | bool，默认 True | 停用后 webhook 拒绝处理，凭据保留 |
| `description` | Text | 备注 |

继承 `BaseMixin`（created_at/updated_at）。`credentials` 的结构校验位于 `schemas.py`：按 `platform` 分派的 Pydantic 模型（`FeishuCredentials` / `TelegramCredentials` / `SlackCredentials`）。

凭据结构（`credentials` 内容按 `platform` 校验）：

- feishu：`app_id`、`app_secret`、`verification_token`、`encrypt_key`（可选，未启用加密则空串）
- telegram：`bot_token`、`secret_token`（webhook 验证请求头；创建时留空由后端 `secrets.token_urlsafe(32)` 自动生成）
- slack：`bot_token`（`xoxb-` 开头）、`signing_secret`

### 3.2 `ConnectorConversation`（表 `chat_connector_conversations`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `connector_id` | FK → `chat_connectors.id` CASCADE | |
| `chat_id` | String(128) | 平台会话标识：飞书 `chat_id`/`open_id`、Telegram `chat.id`、Slack `channel_id` |
| `chat_type` | String(16) | `private` / `group` |
| `thread_id` | String(128) | LangGraph 会话 id，确定性生成：`conn-{connector_id}-{platform}-{chat_id}`（超长时对 chat_id 取 sha256 前 24 位） |
| `last_user_ref` | JSONB | `{"user_id": "...", "display_name": "..."}` 最近发言人 |
| `last_active_at` | DateTime(timezone=True) 索引 | 最近活跃时间 |

约束：`UniqueConstraint(connector_id, chat_id)`。

设计要点：

- 消息本体仍由 LangGraph Checkpointer 按 `thread_id` 存放，本表只做目录映射，与现有 `Conversation` 表（网页对话）互不干扰、不做改动；
- 群聊整群共享一个 thread（同一群内上下文连续）；
- 凭据用 JSONB 而非平铺列：三平台字段结构不同，新增平台只扩展校验 schema。

## 4. 适配器层（新包 `src/a2a_gateway/connectors/`）

```
connectors/
  __init__.py
  base.py       # PlatformAdapter 抽象 + InboundMessage
  registry.py   # platform 字符串 → 适配器实例
  feishu.py / telegram.py / slack.py
```

### 4.1 抽象接口

```python
@dataclass
class InboundMessage:
    platform: str
    chat_id: str
    chat_type: str        # private / group
    user_id: str
    user_name: str
    text: str
    event_id: str         # 平台事件/消息 id，去重键

class PlatformAdapter(ABC):
    platform: str

    def verify_and_parse(self, body: bytes, headers, credentials: dict) -> list[InboundMessage]:
        """验签失败抛 VerifyError（→ 401）；仅产出通过过滤的消息。"""

    def build_challenge(self, body: bytes, credentials: dict) -> dict | None:
        """平台接入握手应答体；无握手返回 None。"""

    async def send(self, credentials: dict, chat_id: str, text: str) -> None:
        """出站发送；超长文本内部分段。"""
```

各平台关键实现约定：

| 平台 | 验签 | 握手 | 过滤规则 | 出站 |
|---|---|---|---|---|
| feishu | `verification_token` 比对；配置 `encrypt_key` 时 AES-CBC 解密 | `url_verification` → `{"challenge": ...}` | `im.message.receive_v1`；群聊需含指向机器人的 mention；忽略 bot/应用自身消息 | `tenant_access_token`（进程内缓存、过期前刷新）→ `im/v1/messages?receive_id_type=chat_id`，`msg_type=text` |
| telegram | `X-Telegram-Bot-Api-Secret-Token` 头与 `secret_token` 全等比对 | 无（`setWebhook` 注册） | private 直接收；group 检查 `@bot用户名` mention（含命令）；忽略 `from.is_bot` | `sendMessage`（纯文本，不带 parse_mode） |
| slack | `X-Slack-Signature`：HMAC-SHA256(`v0:{ts}:{body}`) + 时间戳偏移 >5 分钟拒绝 | `url_verification` → `{"challenge": ...}` | `message.im` 收私聊；`app_mention` 收群聊 @；忽略 `bot_id` 为自身/其他 bot | `chat.postMessage` |

出站分段阈值：Telegram 4000 字符、飞书 4000、Slack 39000，按阈值切块依序发送。

## 5. Webhook 主链路（`routes/connectors.py`）

两个 router：

- **管理 router**：prefix `/api/admin/connectors`，`Depends(get_current_admin)`，薄路由 + `repository.py` 函数，风格对齐 `admin.py`；
- **平台回调 router**：prefix `/api/connectors`，**无 admin 鉴权**，`POST /{platform}/{connector_id}/webhook`，安全依赖平台验签。

处理时序：

1. 先调 `adapter.build_challenge`（内部自行解密并校验 token，命中握手事件即返回应答体，路由直接回 200）——Slack 的 `url_verification` 同样带签名，在 `build_challenge` 内一并校验；未命中握手再走 `verify_and_parse`，验签失败 401；
2. 归一化消息按 `event_id` 进程内 TTL 去重（LRU 容量 4096、TTL 600s，覆盖平台超时重试）；
3. **立即返回 200**（平台要求 ~3s 内确认，Agent 调用可能数十秒）；
4. 投递到该 `(connector_id, chat_id)` 的**进程内串行队列**（`asyncio.Queue`，上限 5，队满丢弃新消息并向该会话回复「消息较多，请稍后再试」），后台 worker 依序处理：
   - 连接器不存在/停用 → 丢弃并记日志；
   - get-or-create 会话映射 → `thread_id`；更新 `last_user_ref` / `last_active_at`；
   - 绑定 Agent 未发布 → 回复「绑定的 Agent 未发布，暂时无法处理消息」；
   - 调用 Agent：复用 `get_agent_instance(agent)` + `graph.ainvoke({"messages": [HumanMessage(content=...)]}, config={"configurable": {"thread_id": thread_id}})`；群聊消息内容带 `[{user_name}]: ` 前缀；整体 `asyncio.wait_for` 120s 超时；
   - 取 `result["messages"][-1].content`（list 内容时拼接文本部分）作为回复，经 `adapter.send` 推送；
   - Agent 失败/超时 → 回复「处理失败，请稍后重试」+ `notify_alert`；发送失败 → 记日志 + `notify_alert`。

## 6. 管理 API

| 方法与路径 | 说明 |
|---|---|
| `GET /api/admin/connectors` | 列表（含 `agent_name`、后端拼好的 `webhook_url`、聚合的 `last_active_at`）；**响应不含凭据** |
| `POST /api/admin/connectors` | 创建；name 重复 409；telegram `secret_token` 留空自动生成 |
| `PATCH /api/admin/connectors/{id}` | 更新 name/description/agent_id/enabled/credentials；**凭据留空字段不覆盖原值** |
| `DELETE /api/admin/connectors/{id}` | 删除（级联清会话映射） |
| `GET /api/admin/connectors/{id}/conversations` | 最近会话（按 `last_active_at` 取前 20，测试发送的会话下拉用） |
| `POST /api/admin/connectors/{id}/send` | 主动推送：`{chat_id?: str, text: str}`；缺省推最近活跃会话，无会话 404；返回实际使用的 chat_id |

`webhook_url` 拼接规则：`{PUBLIC_BASE_URL}/api/connectors/{platform}/{connector_id}/webhook`；`PUBLIC_BASE_URL` 未配置时返回相对路径并在前端提示。

## 7. 配置（`config.py`）

| 新增 | 默认 | 用途 |
|---|---|---|
| `PUBLIC_BASE_URL` | `""` | 展示完整 webhook URL；配置后 Telegram 在创建/启用/改凭据成功时自动调 `setWebhook`（带 `secret_token`，失败仅提示不阻断保存），停用时不删除 webhook（仅拒绝处理） |

飞书/Slack 的 webhook 需在其开放平台控制台手动注册（管理页提供一键复制）。部署者需在各平台为应用授予收发消息所需权限（飞书 `im:message`，Slack `chat:write` 及事件订阅）。

## 8. 前端（MUI，风格对齐现有管理页）

| 文件 | 内容 |
|---|---|
| `web/src/app/admin/connectors/page.tsx` | 列表页：名称、平台图标、绑定 Agent、启用开关（行内）、最近活跃、操作（编辑/发送测试/删除确认）；顶部「新建连接器」 |
| `web/src/components/admin/ConnectorDialog.tsx` | 新建/编辑：平台选择驱动凭据表单动态渲染；凭据回显脱敏（`****`，留空 = 不修改）；绑定 Agent 下拉列出全部 Agent；保存成功后展示 webhook URL + 一键复制，Telegram 显示自动注册结果 |
| `web/src/components/admin/SendTestDialog.tsx` | 发送测试：会话下拉（`GET .../conversations`）或手填 chat_id + 文本 |
| `web/src/components/admin/AdminShell.tsx` | `NAV_ITEMS` 增加「连接器管理」（`/admin/connectors`） |
| `web/src/lib/adminApi.ts` | `Connector*` 类型 + `listConnectors` / `createConnector` / `updateConnector` / `deleteConnector` / `listConnectorConversations` / `sendConnectorMessage` |

## 9. 测试（沿用现有零外部依赖模式）

`tests/test_connectors_api.py`（ASGITransport + `dependency_overrides` + monkeypatch）：

- 管理 CRUD：匿名 401、重名 409、**响应脱敏断言**（不含 `app_secret`/`bot_token` 明文）、PATCH 凭据留空不覆盖、webhook_url 拼接；
- webhook：未知 platform / 不存在连接器 404；验签失败 401；challenge 正确应答；正常消息 → 200 且 Agent 以正确 `thread_id` 被调用、`send` 收到最终回复；`event_id` 重复只处理一次；群聊非 @ 忽略；停用连接器拒绝；Agent 抛错时回复兜底文案；
- 推送接口：指定 chat_id / 缺省最近活跃 / 无会话 404。

`tests/test_connector_adapters.py`：

- 各平台 payload 归一化、验签正/误例（Slack 含时间戳偏移拒绝）、过滤规则（群聊非 @、bot 自身）、超长文本分段。

## 10. 安全与错误处理

- webhook 三层防线：平台验签 → connector 定位 → event 去重；群聊仅响应 @提及 防回复风暴；
- 队列上限 + 120s 处理超时，防消息洪泛拖垮网关；
- 凭据永不写入日志与 API 响应；
- 所有出站/告警失败均不阻断主流程（对齐 `notifier.py` 哲学）。

## 11. 验收标准

1. 管理页可完成三平台连接器的创建/编辑/启停/删除，凭据脱敏展示；
2. 飞书私聊与群聊 @、Telegram 同规则、Slack 同规则下，消息能得到绑定 Agent 的正确回复，且同会话历史连续；
3. 配置 `PUBLIC_BASE_URL` 后 Telegram 自动完成 webhook 注册，管理页可复制完整回调地址；
4. `POST /api/admin/connectors/{id}/send` 可向指定/最近活跃会话成功推送测试消息；
5. `uv run pytest` 全绿，`uv run basedpyright` 与 `uv run ruff check` 无新增告警。

## 12. 范围外（后续迭代）

Discord/钉钉/企业微信适配器；图片/文件/卡片消息；Agent 侧主动推送工具；DB 级事件去重与消息审计日志；多连接器绑多 Agent 的路由规则。
