"use client";

import { useState } from "react";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
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
  A2ATargetInput,
} from "@/lib/adminApi";
import DialogTitleBar from "./DialogTitleBar";
import { useIsMobile } from "@/lib/breakpoints";

interface ManualA2ABindingProps {
  value: A2ATargetInput[];
  onChange: (value: A2ATargetInput[]) => void;
}

/** 手动绑定的 A2A 目标（无需在「A2A 管理」注册，可与勾选并存），弹窗编辑。 */
export default function ManualA2ABinding({ value, onChange }: ManualA2ABindingProps) {
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
        <Typography variant="body2">手动绑定（未注册目标）</Typography>
        <Button size="small" startIcon={<AddIcon />} onClick={openCreate}>
          添加
        </Button>
      </Box>
      <Typography variant="caption" color="text.secondary">
        直接填写地址即可，无需先在「A2A 管理」登记；说明会帮助模型判断何时调用该目标。
      </Typography>

      {value.length === 0 ? (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
          暂无手动绑定的目标
        </Typography>
      ) : (
        <Stack spacing={0.5} sx={{ mt: 1 }}>
          {value.map((item, index) => (
            <Stack key={index} direction="row" spacing={1} sx={{ alignItems: "center" }}>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="body2">
                  <Typography
                    component="span"
                    variant="body2"
                    sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
                  >
                    {item.url}
                  </Typography>
                  {item.auth_type && item.auth_type !== "bearer" && (
                    <Chip
                      size="small"
                      label={AUTH_TYPE_LABELS[item.auth_type] ?? item.auth_type}
                      sx={{ ml: 1, height: 18 }}
                    />
                  )}
                  {item.auth_type && item.auth_type !== "none" && item.token && (
                    <Chip size="small" label="已配置密钥" sx={{ ml: 0.5, height: 18 }} />
                  )}
                </Typography>
                {item.description && (
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block" }}
                    noWrap
                  >
                    {item.description}
                  </Typography>
                )}
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
        <ManualA2ADialog
          initial={editIndex !== null ? value[editIndex] : null}
          existingUrls={value.filter((_, i) => i !== editIndex).map((item) => item.url)}
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
// 单个手动 A2A 目标的编辑弹窗
// ---------------------------------------------------------------------------
interface ManualA2ADialogProps {
  initial: A2ATargetInput | null;
  existingUrls: string[];
  onClose: () => void;
  onSave: (item: A2ATargetInput) => void;
}

function ManualA2ADialog({ initial, existingUrls, onClose, onSave }: ManualA2ADialogProps) {
  const isMobile = useIsMobile();
  const [url, setUrl] = useState(initial?.url ?? "");
  const [token, setToken] = useState(initial?.token ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [authType, setAuthType] = useState<AuthType>(initial?.auth_type ?? "bearer");
  const [authName, setAuthName] = useState(initial?.auth_name ?? "");
  const [errors, setErrors] = useState<{ url?: string; token?: string; authName?: string }>({});

  const authNameLabel = AUTH_NAME_LABELS[authType];
  const needsAuthName = !!authNameLabel;

  const validate = (): boolean => {
    const next: typeof errors = {};
    const u = url.trim();
    if (!u) next.url = "请填写目标地址";
    else if (existingUrls.includes(u)) next.url = `地址 '${u}' 已添加`;
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
      url: url.trim(),
      token: token.trim(),
      description: description.trim(),
      auth_type: authType,
      auth_name: needsAuthName ? authName.trim() : "",
    });
  };

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth fullScreen={isMobile}>
      <DialogTitleBar title={initial ? "编辑手动绑定的 A2A 目标" : "手动绑定 A2A 目标"} onClose={onClose} />
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 0.5 }}>
          <TextField
            size="small"
            required
            label="目标地址"
            placeholder="http://host:port/"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            error={!!errors.url}
            helperText={errors.url ?? "A2A 服务地址（Agent Card 的根地址）"}
            sx={{ "& input": { fontFamily: "monospace" } }}
          />
          <TextField
            size="small"
            label="说明（可选）"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="该目标擅长什么，会进入大模型提示词"
            multiline
            minRows={2}
          />
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
