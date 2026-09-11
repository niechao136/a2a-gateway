"use client";

import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  Switch,
  TextField,
} from "@mui/material";
import {
  MCP_TRANSPORT_LABELS,
  McpServer,
  McpServerCreatePayload,
  McpTransport,
  adminApi,
} from "@/lib/adminApi";

interface McpServerDialogProps {
  /** 传入则为编辑，否则为新建 */
  initial: McpServer | null;
  existingNames: string[];
  onClose: () => void;
  onSaved: () => void;
}

/** 把多行文本解析为列表（去空行与首尾空格）。 */
function parseLines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** 把「每行 KEY=VALUE」解析为环境变量对象。 */
function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of parseLines(text)) {
    const idx = line.indexOf("=");
    if (idx <= 0) continue;
    out[line.slice(0, idx).trim()] = line.slice(idx + 1);
  }
  return out;
}

function envToText(env: Record<string, string> | undefined): string {
  return Object.entries(env ?? {})
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

/**
 * MCP 服务的新建/编辑弹窗。
 * 由父组件在打开时才挂载，初始值直接用 useState 初始化，无需回填副作用。
 */
export default function McpServerDialog({
  initial,
  existingNames,
  onClose,
  onSaved,
}: McpServerDialogProps) {
  const isEdit = !!initial;

  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [transport, setTransport] = useState<McpTransport>(
    initial?.transport ?? "streamable_http",
  );
  const [url, setUrl] = useState(initial?.url ?? "");
  const [command, setCommand] = useState(initial?.command ?? "");
  const [argsText, setArgsText] = useState((initial?.args ?? []).join("\n"));
  const [envText, setEnvText] = useState(envToText(initial?.env));
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [errors, setErrors] = useState<{ name?: string; url?: string; command?: string }>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isStdio = transport === "stdio";

  const validate = (): boolean => {
    const next: { name?: string; url?: string; command?: string } = {};
    const n = name.trim();
    if (!n) next.name = "请填写名称";
    else if (n !== initial?.name && existingNames.includes(n)) {
      next.name = `名称 '${n}' 已存在`;
    }
    if (isStdio) {
      if (!command.trim()) next.command = "stdio 传输需要填写启动命令";
    } else if (!url.trim()) {
      next.url = "该传输方式需要填写服务 URL";
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleSave = async () => {
    if (!validate()) return;
    setSaving(true);
    setError(null);
    try {
      const payload: McpServerCreatePayload = {
        name: name.trim(),
        description: description.trim(),
        transport,
        // 两种传输的参数互斥，提交时清空另一组，避免脏数据
        url: isStdio ? "" : url.trim(),
        command: isStdio ? command.trim() : "",
        args: isStdio ? parseLines(argsText) : [],
        env: isStdio ? parseEnv(envText) : {},
        enabled,
      };
      if (isEdit && initial) await adminApi.updateMcpServer(initial.id, payload);
      else await adminApi.createMcpServer(payload);
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isEdit ? "编辑 MCP 服务" : "新建 MCP 服务"}</DialogTitle>
      <DialogContent dividers>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2, pt: 1 }}>
          <TextField
            label="名称"
            required
            fullWidth
            size="small"
            value={name}
            onChange={(e) => setName(e.target.value)}
            error={!!errors.name}
            helperText={errors.name}
            placeholder="例如：本地文件服务"
          />

          <FormControl fullWidth size="small">
            <InputLabel id="mcp-transport-label">传输方式</InputLabel>
            <Select
              labelId="mcp-transport-label"
              label="传输方式"
              value={transport}
              onChange={(e) => setTransport(e.target.value as McpTransport)}
            >
              {(Object.keys(MCP_TRANSPORT_LABELS) as McpTransport[]).map((value) => (
                <MenuItem key={value} value={value}>
                  {MCP_TRANSPORT_LABELS[value]}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          {isStdio ? (
            <>
              <TextField
                label="启动命令"
                required
                fullWidth
                size="small"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                error={!!errors.command}
                helperText={errors.command ?? "例如：python 或 npx"}
                placeholder="python"
              />
              <TextField
                label="启动参数（每行一个）"
                fullWidth
                size="small"
                multiline
                minRows={2}
                value={argsText}
                onChange={(e) => setArgsText(e.target.value)}
                placeholder={"-m\nmy_mcp_server"}
              />
              <TextField
                label="环境变量（每行 KEY=VALUE）"
                fullWidth
                size="small"
                multiline
                minRows={2}
                value={envText}
                onChange={(e) => setEnvText(e.target.value)}
                placeholder={"API_KEY=xxx"}
              />
            </>
          ) : (
            <TextField
              label="服务地址"
              required
              fullWidth
              size="small"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              error={!!errors.url}
              helperText={errors.url ?? "例如：http://host:8000/mcp"}
              placeholder="http://host:port/mcp"
            />
          )}

          <TextField
            label="描述"
            fullWidth
            size="small"
            multiline
            minRows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />

          <FormControlLabel
            control={
              <Switch size="small" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            }
            label={enabled ? "启用" : "停用（Agent 将不再解析到该服务）"}
          />

          {error && <Alert severity="error">{error}</Alert>}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>
          取消
        </Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={saving}
          startIcon={saving ? <CircularProgress size={16} color="inherit" /> : null}
        >
          {saving ? "保存中..." : "保存"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
