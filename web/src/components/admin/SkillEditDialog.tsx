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

/** 附件 payload 大小（字节）：脚本按 base64 反推，文本按 UTF-8 计。 */
function payloadBytes(f: SkillFilePayload): number {
  return f.encoding === "base64"
    ? Math.floor((f.content.length * 3) / 4)
    : new TextEncoder().encode(f.content).length;
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
    const baseline = skill.files.map((f) => ({
      path: f.path,
      content: f.content ?? "",
      entry_type: f.entry_type ?? "text",
      encoding: f.encoding ?? "utf-8",
    }));
    const contentChanged =
      description !== skill.description ||
      content !== skill.content ||
      JSON.stringify(files) !== JSON.stringify(baseline);
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
                {formatBytes(payloadBytes(f))}
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
