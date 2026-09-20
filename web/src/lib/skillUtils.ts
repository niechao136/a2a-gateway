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
