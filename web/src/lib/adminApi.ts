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
  a2a_targets: A2ATargetInput[];
  system_prompt: string | null;
  enabled_tools: string[];
  status: "draft" | "published";
  created_at: string;
  updated_at: string;
}

export interface AgentCreatePayload {
  slug: string;
  name: string;
  description?: string;
  a2a_targets: A2ATargetInput[];
  system_prompt?: string | null;
  enabled_tools: string[];
}

export type AgentUpdatePayload = Partial<Omit<AgentCreatePayload, "slug">> & {
  status?: "draft" | "published";
};

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
};

/** 管理中心测试对话的 SSE 端点。 */
export function adminTestChatUrl(agentId: number): string {
  return `${API_BASE}/api/admin/agents/${agentId}/test`;
}

/** 管理中心测试对话所需的鉴权头。 */
export function adminAuthHeaders(): Record<string, string> {
  const token = getAdminToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** 可选工具（与后端 tools.py 的 OPTIONAL_TOOLS 保持一致）。 */
export const AVAILABLE_TOOLS: { name: string; label: string; description: string }[] = [
  { name: "web_search", label: "网页搜索", description: "在网页上搜索信息（MVP 阶段为占位实现）" },
];
