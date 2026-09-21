/**
 * Skill 绑定的前端预算估算与字节格式化（纯函数）。
 * 规格来源：docs/superpowers/specs/2026-09-20-skill-binding-design.md §5/§12。
 */

/** 单 Agent 绑定正文总量上限（与后端 MAX_BINDING_CONTENT_BYTES 一致） */
export const MAX_BINDING_CONTENT_BYTES = 131072;

interface SkillLike {
  name: string;
  load_mode: string;
  content?: string;
  size_bytes?: number;
  files?: { size?: number }[];
}

/** 常驻技能正文字符估算（always 正文将进入 system prompt）。 */
export function estimateResidentChars(skills: SkillLike[]): number {
  return skills
    .filter((s) => s.load_mode === "always")
    .reduce((sum, s) => sum + (s.content?.length ?? 0), 0);
}

/**
 * 常驻技能正文的 UTF-8 字节估算。
 *
 * 后端按 `len(content.encode("utf-8"))` 计量，中文正文的 JS 字符数只有字节数的
 * 约 1/3 —— 凡是跟字节上限（如 MAX_BINDING_CONTENT_BYTES）同屏展示的估算，
 * 都必须走这个字节口径，否则中文内容会被严重低估。
 */
export function estimateResidentBytes(skills: SkillLike[]): number {
  return skills
    .filter((s) => s.load_mode === "always")
    .reduce((sum, s) => sum + utf8ByteLength(s.content ?? ""), 0);
}

/** 字符串的 UTF-8 字节长度。 */
function utf8ByteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** 单个 skill 总字节 = 正文 + 附件（后端 size_bytes + files[].size）。 */
export function totalSkillBytes(skill: SkillLike): number {
  const files = (skill.files ?? []).reduce((sum, f) => sum + (f.size ?? 0), 0);
  return (skill.size_bytes ?? 0) + files;
}

/** 字节可读格式化（预览清单展示用）。 */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** 粘贴 tab 的「从本地选择 SKILL.md」：判定所选文件名是否为 Markdown 文件。 */
export function isSkillMdFileName(name: string): boolean {
  return /\.(md|markdown)$/i.test(name);
}

/** Uint8Array → 纯 base64（分片拼接，避免超大文件一次性构造超长字符串）。 */
export function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  }
  return btoa(binary);
}
