/**
 * 管理中心 API 客户端：JWT 鉴权 + Agent CRUD / 发布下线 / 连通性测试。
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";
const TOKEN_KEY = "a2a_admin_token";

export interface A2ATargetInput {
  url: string;
  token: string;
}

export interface Agent {
  id: number;
  slug: string;
  name: string;
  description: string;
  /** 在「A2A 管理」中勾选的目标 id（选择式绑定） */
  a2a_target_ids: number[];
  /** 在「MCP 管理」中勾选的服务 id（选择式绑定） */
  mcp_server_ids: number[];
  /** 由 a2a_target_ids 解析出的绑定快照（只读） */
  a2a_targets: A2ATargetInput[];
  system_prompt: string | null;
  status: "draft" | "published";
  created_at: string;
  updated_at: string;
}

export interface AgentCreatePayload {
  slug: string;
  name: string;
  description?: string;
  /** 优先使用：从「A2A 管理」注册表勾选的 id */
  a2a_target_ids?: number[];
  mcp_server_ids?: number[];
  /** 兼容字段：直接传 url/token（未传 a2a_target_ids 时生效） */
  a2a_targets?: A2ATargetInput[];
  system_prompt?: string | null;
}

export type AgentUpdatePayload = Partial<Omit<AgentCreatePayload, "slug">> & {
  status?: "draft" | "published";
};

// ---------------------------------------------------------------------------
// 鉴权方式（A2A 目标与 MCP 服务共用）
// ---------------------------------------------------------------------------
export type AuthType = "none" | "bearer" | "header" | "query" | "basic";

export const AUTH_TYPE_LABELS: Record<AuthType, string> = {
  none: "无鉴权",
  bearer: "Bearer Token（Authorization 头）",
  header: "自定义请求头",
  query: "URL 查询参数",
  basic: "Basic（用户名 + 密码）",
};

/** 需要额外填写「名称」的鉴权方式：header→头名、query→参数名、basic→用户名 */
export const AUTH_NAME_LABELS: Partial<Record<AuthType, string>> = {
  header: "请求头名称",
  query: "查询参数名",
  basic: "用户名",
};

export const AUTH_NAME_PLACEHOLDERS: Partial<Record<AuthType, string>> = {
  header: "X-Api-Key",
  query: "access_token",
  basic: "admin",
};

// ---------------------------------------------------------------------------
// A2A 目标注册表
// ---------------------------------------------------------------------------
export interface A2AEndpoint {
  id: number;
  name: string;
  url: string;
  token: string;
  description: string;
  auth_type: AuthType;
  auth_name: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface A2AEndpointCreatePayload {
  name: string;
  url: string;
  token?: string;
  description?: string;
  auth_type?: AuthType;
  auth_name?: string;
  enabled?: boolean;
}

export type A2AEndpointUpdatePayload = Partial<A2AEndpointCreatePayload>;

// ---------------------------------------------------------------------------
// MCP 服务注册表
// ---------------------------------------------------------------------------
export type McpTransport = "stdio" | "sse" | "streamable_http";

export const MCP_TRANSPORT_LABELS: Record<McpTransport, string> = {
  stdio: "stdio（本地进程）",
  sse: "sse（远端 SSE）",
  streamable_http: "streamable http（推荐）",
};

export interface McpServer {
  id: number;
  name: string;
  description: string;
  transport: McpTransport;
  url: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  token: string;
  auth_type: AuthType;
  auth_name: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface McpServerCreatePayload {
  name: string;
  description?: string;
  transport: McpTransport;
  url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  token?: string;
  auth_type?: AuthType;
  auth_name?: string;
  enabled?: boolean;
}

export type McpServerUpdatePayload = Partial<McpServerCreatePayload>;

export interface McpToolInfo {
  name: string;
  description: string;
}

// ---------------------------------------------------------------------------
// API Key（对外 A2A 服务调用凭据）
// ---------------------------------------------------------------------------
export interface ApiKey {
  id: number;
  name: string;
  key: string;
  is_default: boolean;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ApiKeyCreatePayload {
  name: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

/** 管理中心专用错误：携带 HTTP 状态码，便于区分 401。 */
export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function getAdminToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setAdminToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

/**
 * 解析 JWT 的 payload（**纯函数，不校验签名**）。
 *
 * 仅用于界面展示（显示当前登录用户名、判断是否过期）；真正的鉴权始终由后端完成。
 * 支持 base64url 与 UTF-8，返回 null 表示 token 结构非法或内容无法解析。
 */
export function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;

  // base64url → base64，并补齐 padding
  const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=");

  try {
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    const parsed: unknown = JSON.parse(new TextDecoder().decode(bytes));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** 判断 JWT payload 是否已失效（无法解析 / 已过期）。无 exp 时交给后端兜底 401。 */
export function isJwtExpired(
  payload: Record<string, unknown> | null,
  now: number = Date.now(),
): boolean {
  if (!payload) return true;
  const exp = payload.exp;
  if (typeof exp !== "number") return false;
  return exp * 1000 <= now;
}

/**
 * 读取本地登录态，供对话页入口等 UI 使用。
 * 登录态存于 localStorage，服务端渲染阶段读不到（会返回未登录）。
 */
export function readAdminAuth(): { loggedIn: boolean; username: string | null } {
  const token = getAdminToken();
  if (!token) return { loggedIn: false, username: null };

  const payload = decodeJwtPayload(token);
  if (isJwtExpired(payload)) return { loggedIn: false, username: null };

  const sub = payload?.sub;
  return { loggedIn: true, username: typeof sub === "string" && sub ? sub : null };
}

/** 本地是否存在「未过期」的管理中心 token（仅用于界面展示，后端仍会独立校验）。 */
export function hasValidAdminToken(): boolean {
  return readAdminAuth().loggedIn;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getAdminToken();
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers || {}),
    },
  });

  if (resp.status === 401) {
    setAdminToken(null);
  }

  if (!resp.ok) {
    let detail = `请求失败（${resp.status}）`;
    try {
      const data = await resp.json();
      if (data?.detail) {
        detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      }
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }

  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const adminApi = {
  login(username: string, password: string): Promise<TokenResponse> {
    return request<TokenResponse>("/api/admin/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
  },

  listAgents(): Promise<Agent[]> {
    return request<Agent[]>("/api/admin/agents");
  },

  createAgent(payload: AgentCreatePayload): Promise<Agent> {
    return request<Agent>("/api/admin/agents", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateAgent(id: number, payload: AgentUpdatePayload): Promise<Agent> {
    return request<Agent>(`/api/admin/agents/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },

  deleteAgent(id: number): Promise<void> {
    return request<void>(`/api/admin/agents/${id}`, { method: "DELETE" });
  },

  publish(id: number): Promise<Agent> {
    return request<Agent>(`/api/admin/agents/${id}/publish`, { method: "POST" });
  },

  unpublish(id: number): Promise<Agent> {
    return request<Agent>(`/api/admin/agents/${id}/unpublish`, { method: "POST" });
  },

  /** 测试 A2A 目标连通性（后端复用 message 字段承载 target JSON）。 */
  testConnection(target: A2ATargetInput): Promise<{ ok: boolean; message: string }> {
    return request<{ ok: boolean; message: string }>("/api/admin/agents/test-connection", {
      method: "POST",
      body: JSON.stringify({ message: JSON.stringify(target) }),
    });
  },

  // ---- A2A 目标注册表 ----
  listA2AEndpoints(): Promise<A2AEndpoint[]> {
    return request<A2AEndpoint[]>("/api/admin/a2a-endpoints");
  },

  createA2AEndpoint(payload: A2AEndpointCreatePayload): Promise<A2AEndpoint> {
    return request<A2AEndpoint>("/api/admin/a2a-endpoints", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateA2AEndpoint(id: number, payload: A2AEndpointUpdatePayload): Promise<A2AEndpoint> {
    return request<A2AEndpoint>(`/api/admin/a2a-endpoints/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },

  /** 删除目标；被 Agent 引用时后端返回 409，可用 force=true 自动解绑。 */
  deleteA2AEndpoint(id: number, force = false): Promise<void> {
    const suffix = force ? "?force=true" : "";
    return request<void>(`/api/admin/a2a-endpoints/${id}${suffix}`, { method: "DELETE" });
  },

  testA2AEndpoint(id: number): Promise<{ ok: boolean; message: string }> {
    return request<{ ok: boolean; message: string }>(`/api/admin/a2a-endpoints/${id}/test`, {
      method: "POST",
    });
  },

  // ---- MCP 服务注册表 ----
  listMcpServers(): Promise<McpServer[]> {
    return request<McpServer[]>("/api/admin/mcp-servers");
  },

  createMcpServer(payload: McpServerCreatePayload): Promise<McpServer> {
    return request<McpServer>("/api/admin/mcp-servers", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateMcpServer(id: number, payload: McpServerUpdatePayload): Promise<McpServer> {
    return request<McpServer>(`/api/admin/mcp-servers/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },

  deleteMcpServer(id: number, force = false): Promise<void> {
    const suffix = force ? "?force=true" : "";
    return request<void>(`/api/admin/mcp-servers/${id}${suffix}`, { method: "DELETE" });
  },

  testMcpServer(id: number): Promise<{ ok: boolean; message: string }> {
    return request<{ ok: boolean; message: string }>(`/api/admin/mcp-servers/${id}/test`, {
      method: "POST",
    });
  },

  listMcpServerTools(id: number): Promise<{ ok: boolean; tools: McpToolInfo[]; message: string }> {
    return request<{ ok: boolean; tools: McpToolInfo[]; message: string }>(
      `/api/admin/mcp-servers/${id}/tools`,
    );
  },

  // ---- API Key 管理（对外 A2A 服务调用凭据）----
  listApiKeys(): Promise<ApiKey[]> {
    return request<ApiKey[]>("/api/admin/api-keys");
  },

  createApiKey(payload: ApiKeyCreatePayload): Promise<ApiKey> {
    return request<ApiKey>("/api/admin/api-keys", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  /** 删除 Key；默认 Key 后端返回 400 不可删除。 */
  deleteApiKey(id: number): Promise<void> {
    return request<void>(`/api/admin/api-keys/${id}`, { method: "DELETE" });
  },
};

/** Agent 对外的 A2A 地址路径（默认 Agent 为 /a2a）。 */
export function a2aPathForAgent(agent: Pick<Agent, "slug">): string {
  return agent.slug === "/" ? "/a2a" : `/a2a/${agent.slug}`;
}

/** 管理中心测试对话的 SSE 端点。 */
export function adminTestChatUrl(agentId: number): string {
  return `${API_BASE}/api/admin/agents/${agentId}/test`;
}

/** 管理中心测试对话所需的鉴权头。 */
export function adminAuthHeaders(): Record<string, string> {
  const token = getAdminToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// 注：原先的「可选工具集」（如 web_search）已移除，其功能由 MCP 服务替代。
// Agent 的能力扩展统一通过「MCP 管理」勾选服务后自动绑定工具完成。
