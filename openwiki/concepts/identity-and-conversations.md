---
type: concept-guide
title: 身份、会话与所有权
description: 解释单 httpOnly JWT 身份 cookie 的双身份设计、登录时会话幂等归并、目录与 checkpointer 分工、所有权校验与删除级联。
tags: [identity, cookie, conversations, ownership, jwt, sessions]
verified:
  - by: openwiki/0.5.2
    at: 2026-09-23T05:12:56.927Z
sources:
  - id: openwiki-source-935b638c916bf49861439611
    resource: repo://src/a2a_gateway/identity.py
  - id: openwiki-source-c02a6d45a645df8106612f51
    resource: repo://src/a2a_gateway/models.py
  - id: openwiki-source-d3e47f45c8a3dad144965b78
    resource: repo://src/a2a_gateway/repository.py
  - id: openwiki-source-7fd5ad639fdb57e093b58272
    resource: repo://src/a2a_gateway/routes/admin.py
  - id: openwiki-source-96e2981cfaad30985f414a47
    resource: repo://src/a2a_gateway/routes/chat.py
generated: { by: "opencode", at: "2026-09-23T05:12:56.927Z" }
---

# 身份、会话与所有权

## 职责与归属

- **身份签发/解析**：`src/a2a_gateway/identity.py` —— 匿名访客与登录管理员共用一个 httpOnly JWT cookie `a2a_identity`。
- **目录 CRUD 与归并**：`repository.py` 的 `list/upsert/rename/delete/claim/import_conversations`（`repository.py:927-1103`）。
- **路由**：`routes/chat.py` 的 identity/conversations 端点（注册在 `{slug}` 通配之前，`chat.py:175-176`）；登录归并挂在 `routes/admin.py` 的 `/login`。
- **模型**：`Conversation`（`models.py:226-251`）；消息本体在 LangGraph checkpointer。
- **孤儿 thread 回填**：`scripts/backfill_conversations.py`。

## 一套令牌两种身份

cookie `a2a_identity` 内是 JWT：`typ=visitor`（`sub` 为 uuid）或 `typ=user`（`sub` 为用户名）；`kind` 与 `Conversation.owner_kind` 取值一致（`identity.py:1-7`、`38-41`）。设计理由（`identity.py:9-13`）：

- **httpOnly + 后端签名**：前端 JS 读不到也改不了，无法伪造他人 `visitor_id` 窃取会话；刻意不用 localStorage。
- **登录归并零搬迁**：消息按 `thread_id` 存 checkpointer、与归属无关，过户只改目录两列。

cookie 属性：`httponly=True`、`samesite=lax`、`secure` 取 `COOKIE_SECURE`（`identity.py:86-96`）。有效期：访客 `VISITOR_EXPIRE_DAYS`（默认 180 天）、用户 `IDENTITY_EXPIRE_DAYS`（默认 30 天），用户态刻意长于管理中心 JWT（`identity.py:43-44`、`config.py:79-83`）。

解析失败（无 cookie/签名错/过期/`typ` 非法）一律返回 `None`（`identity.py:112-124`）；`ensure_identity` 在缺失时新签访客并返回 `issued=True`（`identity.py:127-136`）。

## SSE 场景的显式设置约束

`EventSourceResponse` 是独立返回的 Response 对象：在 FastAPI 依赖里对注入的 `Response` 调 `set_cookie` **不会生效**。因此身份模块不在依赖中自动签发，而由路由显式 `set_identity_cookie(resp, identity)`（`identity.py:15-18`）。聊天 SSE 路径即如此处理（`chat.py:290-294` 及 retry/slug 变体）；普通 JSON 路径（identity、列表）可直接写在依赖的 `Response` 上（`chat.py:198-201`）。

## 目录与 checkpointer 的分工

`conversations` 只存「谁有哪些会话 + 标题/时间」；消息在 checkpoints 系列表按 `thread_id` 存放（`models.py:226-236`）。标题策略：首条消息（前 24 字）定标题，原标题为空才写入，后续消息不覆盖（`repository.py:981-983`、`chat.py:392`）。

历史读取 `_get_history` 直接从 checkpointer 抽 `human`/`tool`/`ai` 消息，工具卡片以 `role=tool` 还原（`chat.py:409-425`）。

## 所有权校验语义

| 操作 | 归属不符时 | 未登记 thread |
|---|---|---|
| `upsert`（登记/刷新） | 返回 `null`，**不抢归属**（防分享链接被收编，`repository.py:971-987`） | 新建 |
| `rename` / `delete` | 返回 `null` / `False` → 路由 404 或 null | 404 / False |
| 读历史 / `_ensure_owned` | 404「会话不存在」（`chat.py:398-406`） | **放行**（回填前历史数据不受影响） |
| 批量 `import` | 已存在 thread_id 一律跳过（PG `on_conflict_do_nothing`，`repository.py:1055-1101`） | 插入 |

`get_conversation` 返回 `None` 表示「未登记」，调用方据此决定放行还是新建（`repository.py:950-957`）。

## 登录归并（claim）

登录成功后，若当前 cookie 是访客身份，执行单条幂等 `UPDATE`：把该 `visitor_id` 名下会话的 `owner_kind/owner_id` 改为 `user/username`（`repository.py:1028-1052`）；`rowcount` 作为 `claimed` 返回给前端（`admin.py:104-114`）。重复登录第二次影响 0 行。归并同时把身份 cookie 换成 user 态；**退出登录则签发全新访客 id**，刻意不复用旧 id，退出后从零开始、不残留已归并走的会话（`admin.py:117-127`）。

## 删除级联

`DELETE /api/chat/conversations/{thread_id}`（`chat.py:251-275`）顺序：

1. 目录删除（带归属校验）；失败 → 404。
2. `pending_store.delete(thread_id)` 清挂起任务 —— 失败仅记日志，不影响主流程。
3. `checkpointer.adelete_thread(thread_id)` 清消息本体 —— 失败**不回滚**目录删除，避免产生「删不掉的幽灵会话」。

三步均 best-effort 协同，目录是权威删除点。

## 孤儿 thread 回填

目录表是后加的：此前 thread 只存在于 checkpoints、侧边栏看不到（`backfill_conversations.py:4-10`）。脚本把孤儿 thread 补登记到指定账号（默认 `ADMIN_USERNAME`），支持 `--dry-run`；消息原地可续。容器内用法见脚本 docstring（`backfill_conversations.py:12-22`）。

## 不变量

- `thread_id` 全局唯一（`uq_conversations_thread`），跨聊天、连接器、A2A 恢复共用线程键。
- 归属只信目录两列；消息本体永不随归属搬迁。
- 未登记 thread 的读写放行，是升级兼容契约，不是漏洞绕过——分享链接场景由 upsert「不抢归属」兜住。
- 身份 cookie 与管理 JWT 分离：前者管会话归属（更长有效期），后者管 `/api/admin/*`（Bearer，短有效期）。

## 代表性测试

- `tests/test_conversations.py`：cookie 完整性（篡改/过期拒绝）、列表/改名/删除归属、登录 claim、import 幂等。
- `tests/test_admin_api.py`：登录 401 与归并触发。
- 相关页：[聊天请求生命周期](/openwiki/workflows/chat-lifecycle.md)、[认证与安全面](/openwiki/concepts/security.md)、[数据模型与迁移](/openwiki/architecture/data-model.md)、[聊天平台连接器](/openwiki/integrations/connectors.md)。
