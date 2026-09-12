"use client";

import { useState } from "react";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import {
  AUTH_NAME_LABELS,
  AUTH_NAME_PLACEHOLDERS,
  AUTH_TYPE_LABELS,
  AuthType,
  ManualMcpServerInput,
  McpTransport,
  MCP_TRANSPORT_LABELS,
} from "@/lib/adminApi";

interface ManualMcpBindingProps {
  value: ManualMcpServerInput[];
  onChange: (value: ManualMcpServerInput[]) => void;
}

/** 手动绑定的 MCP 服务（无需在「MCP 管理」注册，可与勾选并存）。 */
export default function ManualMcpBinding({ value, onChange }: ManualMcpBindingProps) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editIndex, setEditIndex] = useState<number | null>(null);

  const removeRow = (index: number) => onChange(value.filter((_, i) => i !== index));

  const openCreate = () => {
    setEditIndex(null);
    setDialogOpen(true);
  };

  const openEdit = (index: number) => {
    setEditIndex(index);
    setDialogOpen(true);
  };

  return (
    <Box sx={{ mt: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Typography variant="body2">手动绑定（未注册服务）</Typography>
        <Button size="small" startIcon={<AddIcon />} onClick={openCreate}>
          添加
        </Button>
      </Box>
      <Typography variant="caption" color="text.secondary">
        直接填写连接信息即可，无需先在「MCP 管理」登记；说明会帮助模型判断何时使用该服务。
      </Typography>

      {value.length === 0 ? (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
          暂无手动绑定的服务
        </Typography>
      ) : (
        <Stack spacing={0.5} sx={{ mt: 1 }}>
          {value.map((item, index) => (
            <Stack key={index} direction="row" spacing={1} sx={{ alignItems: "center" }}>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="body2">
                  {item.name}
                  <Chip
                    size="small"
                    label={MCP_TRANSPORT_LABELS[item.transport] ?? item.transport}
                    sx={{ ml: 1, height: 18 }}
                  />
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>
                  {item.transport === "stdio"
                    ? `${item.command ?? ""} ${(item.args ?? []).join(" ")}`.trim()
                    : item.url ?? ""}
                </Typography>
              </Box>
              <Tooltip title="编辑">
                <IconButton size="small" onClick={() => openEdit(index)}>
                  <EditOutlinedIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title="移除">
                <IconButton size="small" color="error" onClick={() => removeRow(index)}>
                  <DeleteOutlinedIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Stack>
          ))}
        </Stack>
      )}

      {dialogOpen && (
        <ManualMcpDialog
          initial={editIndex !== null ? value[editIndex] : null}
          existingNames={value
            .filter((_, i) => i !== editIndex)
            .map((item) => item.name)}
          onClose={() => setDialogOpen(false)}
          onSave={(item) => {
            if (editIndex !== null) {
              onChange(value.map((v, i) => (i === editIndex ? item : v)));
            } else {
              onChange([...value, item]);
            }
            setDialogOpen(false);
          }}
        />
      )}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// 单个手动 MCP 服务的编辑弹窗
// ---------------------------------------------------------------------------
interface ManualMcpDialogProps {
  initial: ManualMcpServerInput | null;
  existingNames: string[];
  onClose: () => void;
  onSave: (item: ManualMcpServerInput) => void;
}

function parseLines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

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

function ManualMcpDialog({ initial, existingNames, onClose, onSave }: ManualMcpDialogProps) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [transport, setTransport] = useState<McpTransport>(
    initial?.transport ?? "streamable_http",
  );
  const [url, setUrl] = useState(initial?.url ?? "");
  const [command, setCommand] = useState(initial?.command ?? "");
  const [argsText, setArgsText] = useState((initial?.args ?? []).join("\n"));
  const [envText, setEnvText] = useState(envToText(initial?.env));
  const [token, setToken] = useState(initial?.token ?? "");
  const [authType, setAuthType] = useState<AuthType>(initial?.auth_type ?? "bearer");
  const [authName, setAuthName] = useState(initial?.auth_name ?? "");
  const [errors, setErrors] = useState<{
    name?: string;
    url?: string;
    command?: string;
    token?: string;
    authName?: string;
  }>({});

  const isStdio = transport === "stdio";
  const authNameLabel = AUTH_NAME_LABELS[authType];
  const needsAuthName = !!authNameLabel;

  const validate = (): boolean => {
    const next: typeof errors = {};
    const n = name.trim();
    if (!n) next.name = "请填写名称";
    else if (existingNames.includes(n)) next.name = `名称 '${n}' 已存在`;
    if (isStdio) {
      if (!command.trim()) next.command = "stdio 传输需要填写启动命令";
    } else if (!url.trim()) {
      next.url = "该传输方式需要填写服务 URL";
    }
    if (authType !== "none" && !token.trim()) {
      next.token = `${AUTH_TYPE_LABELS[authType]} 需要填写密钥`;
    } else if (needsAuthName && !authName.trim()) {
      next.authName = `请填写${authNameLabel}`;
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleSave = () => {
    if (!validate()) return;
    onSave({
      name: name.trim(),
      description: description.trim(),
      transport,
      url: isStdio ? "" : url.trim(),
      command: isStdio ? command.trim() : "",
      args: isStdio ? parseLines(argsText) : [],
      env: isStdio ? parseEnv(envText) : {},
      token: token.trim(),
      auth_type: authType,
      auth_name: needsAuthName ? authName.trim() : "",
    });
  };

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{initial ? "编辑手动绑定的 MCP 服务" : "手动绑定 MCP 服务"}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 0.5 }}>
          <TextField
            size="small"
            required
            label="名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
            error={!!errors.name}
            helperText={errors.name ?? "用于生成工具说明，本 Agent 内建议唯一"}
          />
          <TextField
            size="small"
            label="说明（可选）"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="该服务提供什么能力"
          />
          <TextField
            size="small"
            select
            label="传输方式"
            value={transport}
            onChange={(e) => setTransport(e.target.value as McpTransport)}
          >
            {Object.entries(MCP_TRANSPORT_LABELS).map(([value, label]) => (
              <MenuItem key={value} value={value}>
                {label}
              </MenuItem>
            ))}
          </TextField>
          {isStdio ? (
            <>
              <TextField
                size="small"
                required
                label="启动命令"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                error={!!errors.command}
                helperText={errors.command}
              />
              <TextField
                size="small"
                label="启动参数（每行一个）"
                multiline
                minRows={2}
                value={argsText}
                onChange={(e) => setArgsText(e.target.value)}
              />
              <TextField
                size="small"
                label="环境变量（每行 KEY=VALUE）"
                multiline
                minRows={2}
                value={envText}
                onChange={(e) => setEnvText(e.target.value)}
              />
            </>
          ) : (
            <TextField
              size="small"
              required
              label="服务 URL"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              error={!!errors.url}
              helperText={errors.url}
            />
          )}
          <TextField
            size="small"
            select
            label="鉴权方式"
            value={authType}
            onChange={(e) => setAuthType(e.target.value as AuthType)}
          >
            {Object.entries(AUTH_TYPE_LABELS).map(([value, label]) => (
              <MenuItem key={value} value={value}>
                {label}
              </MenuItem>
            ))}
          </TextField>
          {authType !== "none" && (
            <TextField
              size="small"
              required
              label="密钥"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              error={!!errors.token}
              helperText={errors.token}
            />
          )}
          {needsAuthName && (
            <TextField
              size="small"
              required
              label={authNameLabel}
              value={authName}
              onChange={(e) => setAuthName(e.target.value)}
              error={!!errors.authName}
              helperText={errors.authName}
              placeholder={AUTH_NAME_PLACEHOLDERS[authType]}
            />
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>取消</Button>
        <Button variant="contained" onClick={handleSave}>
          {initial ? "保存" : "添加"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
