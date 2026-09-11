/**
 * 对话历史管理（localStorage，匿名访客）。
 *
 * 每个 Agent 路由 (slug) 维护一个会话列表，每个会话对应后端 Checkpointer 的一个
 * thread_id。列表与「当前选中会话」都按 slug 分开存储，互不影响。
 */

export interface Conversation {
  /** 后端 thread_id */
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
}

const LIST_PREFIX = "a2a_convs_";
const ACTIVE_PREFIX = "a2a_active_";
const LEGACY_PREFIX = "a2a_session_";

function listKey(slug: string): string {
  return `${LIST_PREFIX}${slug || "/"}`;
}

function activeKey(slug: string): string {
  return `${ACTIVE_PREFIX}${slug || "/"}`;
}

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

/** 生成 thread_id（优先用 crypto.randomUUID）。 */
export function generateId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `t-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 读取某个 slug 下的会话列表（按最近更新倒序）。 */
export function listConversations(slug: string): Conversation[] {
  if (!isBrowser()) return [];
  try {
    const raw = localStorage.getItem(listKey(slug));
    const parsed = raw ? (JSON.parse(raw) as Conversation[]) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((c) => c && typeof c.id === "string")
      .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
  } catch {
    return [];
  }
}

function save(slug: string, conversations: Conversation[]): void {
  if (!isBrowser()) return;
  localStorage.setItem(listKey(slug), JSON.stringify(conversations));
}

/** 读取当前选中的会话 id。 */
export function getActiveId(slug: string): string | null {
  if (!isBrowser()) return null;
  return localStorage.getItem(activeKey(slug));
}

/** 设置/清除当前选中的会话 id。 */
export function setActiveId(slug: string, id: string | null): void {
  if (!isBrowser()) return;
  if (id) localStorage.setItem(activeKey(slug), id);
  else localStorage.removeItem(activeKey(slug));
}

/**
 * 兼容旧版本：把单个 `a2a_session_<slug>` 的 thread_id 迁移为一条会话记录。
 * @returns 迁移出的会话（若有）
 */
export function migrateLegacy(slug: string): Conversation | null {
  if (!isBrowser()) return null;
  const legacyKey = `${LEGACY_PREFIX}${slug || "/"}`;
  const legacy = localStorage.getItem(legacyKey);
  if (!legacy) return null;
  localStorage.removeItem(legacyKey);

  const conversations = listConversations(slug);
  const existing = conversations.find((c) => c.id === legacy);
  if (existing) return existing;

  const conv: Conversation = {
    id: legacy,
    title: "历史对话",
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
  save(slug, [conv, ...conversations]);
  setActiveId(slug, conv.id);
  return conv;
}

/** 新建会话并置为当前选中。 */
export function createConversation(slug: string, title = "新对话"): Conversation {
  const conv: Conversation = {
    id: generateId(),
    title,
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
  save(slug, [conv, ...listConversations(slug)]);
  setActiveId(slug, conv.id);
  return conv;
}

/** 新增或更新一条会话（按 id 匹配），并刷新 updatedAt。 */
export function upsertConversation(
  slug: string,
  id: string,
  patch: Partial<Pick<Conversation, "title">> = {},
): Conversation {
  const conversations = listConversations(slug);
  const idx = conversations.findIndex((c) => c.id === id);
  let conv: Conversation;
  if (idx >= 0) {
    conv = { ...conversations[idx], ...patch, updatedAt: Date.now() };
    conversations[idx] = conv;
  } else {
    conv = {
      id,
      title: patch.title || "新对话",
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    conversations.unshift(conv);
  }
  save(slug, conversations);
  return conv;
}

/** 删除一条会话；若删除的是当前会话，则自动切换到最近的一条。 */
export function deleteConversation(slug: string, id: string): void {
  const conversations = listConversations(slug).filter((c) => c.id !== id);
  save(slug, conversations);
  if (getActiveId(slug) === id) {
    setActiveId(slug, conversations[0]?.id ?? null);
  }
}
