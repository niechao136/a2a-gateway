# Skill Phase D 实现计划：前端（详情弹窗 + 编辑弹窗 + 脚本试跑面板）

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** Skill 管理页补齐「详情」「编辑」入口：详情弹窗（frontmatter 摘要 + 正文 Markdown 渲染 + 附件表 + 脚本试跑面板）、编辑弹窗（三字段 + allow_scripts + content + 附件全量管理），并把 SKILL.md 上传后的预览/编辑/试跑串成闭环。

**架构：** 表单/附件的校验与组装逻辑抽为 `web/src/lib/skillForm.ts` 纯函数并配 vitest（沿用「校验逻辑先抽纯函数再测」的既定方向）；组件只做状态与展示。附件保存走全量替换（与后端 `SkillUpdate.files` 语义一致）。

**技术栈：** React 19 + MUI 9 + Next 16 App Router；新增依赖 `react-markdown` + `remark-gfm`；vitest。

**规格：** `docs/superpowers/specs/2026-09-21-skill-preview-edit-script-execution-design.md` §5.1/§7/§10

**前置：** Phase A（`Skill.allow_scripts`、`SkillFilePayload`、`isSkillMdFileName` 已就位）、Phase C（`POST /skills/{id}/scripts/run` 已就位）。

## 全局约束

- UI 文案全中文；组件风格对齐 `SkillImportDialog.tsx`（Dialog + 受控状态 + Snackbar 反馈由列表页承担）
- 可测逻辑全部进 `web/src/lib/`（纯函数）并配 vitest；组件自身不做逻辑分支（仅状态转发）
- 附件客户端预检口径与后端一致：脚本后缀 `.py/.sh/.js`、单脚本 ≤ 256KB、单文本 ≤ 1MB（仅作前置提示，后端仍是权威校验）
- `SkillOut.files[].content` 后端全量下发（脚本为 base64），编辑弹窗基于它做全量替换提交
- 不改动公开对话页（`ChatPage` 等）；`npm run build` 必须成功

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `web/package.json` | 修改 | 新增 `react-markdown`、`remark-gfm` |
| `web/src/lib/adminApi.ts` | 修改 | `SkillScriptRunPayload` / `SkillScriptRunResult` 类型 + `runSkillScript` 方法 |
| `web/src/lib/skillUtils.ts` | 修改 | `bytesToBase64`（浏览器/Node 通用分片实现） |
| `web/src/lib/skillForm.ts` | 创建 | 附件组装与校验、表单校验纯函数 |
| `web/src/lib/skillForm.test.ts` | 创建 | 上述纯函数用例 |
| `web/src/components/admin/ScriptRunPanel.tsx` | 创建 | 脚本选择 + argv/stdin/超时 + 结果展示 |
| `web/src/components/admin/SkillDetailDialog.tsx` | 创建 | 详情弹窗（Markdown 正文 + 附件表 + 嵌入试跑面板） |
| `web/src/components/admin/SkillEditDialog.tsx` | 创建 | 编辑弹窗（字段 + 附件管理 + 全量保存） |
| `web/src/app/admin/skills/page.tsx` | 修改 | 列表行加「详情」「编辑」按钮 + 弹窗挂载 |

---

### 任务 1：依赖 + 类型 + API 方法 + 纯函数

**文件：**
- 修改：`web/package.json`、`web/src/lib/adminApi.ts`、`web/src/lib/skillUtils.ts`
- 创建：`web/src/lib/skillForm.ts`
- 测试：`web/src/lib/skillForm.test.ts`

- [ ] **步骤 1：安装依赖**

运行：`cd web; npm install react-markdown remark-gfm`
预期：package.json dependencies 出现两个新包

- [ ] **步骤 2：编写失败的测试**

`web/src/lib/skillForm.test.ts`：

```ts
import { describe, expect, it } from "vitest";
import {
  attachmentFromBytes,
  validateAttachment,
  validateSkillEditForm,
  type SkillFilePayload,
} from "./skillForm";

describe("attachmentFromBytes", () => {
  it("脚本后缀 → entry_type=script + base64 编码", () => {
    const payload = attachmentFromBytes("scripts/gen.py", new Uint8Array([104, 105]));
    expect(payload.entry_type).toBe("script");
    expect(payload.encoding).toBe("base64");
    expect(payload.content).toBe("aGk="); // "hi"
    expect(payload.path).toBe("scripts/gen.py");
  });
  it("非脚本 → entry_type=text + utf-8 原文", () => {
    const payload = attachmentFromBytes("refs/a.md", new TextEncoder().encode("正文"));
    expect(payload.entry_type).toBe("text");
    expect(payload.encoding).toBe("utf-8");
    expect(payload.content).toBe("正文");
  });
});

describe("validateAttachment", () => {
  const base: SkillFilePayload = { path: "a.md", content: "x" };

  it("拒绝空路径与路径穿越", () => {
    expect(validateAttachment({ ...base, path: "" })).toContain("路径");
    expect(validateAttachment({ ...base, path: "../x.md" })).toContain("路径");
  });
  it("拒绝脚本 entry_type 但后缀不在白名单", () => {
    const message = validateAttachment({
      path: "bin/run.exe",
      content: "aGk=",
      entry_type: "script",
      encoding: "base64",
    });
    expect(message).toContain("后缀");
  });
  it("拒绝超限脚本（256KB）", () => {
    const message = validateAttachment({
      path: "big.py",
      content: "aGk=",
      entry_type: "script",
      encoding: "base64",
    });
    expect(message).toBeNull(); // "aGk=" 解码后 2 字节，合法
    const huge = attachmentFromBytes("big.py", new Uint8Array(256 * 1024 + 1));
    expect(validateAttachment(huge)).toContain("256KB");
  });
  it("合法条目返回 null", () => {
    expect(validateAttachment(base)).toBeNull();
    expect(
      validateAttachment({ path: "s.py", content: "aGk=", entry_type: "script", encoding: "base64" }),
    ).toBeNull();
  });
});

describe("validateSkillEditForm", () => {
  it("description 必填", () => {
    const errors = validateSkillEditForm({ description: "   " });
    expect(errors.description).toBeTruthy();
    expect(validateSkillEditForm({ description: "说明" }).description).toBeUndefined();
  });
});
```

- [ ] **步骤 3：运行测试验证失败**

运行：`cd web; npm test`
预期：FAIL，`skillForm` 模块不存在

- [ ] **步骤 4：实现**

`web/src/lib/skillUtils.ts` 尾部追加：

```ts
/** Uint8Array → 纯 base64（分片拼接，避免超大文件一次性构造超长字符串）。 */
export function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  }
  return btoa(binary);
}
```

创建 `web/src/lib/skillForm.ts`：

```ts
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
  return { path: name, content: new TextDecoder().decode(bytes), entry_type: "text", encoding: "utf-8" };
}

/** 附件前置校验；@returns 错误文案（null = 合法）。 */
export function validateAttachment(payload: SkillFilePayload): string | null {
  const path = payload.path ?? "";
  if (!path.trim() || path.startsWith("/") || path.split("/").includes("..")) {
    return "附件路径非法（不能为空、绝对路径或含 ..）";
  }
  const decodedSize =
    payload.encoding === "base64" ? Math.floor((payload.content.length * 3) / 4) : new TextEncoder().encode(payload.content).length;
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
```

`web/src/lib/adminApi.ts`：

1. `SkillUpdatePayload` 附近新增类型：

```ts
export interface SkillScriptRunPayload {
  path: string;
  argv?: string[];
  stdin?: string;
  timeout_s?: number;
}

export interface SkillScriptRunResult {
  exit_code: number | null;
  stdout: string;
  stderr: string;
  truncated: boolean;
  timeout: boolean;
  duration_ms: number;
  error?: string | null;
}
```

2. `adminApi` 对象的 Skill 区（`reviewSkill` 之后）新增方法：

```ts
  runSkillScript(id: number, payload: SkillScriptRunPayload): Promise<SkillScriptRunResult> {
    return request<SkillScriptRunResult>(`/api/admin/skills/${id}/scripts/run`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
```

- [ ] **步骤 5：运行测试验证通过**

运行：`cd web; npm test`
预期：PASS（含 Phase A 的 `skillUtils.test.ts`）

- [ ] **步骤 6：Commit**

```bash
git add web/package.json web/package-lock.json web/src/lib/adminApi.ts web/src/lib/skillUtils.ts web/src/lib/skillForm.ts web/src/lib/skillForm.test.ts
git commit -m "feat(web): Skill 编辑表单纯函数与脚本试跑 API"
```

---

### 任务 2：`ScriptRunPanel` 试跑面板

**文件：**
- 创建：`web/src/components/admin/ScriptRunPanel.tsx`

- [ ] **步骤 1：实现组件**

```tsx
"use client";

import { useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  MenuItem,
  Paper,
  TextField,
  Typography,
} from "@mui/material";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import { ApiError, Skill, SkillScriptRunResult, adminApi } from "@/lib/adminApi";

interface Props {
  skill: Skill;
}

/** 脚本试跑面板（规格 §10.2）：pending 状态也可试跑，辅助审核。 */
export default function ScriptRunPanel({ skill }: Props) {
  const scripts = useMemo(
    () => skill.files.filter((f) => f.entry_type === "script"),
    [skill.files],
  );
  const [scriptPath, setScriptPath] = useState("");
  const [argv, setArgv] = useState("");
  const [stdin, setStdin] = useState("");
  const [timeoutS, setTimeoutS] = useState(30);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SkillScriptRunResult | null>(null);

  if (scripts.length === 0) return null;

  const run = async () => {
    if (!scriptPath) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(
        await adminApi.runSkillScript(skill.id, {
          path: scriptPath,
          argv: argv.trim() ? argv.trim().split(/\s+/) : [],
          stdin,
          timeout_s: timeoutS,
        }),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "试跑失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Paper variant="outlined" sx={{ p: 2, mt: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        脚本试跑（沙箱内执行：无网络、无凭据，仅标准库可用）
      </Typography>
      <Box sx={{ display: "flex", gap: 1.5, flexWrap: "wrap", alignItems: "center" }}>
        <TextField
          select
          size="small"
          label="脚本"
          sx={{ minWidth: 220 }}
          value={scriptPath}
          onChange={(e) => setScriptPath(e.target.value)}
        >
          {scripts.map((f) => (
            <MenuItem key={f.path} value={f.path}>
              {f.path}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          size="small"
          label="argv（空格分隔）"
          sx={{ width: 200 }}
          value={argv}
          onChange={(e) => setArgv(e.target.value)}
        />
        <TextField
          size="small"
          type="number"
          label="超时（秒）"
          sx={{ width: 110 }}
          value={timeoutS}
          onChange={(e) => setTimeoutS(Math.max(1, Math.min(120, Number(e.target.value) || 30)))}
        />
        <Button
          variant="contained"
          startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <PlayArrowIcon />}
          onClick={() => void run()}
          disabled={busy || !scriptPath}
        >
          试跑
        </Button>
      </Box>
      <TextField
        size="small"
        label="stdin（可选）"
        fullWidth
        multiline
        minRows={2}
        margin="normal"
        value={stdin}
        onChange={(e) => setStdin(e.target.value)}
      />
      {error && (
        <Alert severity="error" sx={{ mt: 1 }}>
          {error}
        </Alert>
      )}
      {result && (
        <Box sx={{ mt: 1.5 }}>
          <Typography variant="caption" color="text.secondary">
            exit_code: {result.exit_code ?? "null（超时被杀）"} · 耗时 {result.duration_ms}ms
            {result.timeout && " · 超时"} {result.truncated && " · 输出已截断"}
            {result.error && ` · 错误: ${result.error}`}
          </Typography>
          <TextField
            label="stdout"
            fullWidth
            multiline
            minRows={3}
            margin="normal"
            value={result.stdout}
            InputProps={{ readOnly: true }}
          />
          {result.stderr && (
            <TextField
              label="stderr"
              fullWidth
              multiline
              minRows={2}
              margin="normal"
              value={result.stderr}
              InputProps={{ readOnly: true }}
            />
          )}
        </Box>
      )}
    </Paper>
  );
}
```

- [ ] **步骤 2：构建验证**

运行：`cd web; npm run build`
预期：成功（无类型错误）

- [ ] **步骤 3：Commit**

```bash
git add web/src/components/admin/ScriptRunPanel.tsx
git commit -m "feat(web): 脚本试跑面板"
```

---

### 任务 3：`SkillDetailDialog` 详情弹窗

**文件：**
- 创建：`web/src/components/admin/SkillDetailDialog.tsx`

- [ ] **步骤 1：实现组件**

```tsx
"use client";

import { useState } from "react";
import {
  Chip,
  Dialog,
  DialogContent,
  DialogTitle,
  Divider,
  Typography,
} from "@mui/material";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Skill } from "@/lib/adminApi";
import { formatBytes } from "@/lib/skillUtils";
import ScriptRunPanel from "./ScriptRunPanel";

interface Props {
  skill: Skill;
  open: boolean;
  onClose: () => void;
}

/** Skill 详情弹窗（规格 §10.2）：摘要 + Markdown 正文 + 附件表 + 试跑面板。 */
export default function SkillDetailDialog({ skill, open, onClose }: Props) {
  const [previewPath, setPreviewPath] = useState<string | null>(null);

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>
        {skill.name}
        <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
          {skill.load_mode === "always" ? "常驻" : "按需"} · {formatBytes(skill.size_bytes)} ·{" "}
          {skill.file_count} 附件 · 来源 {skill.source}
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" sx={{ mb: 1 }}>
          {skill.description}
        </Typography>
        <Chip size="small" label={skill.review_status} sx={{ mr: 0.5 }} />
        <Chip size="small" label={skill.enabled ? "启用" : "停用"} sx={{ mr: 0.5 }} />
        <Chip
          size="small"
          color={skill.allow_scripts ? "warning" : "default"}
          label={skill.allow_scripts ? "允许脚本执行" : "禁止脚本执行"}
        />
        <Divider sx={{ my: 1.5 }} />
        <Box sx={{ "& pre": { bgcolor: "grey.100", p: 1, overflowX: "auto", fontSize: 13 } }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{skill.content}</ReactMarkdown>
        </Box>
        <Divider sx={{ my: 1.5 }} />
        <Typography variant="subtitle2" gutterBottom>
          附件
        </Typography>
        {skill.files.length === 0 ? (
          <Typography variant="caption" color="text.secondary">
            （无附件）
          </Typography>
        ) : (
          skill.files.map((f) => (
            <Box key={f.path} sx={{ py: 0.5 }}>
              <Chip
                size="small"
                color={f.entry_type === "script" ? "warning" : "default"}
                label={f.entry_type === "script" ? "脚本" : "文本"}
                sx={{ mr: 1 }}
              />
              <Typography component="span" variant="body2">
                {f.path}
              </Typography>
              <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                {formatBytes(f.size)}
              </Typography>
              {f.entry_type !== "script" && (
                <Typography
                  component="span"
                  variant="caption"
                  color="primary"
                  sx={{ ml: 1, cursor: "pointer" }}
                  onClick={() => setPreviewPath(previewPath === f.path ? null : f.path)}
                >
                  {previewPath === f.path ? "收起" : "预览"}
                </Typography>
              )}
              {previewPath === f.path && (
                <Box sx={{ "& pre": { bgcolor: "grey.100", p: 1, overflowX: "auto", fontSize: 13, whiteSpace: "pre-wrap" } }}>
                  <pre>{f.content ?? ""}</pre>
                </Box>
              )}
            </Box>
          ))
        )}
        <ScriptRunPanel skill={skill} />
      </DialogContent>
    </Dialog>
  );
}
```

注意：`Box` 需要 `import { Box } from "@mui/material"`（与上面 Chip 等合并到同一 import）。

- [ ] **步骤 2：构建验证**

运行：`cd web; npm run build`
预期：成功

- [ ] **步骤 3：Commit**

```bash
git add web/src/components/admin/SkillDetailDialog.tsx
git commit -m "feat(web): Skill 详情弹窗（Markdown 正文 + 附件 + 试跑）"
```

---

### 任务 4：`SkillEditDialog` 编辑弹窗

**文件：**
- 创建：`web/src/components/admin/SkillEditDialog.tsx`

- [ ] **步骤 1：实现组件**

```tsx
"use client";

import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  MenuItem,
  TextField,
  Typography,
} from "@mui/material";
import { ApiError, Skill, SkillFilePayload, SkillLoadMode, adminApi } from "@/lib/adminApi";
import { attachmentFromBytes, validateAttachment, validateSkillEditForm } from "@/lib/skillForm";
import { formatBytes } from "@/lib/skillUtils";

interface Props {
  skill: Skill;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}

/** Skill 编辑弹窗（规格 §10.2）：三字段 + allow_scripts + content + 附件全量管理。 */
export default function SkillEditDialog({ skill, open, onClose, onSaved }: Props) {
  const [description, setDescription] = useState(skill.description);
  const [loadMode, setLoadMode] = useState<SkillLoadMode>(
    skill.load_mode === "always" ? "always" : "on_demand",
  );
  const [enabled, setEnabled] = useState(skill.enabled);
  const [allowScripts, setAllowScripts] = useState(skill.allow_scripts);
  const [content, setContent] = useState(skill.content);
  const [files, setFiles] = useState<SkillFilePayload[]>(
    skill.files.map((f) => ({
      path: f.path,
      content: f.content ?? "",
      entry_type: f.entry_type ?? "text",
      encoding: f.encoding ?? "utf-8",
    })),
  );
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const addFiles = async (fileList: FileList | null) => {
    if (!fileList?.length) return;
    const next = [...files];
    for (const file of Array.from(fileList)) {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const payload = attachmentFromBytes(file.name, bytes);
      const message = validateAttachment(payload);
      if (message) {
        setError(message);
        return;
      }
      if (next.some((f) => f.path === payload.path)) {
        setError(`附件路径重复：${payload.path}`);
        return;
      }
      next.push(payload);
    }
    setError(null);
    setFiles(next);
  };

  const save = async () => {
    const errors = validateSkillEditForm({ description });
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;
    const contentChanged =
      description !== skill.description ||
      content !== skill.content ||
      JSON.stringify(files) !== JSON.stringify(skill.files.map((f) => ({ path: f.path, content: f.content ?? "", entry_type: f.entry_type ?? "text", encoding: f.encoding ?? "utf-8" })));
    if (
      contentChanged &&
      !window.confirm("正文或附件已变更：保存后审核状态将重置为 pending，确认保存？")
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await adminApi.updateSkill(skill.id, {
        description,
        content,
        load_mode: loadMode,
        enabled,
        allow_scripts: allowScripts,
        files,
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>编辑 Skill：{skill.name}</DialogTitle>
      <DialogContent>
        <TextField
          label="description（模型据此决定是否加载）"
          fullWidth
          margin="normal"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          error={!!fieldErrors.description}
          helperText={fieldErrors.description}
        />
        <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap", alignItems: "center" }}>
          <TextField
            select
            label="加载模式"
            sx={{ width: 160 }}
            value={loadMode}
            onChange={(e) => setLoadMode(e.target.value as SkillLoadMode)}
          >
            <MenuItem value="on_demand">按需（on_demand）</MenuItem>
            <MenuItem value="always">常驻（always）</MenuItem>
          </TextField>
          <FormControlLabel
            control={<Checkbox checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />}
            label="启用"
          />
          <FormControlLabel
            control={
              <Checkbox checked={allowScripts} onChange={(e) => setAllowScripts(e.target.checked)} />
            }
            label="允许沙箱执行捆绑脚本（审核决策项）"
          />
        </Box>
        <TextField
          label="正文（SKILL.md 剥离 frontmatter 后的 Markdown）"
          fullWidth
          multiline
          minRows={10}
          margin="normal"
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
        <Typography variant="subtitle2" sx={{ mt: 1 }}>
          附件（全量替换；脚本 .py/.sh/.js ≤ 256KB，文本 ≤ 1MB）
        </Typography>
        {files.map((f, idx) => (
          <Box key={f.path} sx={{ display: "flex", alignItems: "center", gap: 1, py: 0.5 }}>
            <Chip
              size="small"
              color={f.entry_type === "script" ? "warning" : "default"}
              label={f.entry_type === "script" ? "脚本" : "文本"}
            />
            <Typography variant="body2" sx={{ flex: 1 }}>
              {f.path}
              <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                {formatBytes(f.entry_type === "script" ? Math.floor((f.content.length * 3) / 4) : new TextEncoder().encode(f.content).length)}
              </Typography>
            </Typography>
            <Button
              size="small"
              color="error"
              onClick={() => setFiles(files.filter((_, i) => i !== idx))}
            >
              移除
            </Button>
          </Box>
        ))}
        <Button variant="outlined" component="label" sx={{ mt: 1 }}>
          添加附件
          <input
            type="file"
            hidden
            multiple
            accept=".md,.txt,.csv,.json,.yaml,.yml,.py,.sh,.js"
            onChange={(e) => {
              void addFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </Button>
        {error && (
          <Alert severity="error" sx={{ mt: 1.5 }}>
            {error}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>取消</Button>
        <Button
          variant="contained"
          onClick={() => void save()}
          disabled={busy}
          startIcon={busy ? <CircularProgress size={16} color="inherit" /> : null}
        >
          保存
        </Button>
      </DialogActions>
    </Dialog>
  );
}
```

注意：`Chip` 需并入 MUI import。

- [ ] **步骤 2：构建验证**

运行：`cd web; npm test && npm run build`
预期：全绿 + build 成功

- [ ] **步骤 3：Commit**

```bash
git add web/src/components/admin/SkillEditDialog.tsx
git commit -m "feat(web): Skill 编辑弹窗（附件全量管理 + 审核重置提示）"
```

---

### 任务 5：列表页接线 + 全量回归

**文件：**
- 修改：`web/src/app/admin/skills/page.tsx`

- [ ] **步骤 1：接线**

1. import 区补：

```tsx
import VisibilityIcon from "@mui/icons-material/Visibility";
import EditIcon from "@mui/icons-material/Edit";
import SkillDetailDialog from "@/components/admin/SkillDetailDialog";
import SkillEditDialog from "@/components/admin/SkillEditDialog";
```

2. 组件状态（`importOpen` 之后）补：

```tsx
  const [detailSkill, setDetailSkill] = useState<Skill | null>(null);
  const [editSkill, setEditSkill] = useState<Skill | null>(null);
```

3. 操作列（「通过」按钮之前）加两个按钮：

```tsx
                    <Button
                      size="small"
                      startIcon={<VisibilityIcon />}
                      onClick={() => setDetailSkill(skill)}
                    >
                      详情
                    </Button>
                    <Button
                      size="small"
                      startIcon={<EditIcon />}
                      onClick={() => setEditSkill(skill)}
                      disabled={busyId === skill.id}
                    >
                      编辑
                    </Button>
```

（若嫌图标按钮过宽可去掉 startIcon，仅保留文字；两种实现任选其一并保持一致。）

4. 弹窗挂载（`SkillImportDialog` 条件渲染之后）：

```tsx
      {detailSkill && (
        <SkillDetailDialog skill={detailSkill} open onClose={() => setDetailSkill(null)} />
      )}
      {editSkill && (
        <SkillEditDialog
          skill={editSkill}
          open
          onClose={() => setEditSkill(null)}
          onSaved={() => {
            setToast("已保存（内容变更时审核状态已重置为 pending）");
            void load();
          }}
        />
      )}
```

- [ ] **步骤 2：全量回归**

运行：`cd web; npm test && npm run build`
预期：vitest 全绿 + build 成功

- [ ] **步骤 3：真机手检（对照规格 §15 验收清单）**

启动 dev 环境（`docker compose up -d` + `cd web && npm run dev`），核对：
1. 列表「详情」→ Markdown 正文渲染、文本附件可预览、脚本带徽标；
2. 有脚本且沙箱起动的环境下试跑出 stdout（沙箱未起时展示 502 错误文案）；
3. 「编辑」改 description → 保存弹确认 → 列表刷新后状态回到 pending；
4. 添加超限脚本 → 前端前置拦截文案；后端 400 兜底文案可见。

- [ ] **步骤 4：Commit**

```bash
git add web/src/app/admin/skills/page.tsx
git commit -m "feat(web): Skill 列表接入详情与编辑"
```

---

## 自检记录

- 规格覆盖：§10.1 react-markdown → 任务 1；§10.2 详情弹窗（frontmatter 摘要/正文渲染/附件表/试跑入口）、编辑弹窗（四字段 + 附件管理 + 全量提交 + pending 提示）、列表接线 → 任务 2/3/4/5；§7 试跑交互 → 任务 2；§5.1 SKILL.md 上传属 Phase A 已覆盖（本计划不动导入弹窗）
- 类型一致性：`SkillScriptRunResult`（任务 1）↔ `ScriptRunPanel`（任务 2）↔ 后端 `SkillScriptRunOut`（Phase C 任务 3）；`SkillFilePayload`（Phase A 任务 7 定义，任务 1/4 消费）；`attachmentFromBytes` / `validateAttachment` / `validateSkillEditForm`（任务 1 定义，任务 4 消费）
- 组件任务以 `npm run build` + 真机手检为交付验证（与项目现有「vitest 测 lib 纯函数」口径一致）；无占位符
