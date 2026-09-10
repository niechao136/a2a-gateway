/**
 * 匿名访客 session 管理（localStorage）。
 * 每个 Agent 路由 (slug) 独立一个 thread_id，刷新页面后续聊。
 */

const KEY_PREFIX = "a2a_session_";

function storageKey(slug: string): string {
  return `${KEY_PREFIX}${slug || "/"}`;
}

/** 获取指定 slug 的 thread_id（若无则返回 null）。 */
export function getThreadId(slug: string): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(storageKey(slug));
}

/** 保存 thread_id。 */
export function setThreadId(slug: string, threadId: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(storageKey(slug), threadId);
}

/** 清除指定 slug 的 session。 */
export function clearThreadId(slug: string): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(storageKey(slug));
}
