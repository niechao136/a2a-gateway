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
  A2AEndpoint,
  A2AEndpointCreatePayload,
  AUTH_NAME_LABELS,
  AUTH_NAME_PLACEHOLDERS,
  AUTH_TYPE_LABELS,
  AuthType,
  adminApi,
} from "@/lib/adminApi";

interface A2AEndpointDialogProps {
  /** 传入则为编辑，否则为新建 */
  initial: A2AEndpoint | null;
  /** 已有名称，用于提交前的重名提示 */
  existingNames: string[];
  onClose: () => void;
  onSaved: () => void;
}

/**
 * A2A 目标的新建/编辑弹窗。
 * 由父组件在打开时才挂载（见页面中的 `dialogOpen && ...`），
 * 因此初始值直接用 useState 初始化即可，无需额外的回填副作用。
 */
export default function A2AEndpointDialog({
  initial,
  existingNames,
  onClose,
  onSaved,
}: A2AEndpointDialogProps) {
  const isEdit = !!initial;

  const [name, setName] = useState(initial?.name ?? "");
  const [url, setUrl] = useState(initial?.url ?? "");
  const [token, setToken] = useState(initial?.token ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [authType, setAuthType] = useState<AuthType>(initial?.auth_type ?? "bearer");
  const [authName, setAuthName] = useState(initial?.auth_name ?? "");
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [errors, setErrors] = useState<{
    name?: string;
    url?: string;
    token?: string;
    authName?: string;
  }>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const nameLabel = AUTH_NAME_LABELS[authType];
  const needsName = !!nameLabel;

  const validate = (): boolean => {
    const next: typeof errors = {};
    const n = name.trim();
    const u = url.trim();
    if (!n) next.name = "请填写名称";
    else if (n !== initial?.name && existingNames.includes(n)) {
      next.name = `名称 '${n}' 已存在`;
    }
    if (!u) next.url = "请填写服务地址";
    if (authType !== "none" && !token.trim()) {
      next.token = `${AUTH_TYPE_LABELS[authType]} 需要填写密钥`;
    }
    if (needsName && !authName.trim()) {
      next.authName = `请填写${nameLabel}`;
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleSave = async () => {
    if (!validate()) return;
    setSaving(true);
    setError(null);
    try {
      const payload: A2AEndpointCreatePayload = {
        name: name.trim(),
        url: url.trim(),
        token: token.trim(),
        description: description.trim(),
        auth_type: authType,
        auth_name: needsName ? authName.trim() : "",
        enabled,
      };
      if (isEdit && initial) await adminApi.updateA2AEndpoint(initial.id, payload);
      else await adminApi.createA2AEndpoint(payload);
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
      <DialogTitle>{isEdit ? "编辑 A2A 目标" : "新建 A2A 目标"}</DialogTitle>
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
            placeholder="例如：Hermes"
          />
          <TextField
            label="服务地址"
            required
            fullWidth
            size="small"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            error={!!errors.url}
            helperText={errors.url ?? "例如：http://host:9900/"}
            placeholder="http://host:port/"
          />
          <TextField
            label="描述"
            fullWidth
            size="small"
            multiline
            minRows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            helperText="会写入大模型提示词：说明该目标擅长什么，模型才能判断该调用哪个 A2A 目标"
            placeholder="例如：擅长代码编写与重构"
          />

          {/* 鉴权方式 */}
          <FormControl fullWidth size="small">
            <InputLabel id="a2a-auth-type-label">鉴权方式</InputLabel>
            <Select
              labelId="a2a-auth-type-label"
              label="鉴权方式"
              value={authType}
              onChange={(e) => setAuthType(e.target.value as AuthType)}
            >
              {(Object.keys(AUTH_TYPE_LABELS) as AuthType[]).map((value) => (
                <MenuItem key={value} value={value}>
                  {AUTH_TYPE_LABELS[value]}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          {needsName && (
            <TextField
              label={nameLabel}
              required
              fullWidth
              size="small"
              value={authName}
              onChange={(e) => setAuthName(e.target.value)}
              error={!!errors.authName}
              helperText={errors.authName}
              placeholder={AUTH_NAME_PLACEHOLDERS[authType]}
            />
          )}

          {authType !== "none" && (
            <TextField
              label={authType === "basic" ? "密码" : "密钥"}
              required
              fullWidth
              size="small"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              error={!!errors.token}
              helperText={errors.token ?? "密钥统一存于本字段，由鉴权方式决定放到哪里"}
              placeholder="可留空则等同无鉴权"
            />
          )}

          <FormControlLabel
            control={
              <Switch size="small" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            }
            label={enabled ? "启用" : "停用（Agent 将不再解析到该目标）"}
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
