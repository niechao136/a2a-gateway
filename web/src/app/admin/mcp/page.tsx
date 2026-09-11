"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
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
import { styled } from "@mui/material/styles";
import AddIcon from "@mui/icons-material/Add";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import BoltIcon from "@mui/icons-material/Bolt";
import ListAltIcon from "@mui/icons-material/ListAlt";
import {
  ApiError,
  MCP_TRANSPORT_LABELS,
  McpServer,
  McpToolInfo,
  adminApi,
} from "@/lib/adminApi";
import McpServerDialog from "@/components/admin/McpServerDialog";

const MonoText = styled(Typography)({
  fontFamily: "monospace",
  fontSize: 12,
  wordBreak: "break-all",
});

/** 展示某 MCP 服务的连接信息：stdio 显示命令，远端显示地址。 */
function connectionText(server: McpServer): string {
  if (server.transport === "stdio") {
    return [server.command, ...(server.args ?? [])].filter(Boolean).join(" ");
  }
  return server.url || "-";
}

/** MCP 服务注册表管理页：集中登记可复用的 MCP 服务。 */
export default function McpAdminPage() {
  const [items, setItems] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<McpServer | null>(null);
  // 工具清单：点击时先拉数据再展示，避免弹窗内部再做一次副作用式加载
  const [toolsTarget, setToolsTarget] = useState<{
    name: string;
    tools: McpToolInfo[];
    message: string;
  } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await adminApi.listMcpServers());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 MCP 服务失败");
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

  const openEdit = (item: McpServer) => {
    setEditing(item);
    setDialogOpen(true);
  };

  const handleTest = async (item: McpServer) => {
    setBusyId(item.id);
    setError(null);
    try {
      const res = await adminApi.testMcpServer(item.id);
      setToast(res.ok ? `✅ ${res.message}` : `❌ ${res.message}`);
    } catch (err) {
      setToast(`❌ ${err instanceof Error ? err.message : "测试失败"}`);
    } finally {
      setBusyId(null);
    }
  };

  const handleOpenTools = async (item: McpServer) => {
    setBusyId(item.id);
    setError(null);
    try {
      const res = await adminApi.listMcpServerTools(item.id);
      setToolsTarget({ name: item.name, tools: res.tools ?? [], message: res.message });
      if (!res.ok) setToast(`⚠️ ${res.message}`);
    } catch (err) {
      setToast(`❌ ${err instanceof Error ? err.message : "获取工具列表失败"}`);
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (item: McpServer) => {
    if (!window.confirm(`确认删除 MCP 服务「${item.name}」？`)) return;
    setBusyId(item.id);
    setError(null);
    try {
      await adminApi.deleteMcpServer(item.id);
      setToast("已删除");
      await load();
      return;
    } catch (err) {
      const message = err instanceof Error ? err.message : "删除失败";
      const inUse = err instanceof ApiError && err.status === 409;
      if (inUse && window.confirm(`${message}\n\n是否强制删除，并从所有引用它的 Agent 上自动解绑？`)) {
        try {
          await adminApi.deleteMcpServer(item.id, true);
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
          <Typography variant="h6">MCP 管理</Typography>
          <Typography variant="caption" color="text.secondary">
            集中登记 MCP 服务（stdio / sse / streamable http）；Agent 侧勾选后即可使用其工具。
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<AddIcon />} onClick={openCreate}>
          新建服务
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
              <TableCell>传输方式</TableCell>
              <TableCell>连接信息</TableCell>
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
                  暂无 MCP 服务，点击右上角「新建服务」添加
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
                        sx={{ display: "block", maxWidth: 240 }}
                      >
                        {item.description}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {MCP_TRANSPORT_LABELS[item.transport] ?? item.transport}
                    </Typography>
                  </TableCell>
                  <TableCell sx={{ maxWidth: 280 }}>
                    <MonoText variant="caption" color="text.secondary">
                      {connectionText(item)}
                    </MonoText>
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
                    <Tooltip title="查看可用工具">
                      <IconButton
                        size="small"
                        onClick={() => handleOpenTools(item)}
                        disabled={busyId === item.id}
                      >
                        <ListAltIcon fontSize="small" />
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
        <McpServerDialog
          initial={editing}
          existingNames={items.map((i) => i.name)}
          onClose={() => setDialogOpen(false)}
          onSaved={load}
        />
      )}

      <Dialog
        open={!!toolsTarget}
        onClose={() => setToolsTarget(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>可用工具 · {toolsTarget?.name ?? ""}</DialogTitle>
        <DialogContent dividers>
          {toolsTarget && toolsTarget.tools.length > 0 ? (
            <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
              {toolsTarget.tools.map((tool) => (
                <Box key={tool.name}>
                  <MonoText variant="body2">{tool.name}</MonoText>
                  <Typography variant="caption" color="text.secondary">
                    {tool.description || "无描述"}
                  </Typography>
                </Box>
              ))}
            </Box>
          ) : (
            <Typography variant="body2" color="text.secondary">
              {toolsTarget?.message || "该服务暂无可用工具"}
            </Typography>
          )}
        </DialogContent>
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
