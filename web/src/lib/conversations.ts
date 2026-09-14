/**
 * 对话历史管理（服务端存储，按身份归属）。
 *
 * 会话目录存在后端 conversations 表里，归属由后端签发的 httpOnly 身份 cookie
 * 决定：匿名时归 visitor，登录时自动归并到账号 —— 因此换机器 / 换浏览器登录后
 * 仍能看到自己的全部会话。
 *
 * 迁移策略：旧版本把会话列表放在 localStorage（`a2a_convs_<slug>`）。
 * 首次加载时若发现本地残留，会一次性导入服务端并清掉本地数据（见
 * `migrateLegacyList`），之后一律以服务端为准。
 *
 * 「当前选中的会话」仍存在 localStorage：它是「这台浏览器正在看哪一条」的
 * 临时状态，不需要跨设备同步。
 */

import {
  ConversationRecord,
  deleteConversation as apiDeleteConversation,
  ensureIdentity,
  fetchConversations,
  importConversations,
  saveConversation,
} from "@/lib/api";

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
/** 标记某 slug 的本地列表是否已导入服务端（避免重复导入） */
const IMPORTED_PREFIX = "a2a_imported_";

function listKey(slug: string): string {
  return `${LIST_PREFIX}${slug || "/"}`;
}

function activeKey(slug: string): string {
  return `${ACTIVE_PREFIX}${slug || "/"}`;
}

function importedKey(slug: string): string {
  return `${IMPORTED_PREFIX}${slug || "/"}`;
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

/** 服务端记录 → 前端会话结构（ISO 时间串转毫秒时间戳）。 */
export function fromRecord(record: ConversationRecord): Conversation {
  return {
    id: record.thread_id,
    title: record.title,
    createdAt: Date.parse(record.created_at) || 0,
    updatedAt: Date.parse(record.updated_at) || 0,
  };
}

/** 读取当前选中的会话 id（仅本地临时状态）。 */
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

/** 读取旧版本遗留在 localStorage 的会话列表（只读，不清理）。 */
function readLocalList(slug: string): Conversation[] {
  if (!isBrowser()) return [];
  try {
    const raw = localStorage.getItem(listKey(slug));
    const parsed = raw ? (JSON.parse(raw) as Conversation[]) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((c) => c && typeof c.id === "string");
  } catch {
    return [];
  }
}

/**
 * 兼容旧版本：把单个 `a2a_session_<slug>` 的 thread_id 并入本地列表。
 * @returns 迁移出的会话（若有）
 */
export function migrateLegacy(slug: string): Conversation | null {
  if (!isBrowser()) return null;
  const legacyKey = `${LEGACY_PREFIX}${slug || "/"}`;
  const legacy = localStorage.getItem(legacyKey);
  if (!legacy) return null;
  localStorage.removeItem(legacyKey);

  const conversations = readLocalList(slug);
  const existing = conversations.find((c) => c.id === legacy);
  if (existing) return existing;

  const conv: Conversation = {
    id: legacy,
    title: "历史对话",
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
  localStorage.setItem(listKey(slug), JSON.stringify([conv, ...conversations]));
  setActiveId(slug, conv.id);
  return conv;
}

/**
 * 一次性迁移：把本地残留的会话列表导入服务端，成功后清掉本地数据。
 *
 * 只在「本 slug 尚未迁移过」且「本地确实有数据」时真正发请求。
 * @returns 是否执行了导入
 */
export async function migrateLegacyList(slug: string): Promise<boolean> {
  if (!isBrowser()) return false;
  if (localStorage.getItem(importedKey(slug))) return false;

  migrateLegacy(slug);
  const local = readLocalList(slug);
  // 标记前置：即使导入失败也不再重试，避免每次刷新都打一次接口
  localStorage.setItem(importedKey(slug), "1");
  if (local.length === 0) return false;

  try {
    await importConversations(
      slug,
      local.map((c) => ({ thread_id: c.id, title: c.title || "" })),
    );
  } catch {
    return false;
  }
  localStorage.removeItem(listKey(slug));
  return true;
}

/** 身份 cookie 由后端 httpOnly 下发，这里只确保它已存在。 */
export async function initIdentity(): Promise<boolean> {
  const identity = await ensureIdentity();
  return identity !== null;
}

/** 从服务端读取会话列表（按最近更新倒序）；失败时返回空数组。 */
export async function loadConversations(slug: string): Promise<Conversation[]> {
  try {
    const records = await fetchConversations(slug);
    return records.map(fromRecord);
  } catch {
    return [];
  }
}

/** 新建会话：先在本地落地（保证对话不被打断），再异步登记到服务端。 */
export async function createConversation(
  slug: string,
  title = "新对话",
): Promise<Conversation> {
  const conv: Conversation = {
    id: generateId(),
    title,
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
  setActiveId(slug, conv.id);
  await saveConversation(slug, conv.id, title);
  return conv;
}

/** 刷新一条会话（续聊时更新排序）；首条消息会带标题。 */
export async function touchConversation(
  slug: string,
  id: string,
  title?: string,
): Promise<void> {
  await saveConversation(slug, id, title);
}

/** 删除会话。 */
export async function removeConversation(slug: string, id: string): Promise<void> {
  await apiDeleteConversation(id);
}
