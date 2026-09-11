"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  Paper,
  Snackbar,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import BoltIcon from "@mui/icons-material/Bolt";
import { A2AEndpoint, AUTH_TYPE_LABELS, ApiError, adminApi } from "@/lib/adminApi";
import A2AEndpointDialog from "@/components/admin/A2AEndpointDialog";

/** A2A 目标注册表管理页：集中登记可复用的 A2A 服务。 */
export default function A2AAdminPage() {
  const [items, setItems] = useState<A2AEndpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<A2AEndpoint | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await adminApi.listA2AEndpoints());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 A2A 目标失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };

  const openEdit = (item: A2AEndpoint) => {
    setEditing(item);
    setDialogOpen(true);
  };

  const handleTest = async (item: A2AEndpoint) => {
    setBusyId(item.id);
    setError(null);
    try {
      const res = await adminApi.testA2AEndpoint(item.id);
      setToast(res.ok ? `✅ ${res.message}` : `❌ ${res.message}`);
    } catch (err) {
      setToast(`❌ ${err instanceof Error ? err.message : "测试失败"}`);
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (item: A2AEndpoint) => {
    if (!window.confirm(`确认删除 A2A 目标「${item.name}」？`)) return;
    setBusyId(item.id);
    setError(null);
    try {
      await adminApi.deleteA2AEndpoint(item.id);
      setToast("已删除");
      await load();
      return;
    } catch (err) {
      const message = err instanceof Error ? err.message : "删除失败";
      // 被 Agent 引用时后端返回 409，询问是否强制解绑
      const inUse = err instanceof ApiError && err.status === 409;
      if (inUse && window.confirm(`${message}\n\n是否强制删除，并从所有引用它的 Agent 上自动解绑？`)) {
        try {
          await adminApi.deleteA2AEndpoint(item.id, true);
          setToast("已强制删除并解绑");
          await load();
          return;
        } catch (err2) {
          setError(err2 instanceof Error ? err2.message : "强制删除失败");
        }
      } else {
        setError(message);
      }
    } finally {
      setBusyId(null);
    }
  };

  return (
    <>
      <Box sx={{ display: "flex", alignItems: "flex-start", gap: 2, mb: 2 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="h6">A2A 管理</Typography>
          <Typography variant="caption" color="text.secondary">
            集中登记可复用的 A2A 服务；Agent 侧只需勾选，无需重复填写地址与 token。
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<AddIcon />} onClick={openCreate}>
          新建目标
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
              <TableCell>服务地址</TableCell>
              <TableCell>鉴权</TableCell>
              <TableCell>状态</TableCell>
              <TableCell>更新时间</TableCell>
              <TableCell align="right">操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={6} align="center" sx={{ py: 4 }}>
                  <CircularProgress size={24} />
                </TableCell>
              </TableRow>
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} align="center" sx={{ py: 4, color: "text.secondary" }}>
                  暂无 A2A 目标，点击右上角「新建目标」添加
                </TableCell>
              </TableRow>
            ) : (
              items.map((item) => (
                <TableRow key={item.id} hover>
                  <TableCell>
                    <Typography variant="body2">{item.name}</Typography>
                    {item.description && (
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        noWrap
                        sx={{ display: "block", maxWidth: 260 }}
                      >
                        {item.description}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
                      {item.url}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {AUTH_TYPE_LABELS[item.auth_type] ?? item.auth_type}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={item.enabled ? "启用" : "停用"}
                      color={item.enabled ? "success" : "default"}
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {item.updated_at ? new Date(item.updated_at).toLocaleString() : "-"}
                    </Typography>
                  </TableCell>
                  <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                    {busyId === item.id && <CircularProgress size={16} sx={{ mr: 1 }} />}
                    <Tooltip title="测试连接">
                      <IconButton
                        size="small"
                        onClick={() => handleTest(item)}
                        disabled={busyId === item.id}
                      >
                        <BoltIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="编辑">
                      <IconButton
                        size="small"
                        onClick={() => openEdit(item)}
                        disabled={busyId === item.id}
                      >
                        <EditOutlinedIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="删除">
                      <IconButton
                        size="small"
                        color="error"
                        onClick={() => handleDelete(item)}
                        disabled={busyId === item.id}
                      >
                        <DeleteOutlinedIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {/* 仅在打开时挂载，弹窗初始值直接由 initial 决定 */}
      {dialogOpen && (
        <A2AEndpointDialog
          initial={editing}
          existingNames={items.map((i) => i.name)}
          onClose={() => setDialogOpen(false)}
          onSaved={load}
        />
      )}

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
