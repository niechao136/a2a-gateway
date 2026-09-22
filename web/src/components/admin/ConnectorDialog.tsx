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
  FormControl,
  FormControlLabel,
  FormHelperText,
  InputLabel,
  MenuItem,
  Select,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import { Agent, Connector, ConnectorPlatform, adminApi } from "@/lib/adminApi";
import DialogTitleBar from "./DialogTitleBar";
import { useIsMobile } from "@/lib/breakpoints";

export const PLATFORM_LABELS: Record<ConnectorPlatform, string> = {
  feishu: "飞书",
  telegram: "Telegram",
  slack: "Slack",
};

/** 各平台凭据字段：平台选择驱动动态渲染 */
export const PLATFORM_CREDENTIAL_FIELDS: Record<
  ConnectorPlatform,
  { key: string; label: string; helper?: string }[]
> = {
  feishu: [
    { key: "app_id", label: "App ID" },
    { key: "app_secret", label: "App Secret" },
    { key: "verification_token", label: "Verification Token" },
    { key: "encrypt_key", label: "Encrypt Key", helper: "未启用事件加密则留空" },
  ],
  telegram: [
    { key: "bot_token", label: "Bot Token", helper: "BotFather 提供的 token" },
    { key: "secret_token", label: "Secret Token", helper: "留空由后端自动生成" },
  ],
  slack: [
    { key: "bot_token", label: "Bot Token", helper: "xoxb- 开头" },
    { key: "signing_secret", label: "Signing Secret" },
  ],
};

/** 各平台把 Webhook URL 填到哪里（保存成功后的指引） */
const PLATFORM_SETUP_HINTS: Record<ConnectorPlatform, string> = {
  feishu: "在飞书开放平台「事件与回调」中填入该地址，并订阅「接收消息 im.message.receive_v1」事件。",
  telegram: "Telegram 会自动注册 Webhook（需配置 PUBLIC_BASE_URL）；失败时可手动调用 setWebhook。",
  slack: "在 Slack App 的「Event Subscriptions」中填入该地址，并订阅 message.im 与 app_mention 事件。",
};

interface ConnectorDialogProps {
  /** 传入则为编辑，否则为新建 */
  initial: Connector | null;
  agents: Agent[];
  /** 已有名称，用于提交前的重名提示 */
  existingNames: string[];
  onClose: () => void;
  onSaved: () => void;
}

/**
 * 连接器新建/编辑弹窗。保存成功后切换为「Webhook URL 展示」视图：
 * 提供一键复制与平台侧配置指引（Telegram 自动注册结果另行提示）。
 */
export default function ConnectorDialog({
  initial,
  agents,
  existingNames,
  onClose,
  onSaved,
}: ConnectorDialogProps) {
  const isMobile = useIsMobile();
  const isEdit = !!initial;

  const [name, setName] = useState(initial?.name ?? "");
  const [platform, setPlatform] = useState<ConnectorPlatform>(initial?.platform ?? "feishu");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [agentId, setAgentId] = useState<number>(initial?.agent_id ?? agents[0]?.id ?? 0);
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<{ name?: string; agentId?: string }>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<Connector | null>(null);
  const [copied, setCopied] = useState(false);

  const masked = initial?.credentials_masked ?? {};

  const validate = (): boolean => {
    const next: typeof errors = {};
    const trimmed = name.trim();
    if (!trimmed) {
      next.name = "请填写名称";
    } else if (trimmed !== initial?.name && existingNames.includes(trimmed)) {
      next.name = `名称「${trimmed}」已存在`;
    }
    if (!agentId) next.agentId = "请选择绑定的 Agent";
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const submit = async () => {
    if (!validate()) return;
    setSaving(true);
    setError(null);
    try {
      const result =
        isEdit && initial
          ? await adminApi.updateConnector(initial.id, {
              name: name.trim(),
              description: description.trim(),
              agent_id: agentId,
              enabled,
              credentials,
            })
          : await adminApi.createConnector({
              name: name.trim(),
              platform,
              description: description.trim(),
              agent_id: agentId,
              enabled,
              credentials,
            });
      setSaved(result);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const copyWebhook = async (url: string) => {
    await navigator.clipboard.writeText(url);
    setCopied(true);
  };

  if (saved) {
    return (
      <Dialog open onClose={onClose} maxWidth="sm" fullWidth fullScreen={isMobile}>
        <DialogTitleBar title="连接器已保存" onClose={onClose} />
        <DialogContent dividers>
          {saved.setup_warning ? (
            <Alert severity="warning" sx={{ mb: 2 }}>
              {saved.setup_warning}
            </Alert>
          ) : saved.platform === "telegram" ? (
            <Alert severity="success" sx={{ mb: 2 }}>
              Telegram Webhook 已自动注册
            </Alert>
          ) : null}
          <Typography variant="body2" sx={{ mb: 1 }}>
            Webhook URL（请填入平台后台的事件订阅配置）
          </Typography>
          <Box sx={{ display: "flex", gap: 1, alignItems: "flex-start" }}>
            <TextField
              fullWidth
              size="small"
              value={saved.webhook_url}
              slotProps={{ input: { readOnly: true } }}
            />
            <Button
              variant="outlined"
              startIcon={<ContentCopyIcon />}
              onClick={() => void copyWebhook(saved.webhook_url)}
              sx={{ flexShrink: 0 }}
            >
              复制
            </Button>
          </Box>
          {copied && (
            <Typography variant="caption" color="success.main" sx={{ display: "block", mt: 0.5 }}>
              已复制到剪贴板
            </Typography>
          )}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
            {PLATFORM_SETUP_HINTS[saved.platform]}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>关闭</Button>
        </DialogActions>
      </Dialog>
    );
  }

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth fullScreen={isMobile}>
      <DialogTitleBar title={isEdit ? "编辑连接器" : "新建连接器"} onClose={onClose} />
      <DialogContent dividers>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2, pt: 1 }}>
          {error && (
            <Alert severity="error" onClose={() => setError(null)}>
              {error}
            </Alert>
          )}
          <TextField
            label="名称"
            required
            fullWidth
            size="small"
            value={name}
            onChange={(e) => setName(e.target.value)}
            error={!!errors.name}
            helperText={errors.name}
            placeholder="例如：客服机器人"
          />
          <FormControl fullWidth size="small" disabled={isEdit}>
            <InputLabel id="connector-platform-label">平台</InputLabel>
            <Select
              labelId="connector-platform-label"
              label="平台"
              value={platform}
              onChange={(e) => setPlatform(e.target.value as ConnectorPlatform)}
            >
              {(Object.keys(PLATFORM_LABELS) as ConnectorPlatform[]).map((value) => (
                <MenuItem key={value} value={value}>
                  {PLATFORM_LABELS[value]}
                </MenuItem>
              ))}
            </Select>
            {isEdit && <FormHelperText>平台创建后不可更换</FormHelperText>}
          </FormControl>
          <FormControl fullWidth size="small" error={!!errors.agentId}>
            <InputLabel id="connector-agent-label">绑定 Agent</InputLabel>
            <Select
              labelId="connector-agent-label"
              label="绑定 Agent"
              value={agentId || ""}
              onChange={(e) => setAgentId(Number(e.target.value))}
            >
              {agents.map((agent) => (
                <MenuItem key={agent.id} value={agent.id}>
                  {agent.name}
                  {agent.status === "draft" ? "（未发布）" : ""}
                </MenuItem>
              ))}
            </Select>
            <FormHelperText>
              {errors.agentId ?? "一个连接器绑定一个 Agent；未发布的 Agent 收到消息会提示未发布"}
            </FormHelperText>
          </FormControl>

          {PLATFORM_CREDENTIAL_FIELDS[platform].map((field) => (
            <TextField
              key={field.key}
              label={field.label}
              fullWidth
              size="small"
              type="password"
              autoComplete="new-password"
              value={credentials[field.key] ?? ""}
              onChange={(e) =>
                setCredentials((prev) => ({ ...prev, [field.key]: e.target.value }))
              }
              placeholder={masked[field.key] ? "已配置，留空则不修改" : ""}
              helperText={
                masked[field.key]
                  ? `${field.helper ?? ""}${field.helper ? " · " : ""}已配置，留空则不修改`
                  : field.helper
              }
            />
          ))}

          <TextField
            label="备注"
            fullWidth
            size="small"
            multiline
            minRows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <FormControlLabel
            control={<Switch checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />}
            label="启用（停用后平台回调直接拒绝，凭据保留）"
          />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>取消</Button>
        <Button
          variant="contained"
          onClick={() => void submit()}
          disabled={saving}
          startIcon={saving ? <CircularProgress size={16} /> : undefined}
        >
          保存
        </Button>
      </DialogActions>
    </Dialog>
  );
}
