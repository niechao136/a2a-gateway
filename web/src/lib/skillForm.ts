/**
 * Skill 编辑表单的附件组装与校验（纯函数）。
 * 口径与后端一致：脚本后缀白名单、单脚本 256KB；这里只做前置提示，后端是权威校验。
 * 规格来源：docs/superpowers/specs/2026-09-21-skill-preview-edit-script-execution-design.md §6/§10。
 */

import { bytesToBase64 } from "./skillUtils";

export type SkillFilePayload = {
  path: string;
  content: string;
  entry_type?: "text" | "script";
  encoding?: "utf-8" | "base64";
};

const SCRIPT_SUFFIXES = [".py", ".sh", ".js"];
const MAX_SCRIPT_BYTES = 256 * 1024;
const MAX_TEXT_BYTES = 1024 * 1024;

/** 文件名是否命中脚本白名单后缀。 */
function isScriptName(name: string): boolean {
  const dot = name.lastIndexOf(".");
  if (dot < 0) return false;
  return SCRIPT_SUFFIXES.includes(name.slice(dot).toLowerCase());
}

/** 文件字节 → 附件 payload（脚本 base64，文本 utf-8 原文）。 */
export function attachmentFromBytes(name: string, bytes: Uint8Array): SkillFilePayload {
  if (isScriptName(name)) {
    return { path: name, content: bytesToBase64(bytes), entry_type: "script", encoding: "base64" };
  }
  return {
    path: name,
    content: new TextDecoder().decode(bytes),
    entry_type: "text",
    encoding: "utf-8",
  };
}

/** 附件前置校验；@returns 错误文案（null = 合法）。 */
export function validateAttachment(payload: SkillFilePayload): string | null {
  const path = payload.path ?? "";
  if (!path.trim() || path.startsWith("/") || path.split("/").includes("..")) {
    return "附件路径非法（不能为空、绝对路径或含 ..）";
  }
  const decodedSize =
    payload.encoding === "base64"
      ? Math.floor((payload.content.length * 3) / 4)
      : new TextEncoder().encode(payload.content).length;
  if (payload.entry_type === "script") {
    const dot = path.lastIndexOf(".");
    const suffix = dot >= 0 ? path.slice(dot).toLowerCase() : "";
    if (!SCRIPT_SUFFIXES.includes(suffix)) {
      return `脚本附件后缀必须是 ${SCRIPT_SUFFIXES.join("/")}：${path}`;
    }
    if (decodedSize > MAX_SCRIPT_BYTES) {
      return `脚本超过 256KB 上限：${path}`;
    }
  } else if (decodedSize > MAX_TEXT_BYTES) {
    return `文本附件超过 1MB 上限：${path}`;
  }
  return null;
}

/** 编辑表单校验；@returns 字段 → 错误文案。 */
export function validateSkillEditForm(form: { description: string }): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!form.description.trim()) errors.description = "description 不能为空（模型依据它决定是否加载）";
  return errors;
}
