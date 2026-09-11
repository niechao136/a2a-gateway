/**
 * 后端 API 客户端：SSE 流式对话 + 历史记录。
 *
 * 开发期通过 next.config rewrite 走 /api/*（同源），生产期经 nginx 同源反向代理
 * （NEXT_PUBLIC_API_BASE_URL 为空），也可配置为后端绝对地址。
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

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
      const eventName = parsedBlock.event;
      const raw = parsedBlock.data;

      try {
        const parsed = JSON.parse(raw);
        switch (eventName) {
          case "token":
            onEvent({ type: "token", content: parsed.content || "" });
            break;
          case "tool_start":
            onEvent({ type: "tool_start", name: parsed.name || "" });
            break;
          case "tool_end":
            onEvent({
              type: "tool_end",
              name: parsed.name || "",
              output: String(parsed.output ?? ""),
            });
            break;
          case "done":
            finalThreadId = parsed.thread_id || finalThreadId;
            onEvent({ type: "done", thread_id: finalThreadId });
            break;
          case "error":
            onEvent({ type: "error", detail: parsed.detail || "对话出错" });
            break;
          default:
            break;
        }
      } catch {
        // 忽略非 JSON 数据块
      }
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

/** 获取会话历史。 */
export async function fetchHistory(slug: string, threadId: string): Promise<ChatMessage[]> {
  const url =
    slug && slug !== "/"
      ? `${API_BASE}/api/chat/${slug}/history?thread_id=${encodeURIComponent(threadId)}`
      : `${API_BASE}/api/chat/history?thread_id=${encodeURIComponent(threadId)}`;
  const resp = await fetch(url);
  if (!resp.ok) return [];
  const data = await resp.json();
  return Array.isArray(data) ? data : [];
}
