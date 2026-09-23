"use client";

import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  FormControlLabel,
  MenuItem,
  TextField,
  Typography,
} from "@mui/material";
import { ApiError, Skill, SkillFilePayload, SkillLoadMode, adminApi } from "@/lib/adminApi";
import {
  attachmentFromBytes,
  normalizeAttachmentPath,
  validateAttachment,
  validateAttachmentPath,
  validateSkillEditForm,
} from "@/lib/skillForm";
import { base64ToUtf8, formatBytes, utf8ToBase64 } from "@/lib/skillUtils";
import DialogTitleBar from "./DialogTitleBar";
import SkillDirPickerDialog from "./SkillDirPickerDialog";
import { useIsMobile } from "@/lib/breakpoints";

/**
 * 附件行 = SkillFilePayload + 组件内部稳定 id（id 不进提交 payload）。
 *
 * path 现在是可编辑的，不能继续拿它当 React key 与展开态标识：
 * 每敲一个字符 key 就会变，React 会卸载重建输入框、光标直接丢失。
 */
interface Row extends SkillFilePayload {
  id: string;
}

let rowSeq = 0;
/** 行标识：module 级自增，保证同一页面生命周期内唯一即可（无需 crypto，避免非安全上下文报错）。 */
const nextRowId = () => `row-${(rowSeq += 1)}`;

/** 附件行 → 提交 payload（剥掉 id，键顺序与基线比较保持一致）。 */
const toPayload = (row: Row): SkillFilePayload => ({
  path: row.path,
  content: row.content,
  entry_type: row.entry_type,
  encoding: row.encoding,
});

interface Props {
  skill: Skill;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}

/** 附件 payload 大小（字节）：脚本按 base64 反推，文本按 UTF-8 计。 */
function payloadBytes(f: SkillFilePayload): number {
  return f.encoding === "base64"
    ? Math.floor((f.content.length * 3) / 4)
    : new TextEncoder().encode(f.content).length;
}

/** 附件在编辑器中的展示文本：脚本（base64 存储）解码为 UTF-8，文本按原文。 */
function attachmentDisplayText(f: SkillFilePayload): string {
  return f.encoding === "base64" ? base64ToUtf8(f.content) : f.content;
}

/** Skill 编辑弹窗（规格 §10.2）：三字段 + allow_scripts + content + 附件全量管理。 */
export default function SkillEditDialog({ skill, open, onClose, onSaved }: Props) {
  const isMobile = useIsMobile();
  const [description, setDescription] = useState(skill.description);
  const [loadMode, setLoadMode] = useState<SkillLoadMode>(
    skill.load_mode === "always" ? "always" : "on_demand",
  );
  const [enabled, setEnabled] = useState(skill.enabled);
  const [allowScripts, setAllowScripts] = useState(skill.allow_scripts);
  const [content, setContent] = useState(skill.content);
  const [rows, setRows] = useState<Row[]>(() =>
    skill.files.map((f) => ({
      id: nextRowId(),
      path: f.path,
      content: f.content ?? "",
      entry_type: f.entry_type ?? "text",
      encoding: f.encoding ?? "utf-8",
    })),
  );
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** 当前展开内联编辑框的行 id（null = 全部收起）。 */
  const [editingId, setEditingId] = useState<string | null>(null);
  const [dirPickerOpen, setDirPickerOpen] = useState(false);

  /** 路径是否已被某个附件行占用（exceptId 用于跳过自己那一行）。 */
  const isDupPath = (path: string, exceptId?: string) =>
    rows.some((r) => r.id !== exceptId && r.path === path);

  /** 把一批 payload 追加进附件列表；重名则报错并放弃整批（保持既有语义）。 */
  const appendPayloads = (payloads: SkillFilePayload[]) => {
    const dup = payloads.find((p) => isDupPath(p.path));
    if (dup) {
      setError(`附件路径重复：${dup.path}`);
      return;
    }
    setError(null);
    setRows([...rows, ...payloads.map((p) => ({ ...p, id: nextRowId() }))]);
  };

  const addFiles = async (fileList: FileList | null) => {
    if (!fileList?.length) return;
    const payloads: SkillFilePayload[] = [];
    for (const file of Array.from(fileList)) {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const payload = attachmentFromBytes(file.name, bytes);
      const message = validateAttachment(payload);
      if (message) {
        setError(message);
        return;
      }
      if (payloads.some((p) => p.path === payload.path)) {
        setError(`附件路径重复：${payload.path}`);
        return;
      }
      payloads.push(payload);
    }
    appendPayloads(payloads);
  };

  /** 目录勾选面板回传的附件（面板已消解重名，这里只做防御性校验）。 */
  const addFromDirPicker = (payloads: SkillFilePayload[]) => {
    const invalid = payloads.map((p) => validateAttachment(p)).find((m) => m !== null);
    if (invalid) {
      setError(invalid);
      return;
    }
    appendPayloads(payloads);
  };

  /** 编辑单个附件内容：脚本回写时重新编码 base64，文本直接存原文。 */
  const updateFileContent = (id: string, text: string) => {
    setRows((prev) =>
      prev.map((r) =>
        r.id === id ? { ...r, content: r.encoding === "base64" ? utf8ToBase64(text) : text } : r,
      ),
    );
  };

  /** 输入路径（原样存，便于连续输入）；规范化与重名判定放在失焦与保存时。 */
  const setRawPath = (id: string, path: string) => {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, path } : r)));
  };

  /** 失焦时规范化路径（`./a.md` → `a.md`、`a//b.md` → `a/b.md`）。 */
  const normalizeRowPath = (id: string) => {
    setRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, path: normalizeAttachmentPath(r.path) } : r)),
    );
  };

  const removeRow = (id: string) => {
    setRows((prev) => prev.filter((r) => r.id !== id));
    if (editingId === id) setEditingId(null);
  };

  const save = async () => {
    const errors = validateSkillEditForm({ description });
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;
    // 规范化在此兜底：用户可能没触发失焦就点了保存，不能依赖 onBlur 的时序
    const payloads = rows.map((r) => toPayload({ ...r, path: normalizeAttachmentPath(r.path) }));
    // 提交前对附件再做一轮前置校验（内联编辑可能把内容改到超限）
    const invalid = payloads.map((p) => validateAttachment(p)).find((m) => m !== null);
    if (invalid) {
      setError(invalid);
      return;
    }
    // 后端 normalize_file_entries 不查列表内重名，重复 path 会被原样落库，这里必须拦住
    const dup = payloads.find((p, i) => payloads.some((q, j) => i !== j && q.path === p.path));
    if (dup) {
      setError(`附件路径重复：${dup.path}`);
      return;
    }
    const baseline = skill.files.map((f) => ({
      path: f.path,
      content: f.content ?? "",
      entry_type: f.entry_type ?? "text",
      encoding: f.encoding ?? "utf-8",
    }));
    const contentChanged =
      description !== skill.description ||
      content !== skill.content ||
      JSON.stringify(payloads) !== JSON.stringify(baseline);
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
        files: payloads,
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
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md" fullScreen={isMobile}>
      <DialogTitleBar title={`编辑 Skill：${skill.name}`} onClose={onClose} />
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
            sx={{ width: 180 }}
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
          附件（路径可改，按技能包内相对路径填写，如 references/a.md；保存时全量替换；脚本
          .py/.sh/.js ≤ 256KB，文本 ≤ 1MB）
        </Typography>
        {rows.map((row) => {
          const editing = editingId === row.id;
          const pathMessage = editing
            ? (validateAttachmentPath(row.path) ??
              (isDupPath(row.path, row.id) ? `附件路径重复：${row.path}` : null))
            : null;
          // 路径错误优先展示在路径输入框上，内容框只承接体积类错误，避免同一处报两遍
          const contentMessage = editing && !pathMessage ? validateAttachment(row) : null;
          return (
            <Box key={row.id} sx={{ py: 0.5, borderBottom: "1px dashed divider" }}>
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <Chip
                  size="small"
                  color={row.entry_type === "script" ? "warning" : "default"}
                  label={row.entry_type === "script" ? "脚本" : "文本"}
                />
                <Typography variant="body2" sx={{ flex: 1, wordBreak: "break-all" }}>
                  {row.path}
                  <Typography
                    component="span"
                    variant="caption"
                    color="text.secondary"
                    sx={{ ml: 1 }}
                  >
                    {formatBytes(payloadBytes(row))}
                  </Typography>
                </Typography>
                <Button size="small" onClick={() => setEditingId(editing ? null : row.id)}>
                  {editing ? "收起" : "编辑"}
                </Button>
                <Button size="small" color="error" onClick={() => removeRow(row.id)}>
                  移除
                </Button>
              </Box>
              {editing && (
                <>
                  <TextField
                    label="路径（技能包内相对路径，可含子目录）"
                    fullWidth
                    margin="normal"
                    value={row.path}
                    onChange={(e) => setRawPath(row.id, e.target.value)}
                    onBlur={() => normalizeRowPath(row.id)}
                    error={!!pathMessage}
                    helperText={
                      pathMessage ?? "如 references/a.md、scripts/run.py；不能为空、绝对路径或含 .."
                    }
                  />
                  <TextField
                    label={`附件内容（${row.entry_type === "script" ? "脚本，保存时按 base64 编码" : "文本"}）`}
                    fullWidth
                    multiline
                    minRows={6}
                    maxRows={16}
                    margin="normal"
                    value={attachmentDisplayText(row)}
                    onChange={(e) => updateFileContent(row.id, e.target.value)}
                    error={!!contentMessage}
                    helperText={contentMessage ?? undefined}
                  />
                </>
              )}
            </Box>
          );
        })}
        <Box sx={{ display: "flex", gap: 1, mt: 1, flexWrap: "wrap" }}>
          <Button variant="outlined" component="label">
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
          <Button variant="outlined" onClick={() => setDirPickerOpen(true)}>
            从目录添加（保留层级）
          </Button>
        </Box>
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
      {/* MUI Dialog 会 portal 到 body，嵌套在此处的 DOM 位置与外层弹窗无关 */}
      <SkillDirPickerDialog
        existingPaths={rows.map((r) => normalizeAttachmentPath(r.path))}
        existingSizeBytes={rows.reduce((sum, r) => sum + payloadBytes(r), 0)}
        open={dirPickerOpen}
        onClose={() => setDirPickerOpen(false)}
        onAdd={addFromDirPicker}
      />
    </Dialog>
  );
}
