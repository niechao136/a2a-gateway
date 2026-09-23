/**
 * Skill 编辑表单的附件组装、路径规范化与校验（纯函数）。
 * 口径与后端一致：脚本后缀白名单、单脚本 256KB；这里只做前置提示，后端是权威校验。
 * 附件 path 是技能包内相对路径，支持子目录（references/a.md）；文本附件不设后缀白名单，
 * 与后端 _is_probably_text 同口径按 UTF-8 可解码判定。
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
/** 单个文本附件上限（与后端 MAX_FILE_BYTES 一致）：超过此值不必读进内存。 */
export const MAX_TEXT_BYTES = 1024 * 1024;

/** 单 skill 附件数上限（与后端 MAX_FILES 一致）。 */
export const MAX_FILES = 100;
/** 单 skill 附件总量上限（与后端 MAX_SKILL_BYTES 一致）。 */
export const MAX_SKILL_BYTES = 4 * 1024 * 1024;

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

/**
 * 附件路径规范化：反斜杠转正斜杠、折叠重复斜杠、去 ./ 前缀、trim、去尾斜杠。
 *
 * 只做不会掩盖非法性的清理 —— 前导 `/` 与 `..` 一律原样保留，交给
 * validateAttachmentPath 拒绝。若在这里把 `/abs` 或 `a/../b` 顺手"修好"，
 * 用户就再也看不到自己填错了什么。
 */
export function normalizeAttachmentPath(raw: string): string {
  let path = (raw ?? "").replace(/\\/g, "/").trim();
  path = path.replace(/\/{2,}/g, "/");
  path = path.replace(/^(?:\.\/)+/, "");
  path = path.replace(/\/+$/, "");
  return path === "." ? "" : path;
}

/** 附件路径合法性（与后端 _assert_safe_rel_path 同口径）；@returns 错误文案（null = 合法）。 */
export function validateAttachmentPath(path: string): string | null {
  const value = (path ?? "").trim();
  if (!value || value === "." || value.startsWith("/") || value.split("/").includes("..")) {
    return "附件路径非法（不能为空、绝对路径或含 ..）";
  }
  // 后端 posixpath.normpath("references/") 会吃掉尾斜杠，静默生成名为 references 的无后缀条目
  if (value.endsWith("/")) return "附件路径不能以 / 结尾";
  return null;
}

/**
 * webkitRelativePath → 包内相对路径：剥掉选中的顶层目录名。
 *
 * 用户选中的那个目录就是技能包根（`my-skill/references/a.md` → `references/a.md`），
 * 否则包名会被当成第一层子目录写进 path。
 */
export function stripTopDir(relPath: string): string {
  const path = normalizeAttachmentPath(relPath);
  const slash = path.indexOf("/");
  return slash < 0 ? path : path.slice(slash + 1);
}

/** 附件前置校验；@returns 错误文案（null = 合法）。 */
export function validateAttachment(payload: SkillFilePayload): string | null {
  const path = payload.path ?? "";
  const pathError = validateAttachmentPath(path);
  if (pathError) return pathError;
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

// ---------------------------------------------------------------------------
// 整目录上传：目录内容 → 候选清单（纯函数，便于测试）
// ---------------------------------------------------------------------------

/** 目录读取后的原始条目：path 应已剥掉顶层目录名。 */
export interface DirEntryInput {
  path: string;
  /** 原始字节；超限文件不必读进内存，此时给 size 即可。 */
  bytes?: Uint8Array;
  /** 原始字节数；省略时按 bytes.length 计算。 */
  size?: number;
}

/** 单个候选文件的筛选结果。 */
export interface DirCandidate {
  /** 规范化后的包内相对路径（展示用）。 */
  path: string;
  /** 原始字节数。 */
  size: number;
  /** 可直接入附件列表的负载；skipReason 非空时为 null。 */
  payload: SkillFilePayload | null;
  /** 不可用原因（SKILL.md / 非文本 / 超限 / 路径非法）；null = 可用。 */
  skipReason: string | null;
  /** 与现有附件路径重复（UI 默认不勾选）。 */
  duplicate: boolean;
}

/** 末尾名是否为 SKILL.md（大小写不敏感；其正文由编辑弹窗的 content 字段单独维护）。 */
function isSkillMdPath(path: string): boolean {
  return path.slice(path.lastIndexOf("/") + 1).toLowerCase() === "skill.md";
}

/**
 * 二进制嗅探：与后端 _is_probably_text 同口径（含 NUL 或非 UTF-8 即可疑）。
 *
 * 不按扩展名白名单放行 —— 后端对文本附件只要求 UTF-8 可解码，
 * 按后缀白名单会让 .toml / .html / LICENSE 这类合法文本附件被无谓丢弃。
 */
function decodeUtf8Text(bytes: Uint8Array): string | null {
  if (bytes.indexOf(0) !== -1) return null;
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

/**
 * 目录内容 → 候选清单：逐条给出可用负载或跳过原因。
 *
 * 跳过项不阻断同批其余文件（目录里混一张图片不该让整批失败）；
 * 与已有附件重名的项保留 payload 但标 duplicate，交由 UI 默认取消勾选 ——
 * 因为编辑弹窗的既有语义是"重复路径即报错中断整批"。
 */
export function collectDirCandidates(
  entries: readonly DirEntryInput[],
  existingPaths: readonly string[] = [],
): DirCandidate[] {
  const existing = new Set(existingPaths);
  return entries.map(({ path, bytes, size: declaredSize }) => {
    const size = declaredSize ?? bytes?.length ?? 0;
    const normalized = normalizeAttachmentPath(path);
    const base: DirCandidate = {
      path: normalized || path,
      size,
      payload: null,
      skipReason: null,
      duplicate: false,
    };
    if (isSkillMdPath(normalized)) {
      return { ...base, skipReason: "SKILL.md 的正文由上方内容字段维护，不作为附件" };
    }
    const pathError = validateAttachmentPath(normalized);
    if (pathError) return { ...base, skipReason: pathError };
    if (isScriptName(normalized)) {
      if (!bytes || size > MAX_SCRIPT_BYTES) {
        return { ...base, skipReason: `脚本超过 256KB 上限：${normalized}` };
      }
      return {
        ...base,
        payload: {
          path: normalized,
          content: bytesToBase64(bytes),
          entry_type: "script",
          encoding: "base64",
        },
        duplicate: existing.has(normalized),
      };
    }
    if (!bytes || size > MAX_TEXT_BYTES) {
      return { ...base, skipReason: `文本附件超过 1MB 上限：${normalized}` };
    }
    const text = decodeUtf8Text(bytes);
    if (text === null) {
      return { ...base, skipReason: `不是可读的 UTF-8 文本（二进制文件）：${normalized}` };
    }
    return {
      ...base,
      payload: { path: normalized, content: text, entry_type: "text", encoding: "utf-8" },
      duplicate: existing.has(normalized),
    };
  });
}

/** 编辑表单校验；@returns 字段 → 错误文案。 */
export function validateSkillEditForm(form: { description: string }): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!form.description.trim()) errors.description = "description 不能为空（模型依据它决定是否加载）";
  return errors;
}
