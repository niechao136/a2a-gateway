/**
 * 后端 API 客户端：SSE 流式对话 + 历史记录 + 会话目录。
 *
 * 开发期通过 next.config rewrite 走 /api/*（同源），生产期经 nginx 同源反向代理
 * （NEXT_PUBLIC_API_BASE_URL 为空），也可配置为后端绝对地址。
 *
 * 所有请求都带 credentials: "include" —— 会话归属由后端签发的 httpOnly
 * 身份 cookie 决定，前端读不到也改不了它。
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

/**
 * 身份变化事件：登录 / 退出后由对应页面派发，对话页收到后重新拉取会话列表
 * （登录会把匿名会话归并到账号，列表内容随之变化）。
 */
export const IDENTITY_CHANGED_EVENT = "a2a:identity-changed";

/** 派发身份变化事件（登录页 / 管理中心退出登录时调用）。 */
export function notifyIdentityChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(IDENTITY_CHANGED_EVENT));
}

export type ChatRole = "user" | "assistant" | "tool";

export interface ChatMessage {
  role: ChatRole;
  content: string;
  /** 工具调用元信息（仅 tool 角色） */
  toolName?: string;
  toolOutput?: string;
}

export type SSEEvent =
  | { type: "token"; content: string }
  | { type: "tool_start"; name: string }
  | { type: "tool_end"; name: string; output: string }
  | { type: "interrupt"; question: string }
  | { type: "done"; thread_id: string }
  | { type: "error"; detail: string };

/**
 * 从缓冲区中切出完整的 SSE 事件块，返回 [完整块列表, 剩余未完成内容]。
 *
 * SSE 以「空行」分隔事件块，而 sse-starlette 使用 CRLF（\r\n），
 * 所以必须同时兼容 \n 与 \r\n —— 只用 "\n\n" 切分会永远切不出块。
 */
export function splitSSEBlocks(buffer: string): [string[], string] {
  const blocks = buffer.split(/\r?\n\r?\n/);
  const rest = blocks.pop() || "";
  return [blocks, rest];
}

/** 解析单个 SSE 块的 event 名与 data 内容；无 data 时返回 null。 */
export function parseSSEBlock(block: string): { event: string; data: string } | null {
  let eventName = "message";
  const dataLines: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (dataLines.length === 0) return null;
  return { event: eventName, data: dataLines.join("\n") };
}

/**
 * 把单个 SSE 事件的 event 名与 data 负载映射为 SSEEvent；
 * 未知事件或非法 JSON 返回 null。独立导出便于单测（consumeSSE 内部同样走这里）。
 */
export function parseSSEEvent(eventName: string, raw: string): SSEEvent | null {
  try {
    const parsed = JSON.parse(raw);
    switch (eventName) {
      case "token":
        return { type: "token", content: parsed.content || "" };
      case "tool_start":
        return { type: "tool_start", name: parsed.name || "" };
      case "tool_end":
        return {
          type: "tool_end",
          name: parsed.name || "",
          output: String(parsed.output ?? ""),
        };
      case "interrupt":
        return { type: "interrupt", question: String(parsed.question ?? "") };
      case "done":
        return { type: "done", thread_id: parsed.thread_id || "" };
      case "error":
        return { type: "error", detail: parsed.detail || "对话出错" };
      default:
        return null;
    }
  } catch {
    return null;
  }
}

/**
 * 解析 SSE 响应流并回调事件。返回最终的 thread_id。
 */
async function consumeSSE(
  resp: Response,
  onEvent: (e: SSEEvent) => void,
  initialThreadId: string | null,
): Promise<string> {
  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalThreadId = initialThreadId || "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE 事件块以「空行」分隔（兼容 \n 与 \r\n，见 splitSSEBlocks）
    const [blocks, rest] = splitSSEBlocks(buffer);
    buffer = rest;

    for (const block of blocks) {
      const parsedBlock = parseSSEBlock(block);
      if (!parsedBlock) continue;
      const event = parseSSEEvent(parsedBlock.event, parsedBlock.data);
      if (!event) continue;
      if (event.type === "done") finalThreadId = event.thread_id || finalThreadId;
      onEvent(event);
    }
  }

  return finalThreadId;
}

/**
 * 向指定的 SSE 对话端点发送消息并流式接收事件。
 * @param url 完整的对话端点地址（公开路由或管理中心测试路由）
 * @param message 用户消息
 * @param threadId 会话 ID（续聊时传入）
 * @param onEvent 事件回调
 * @param headers 额外请求头（如管理中心需带 Authorization）
 * @returns 最终的 threadId
 */
export async function streamChatUrl(
  url: string,
  message: string,
  threadId: string | null,
  onEvent: (e: SSEEvent) => void,
  headers: Record<string, string> = {},
): Promise<string> {
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...headers,
    },
    body: JSON.stringify({ message, thread_id: threadId }),
    credentials: "include",
  });

  if (!resp.ok || !resp.body) {
    let detail = "请求失败";
    try {
      const data = await resp.json();
      detail = data.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }

  return consumeSSE(resp, onEvent, threadId);
}

/**
 * 发送消息并流式接收 SSE 事件（基于 fetch + ReadableStream，比 EventSource 更灵活）。
 * @param slug  Agent 路由（空串或 "/" 表示默认 Agent）
 * @param message 用户消息
 * @param threadId 会话 ID（续聊时传入）
 * @param onEvent 事件回调
 * @returns 最终的 threadId
 */
export async function streamChat(
  slug: string,
  message: string,
  threadId: string | null,
  onEvent: (e: SSEEvent) => void,
): Promise<string> {
  const url =
    slug && slug !== "/" ? `${API_BASE}/api/chat/${slug}` : `${API_BASE}/api/chat`;
  return streamChatUrl(url, message, threadId, onEvent);
}

/**
 * 重试最后一次回复（time travel：后端从最后一次人类消息的检查点重放）。
 * @param slug  Agent 路由（空串或 "/" 表示默认 Agent）
 * @param threadId 会话 ID
 * @param onEvent 事件回调
 */
export async function retryChat(
  slug: string,
  threadId: string,
  onEvent: (e: SSEEvent) => void,
): Promise<string> {
  const url =
    slug && slug !== "/"
      ? `${API_BASE}/api/chat/${slug}/retry`
      : `${API_BASE}/api/chat/retry`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ thread_id: threadId }),
    credentials: "include",
  });

  if (!resp.ok || !resp.body) {
    let detail = "请求失败";
    try {
      const data = await resp.json();
      detail = data.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }

  return consumeSSE(resp, onEvent, threadId);
}

/** 获取会话历史。 */
export async function fetchHistory(slug: string, threadId: string): Promise<ChatMessage[]> {
  const url =
    slug && slug !== "/"
      ? `${API_BASE}/api/chat/${slug}/history?thread_id=${encodeURIComponent(threadId)}`
      : `${API_BASE}/api/chat/history?thread_id=${encodeURIComponent(threadId)}`;
  const resp = await fetch(url, { credentials: "include" });
  if (!resp.ok) return [];
  const data = await resp.json();
  return Array.isArray(data) ? data : [];
}

// ---------------------------------------------------------------------------
// 会话目录（服务端存储，按身份归属）
// ---------------------------------------------------------------------------

export type IdentityKind = "visitor" | "user";

export interface ConversationRecord {
  thread_id: string;
  agent_slug: string;
  title: string;
  created_at: string;
  updated_at: string;
}

/** 身份 cookie 由后端 httpOnly 下发；本接口只负责「确保它存在」。 */
export async function ensureIdentity(): Promise<{
  kind: IdentityKind;
  id: string;
} | null> {
  try {
    const resp = await fetch(`${API_BASE}/api/chat/identity`, {
      method: "POST",
      credentials: "include",
    });
    if (!resp.ok) return null;
    return (await resp.json()) as { kind: IdentityKind; id: string };
  } catch {
    return null;
  }
}

/** 当前身份名下的会话列表（按最近更新倒序）。 */
export async function fetchConversations(slug: string): Promise<ConversationRecord[]> {
  const params = new URLSearchParams({ slug });
  const resp = await fetch(`${API_BASE}/api/chat/conversations?${params.toString()}`, {
    credentials: "include",
  });
  if (!resp.ok) throw new Error("读取会话列表失败");
  const data = await resp.json();
  return Array.isArray(data) ? (data as ConversationRecord[]) : [];
}

/** 登记 / 刷新一条会话；会话已归属他人时后端返回 null。 */
export async function saveConversation(
  slug: string,
  threadId: string,
  title?: string,
): Promise<ConversationRecord | null> {
  const resp = await fetch(`${API_BASE}/api/chat/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId, slug, title: title ?? null }),
    credentials: "include",
  });
  if (!resp.ok) return null;
  const data = await resp.json();
  return (data as ConversationRecord | null) ?? null;
}

/** 重命名会话。 */
export async function renameConversation(
  threadId: string,
  title: string,
): Promise<boolean> {
  const resp = await fetch(
    `${API_BASE}/api/chat/conversations/${encodeURIComponent(threadId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
      credentials: "include",
    },
  );
  return resp.ok;
}

/** 删除会话（目录 + 服务端消息本体）。 */
export async function deleteConversation(threadId: string): Promise<boolean> {
  const resp = await fetch(
    `${API_BASE}/api/chat/conversations/${encodeURIComponent(threadId)}`,
    { method: "DELETE", credentials: "include" },
  );
  return resp.ok;
}

/** 批量导入旧 localStorage 里的会话（一次性迁移）。 */
export async function importConversations(
  slug: string,
  items: { thread_id: string; title?: string }[],
): Promise<number> {
  if (items.length === 0) return 0;
  const resp = await fetch(`${API_BASE}/api/chat/conversations/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slug, items }),
    credentials: "include",
  });
  if (!resp.ok) return 0;
  const data = await resp.json();
  return typeof data?.imported === "number" ? data.imported : 0;
}
