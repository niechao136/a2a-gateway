"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Paper,
  Snackbar,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import ContentCopyOutlinedIcon from "@mui/icons-material/ContentCopyOutlined";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import { ApiError, ApiKey, adminApi } from "@/lib/adminApi";

/** API Key 管理页：查看 / 新增 / 删除对外 A2A 调用凭据。 */
export default function ApiKeysAdminPage() {
  const [items, setItems] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // 新建弹窗
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await adminApi.listApiKeys());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 API Key 失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const copyKey = async (key: string) => {
    try {
      await navigator.clipboard.writeText(key);
      setToast("已复制到剪贴板");
    } catch {
      setError("复制失败，请手动选择复制");
    }
  };

  const handleCreate = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("请填写名称");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const created = await adminApi.createApiKey({ name: trimmed });
      setDialogOpen(false);
      setName("");
      setToast(`已创建 Key：${created.key}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = (item: ApiKey) => {
    if (item.is_default) return;
    if (!window.confirm(`确认删除 API Key「${item.name}」？删除后使用该 Key 的调用方将无法继续访问。`)) {
      return;
    }
    setBusyId(item.id);
    setError(null);
    void adminApi
      .deleteApiKey(item.id)
      .then(async () => {
        setToast("已删除");
        await load();
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 400) {
          setError(err.message);
        } else {
          setError(err instanceof Error ? err.message : "删除失败");
        }
      })
      .finally(() => setBusyId(null));
  };

  return (
    <>
      <Box sx={{ display: "flex", alignItems: "flex-start", gap: 2, mb: 2 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="h6">API Key 管理</Typography>
          <Typography variant="caption" color="text.secondary">
            其他 Agent 通过 A2A 调用本网关 Agent（/a2a/...）时需携带此 Key：请求头
            <code style={{ margin: "0 4px" }}>X-Api-Key</code>或
            <code style={{ margin: "0 4px" }}>Authorization: Bearer</code>。
            默认 Key 首次启动自动生成，不可删除。
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => setDialogOpen(true)}>
          新建 Key
        </Button>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <TableContainer component={Paper} variant="outlined">
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>名称</TableCell>
              <TableCell>Key</TableCell>
              <TableCell>状态</TableCell>
              <TableCell>创建时间</TableCell>
              <TableCell align="right">操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={5} align="center" sx={{ py: 4 }}>
                  <CircularProgress size={24} />
                </TableCell>
              </TableRow>
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} align="center" sx={{ py: 4, color: "text.secondary" }}>
                  暂无 API Key
                </TableCell>
              </TableRow>
            ) : (
              items.map((item) => (
                <TableRow key={item.id} hover>
                  <TableCell>
                    <Typography variant="body2">{item.name}</Typography>
                  </TableCell>
                  <TableCell>
                    <Stack direction="row" sx={{ alignItems: "center" }} spacing={0.5}>
                      <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
                        {item.key}
                      </Typography>
                      <Tooltip title="复制">
                        <IconButton size="small" onClick={() => void copyKey(item.key)}>
                          <ContentCopyOutlinedIcon sx={{ fontSize: 16 }} />
                        </IconButton>
                      </Tooltip>
                    </Stack>
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={item.is_default ? "默认" : "自定义"}
                      color={item.is_default ? "primary" : "default"}
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {item.created_at ? new Date(item.created_at).toLocaleString() : "-"}
                    </Typography>
                  </TableCell>
                  <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                    {busyId === item.id && <CircularProgress size={16} sx={{ mr: 1 }} />}
                    <Tooltip title={item.is_default ? "默认 Key 不可删除" : "删除"}>
                      <span>
                        <IconButton
                          size="small"
                          color="error"
                          onClick={() => handleDelete(item)}
                          disabled={busyId === item.id || item.is_default}
                        >
                          <DeleteOutlinedIcon fontSize="small" />
                        </IconButton>
                      </span>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <Dialog open={dialogOpen} onClose={() => !creating && setDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>新建 API Key</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            size="small"
            margin="dense"
            label="名称"
            placeholder="如：ci 调用 / 某服务专用"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={creating}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleCreate();
            }}
          />
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
            保存后生成形如 a2a-xxxx 的密钥，创建后请在列表中复制保存。
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)} disabled={creating}>
            取消
          </Button>
          <Button variant="contained" onClick={() => void handleCreate()} disabled={creating || !name.trim()}>
            创建
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={!!toast}
        autoHideDuration={3200}
        onClose={() => setToast(null)}
        message={toast ?? ""}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      />
    </>
  );
}
