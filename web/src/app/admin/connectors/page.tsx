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
  Switch,
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
import SendOutlinedIcon from "@mui/icons-material/SendOutlined";
import ContentCopyOutlinedIcon from "@mui/icons-material/ContentCopyOutlined";
import { Agent, Connector, adminApi } from "@/lib/adminApi";
import ConnectorDialog, { PLATFORM_LABELS } from "@/components/admin/ConnectorDialog";
import SendTestDialog from "@/components/admin/SendTestDialog";
import { useIsMobile } from "@/lib/breakpoints";

const PLATFORM_COLORS: Record<string, "primary" | "info" | "warning"> = {
  feishu: "primary",
  telegram: "info",
  slack: "warning",
};

function formatTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

/** 连接器管理页：把 Agent 接入飞书 / Telegram / Slack。 */
export default function ConnectorsAdminPage() {
  const isMobile = useIsMobile();
  const [items, setItems] = useState<Connector[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Connector | null>(null);
  const [sending, setSending] = useState<Connector | null>(null);

  const load = useCallback(async () => {
    // 首个 await 之前不置 state（避免 effect 内同步 setState 触发级联渲染）；
    // loading 初值为 true，仅在首次加载时展示
    try {
      const [connectors, agentList] = await Promise.all([
        adminApi.listConnectors(),
        adminApi.listAgents(),
      ]);
      setItems(connectors);
      setAgents(agentList);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载连接器失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };

  const openEdit = (item: Connector) => {
    setEditing(item);
    setDialogOpen(true);
  };

  const handleToggle = async (item: Connector, enabled: boolean) => {
    setBusyId(item.id);
    setError(null);
    try {
      const updated = await adminApi.updateConnector(item.id, { enabled });
      setToast(updated.setup_warning || (enabled ? "已启用" : "已停用"));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (item: Connector) => {
    if (!window.confirm(`确认删除连接器「${item.name}」？其会话映射将一并删除。`)) return;
    setBusyId(item.id);
    setError(null);
    try {
      await adminApi.deleteConnector(item.id);
      setToast("已删除");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBusyId(null);
    }
  };

  const copyWebhook = async (item: Connector) => {
    await navigator.clipboard.writeText(item.webhook_url);
    setToast("Webhook URL 已复制");
  };

  const renderActions = (item: Connector) => (
    <>
      {busyId === item.id && <CircularProgress size={16} sx={{ mr: 0.5 }} />}
      <Tooltip title="复制 Webhook URL">
        <IconButton
          size="small"
          disabled={busyId === item.id}
          onClick={() => void copyWebhook(item)}
        >
          <ContentCopyOutlinedIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title="发送测试消息">
        <IconButton
          size="small"
          disabled={busyId === item.id || !item.enabled}
          onClick={() => setSending(item)}
        >
          <SendOutlinedIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title="编辑">
        <IconButton size="small" disabled={busyId === item.id} onClick={() => openEdit(item)}>
          <EditOutlinedIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title="删除">
        <IconButton
          size="small"
          color="error"
          disabled={busyId === item.id}
          onClick={() => void handleDelete(item)}
        >
          <DeleteOutlinedIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </>
  );

  return (
    <>
      <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1, flexWrap: "wrap", mb: 2 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="h6">连接器管理</Typography>
          <Typography variant="caption" color="text.secondary">
            把 Agent 接入飞书 / Telegram / Slack：私聊直接回复，群聊 @机器人 才处理。
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<AddIcon />} onClick={openCreate}>
          新建连接器
        </Button>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {isMobile && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          {loading ? (
            <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
              <CircularProgress size={24} />
            </Box>
          ) : items.length === 0 ? (
            <Typography variant="body2" color="text.secondary" align="center" sx={{ py: 4 }}>
              还没有连接器，点击右上角「新建连接器」开始接入
            </Typography>
          ) : (
            items.map((item) => (
              <Paper key={item.id} variant="outlined" sx={{ p: 2 }}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Typography variant="body1" sx={{ flex: 1, minWidth: 0, fontWeight: 600 }} noWrap>
                    {item.name}
                  </Typography>
                  <Chip
                    size="small"
                    color={PLATFORM_COLORS[item.platform] ?? "default"}
                    label={PLATFORM_LABELS[item.platform]}
                  />
                </Box>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                  Agent：{item.agent_name || item.agent_id} · 最近活跃：
                  {formatTime(item.last_active_at)}
                </Typography>
                <Box sx={{ display: "flex", alignItems: "center", mt: 1 }}>
                  <Switch
                    size="small"
                    checked={item.enabled}
                    disabled={busyId === item.id}
                    onChange={(e) => void handleToggle(item, e.target.checked)}
                  />
                  <Typography variant="caption" color="text.secondary">
                    {item.enabled ? "启用" : "停用"}
                  </Typography>
                </Box>
                <Typography
                  variant="caption"
                  sx={{ display: "block", mt: 0.5, fontFamily: "monospace", wordBreak: "break-all" }}
                >
                  {item.webhook_url}
                </Typography>
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "flex-end",
                    alignItems: "center",
                    gap: 0.5,
                    mt: 1,
                    minHeight: 44,
                  }}
                >
                  {renderActions(item)}
                </Box>
              </Paper>
            ))
          )}
        </Box>
      )}

      {!isMobile && (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>名称</TableCell>
                <TableCell>平台</TableCell>
                <TableCell>绑定 Agent</TableCell>
                <TableCell>启用</TableCell>
                <TableCell>最近活跃</TableCell>
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
                    还没有连接器，点击右上角「新建连接器」开始接入
                  </TableCell>
                </TableRow>
              ) : (
                items.map((item) => (
                  <TableRow key={item.id} hover>
                    <TableCell sx={{ maxWidth: 220 }}>
                      <Typography variant="body2" noWrap>
                        {item.name}
                      </Typography>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ display: "block", fontFamily: "monospace" }}
                        noWrap
                      >
                        {item.webhook_url}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        color={PLATFORM_COLORS[item.platform] ?? "default"}
                        label={PLATFORM_LABELS[item.platform]}
                      />
                    </TableCell>
                    <TableCell>{item.agent_name || item.agent_id}</TableCell>
                    <TableCell>
                      <Switch
                        size="small"
                        checked={item.enabled}
                        disabled={busyId === item.id}
                        onChange={(e) => void handleToggle(item, e.target.checked)}
                      />
                    </TableCell>
                    <TableCell>{formatTime(item.last_active_at)}</TableCell>
                    <TableCell align="right">{renderActions(item)}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {dialogOpen && (
        <ConnectorDialog
          initial={editing}
          agents={agents}
          existingNames={items.map((item) => item.name)}
          onClose={() => setDialogOpen(false)}
          onSaved={() => void load()}
        />
      )}
      {sending && <SendTestDialog connector={sending} onClose={() => setSending(null)} />}

      <Snackbar
        open={toast !== null}
        autoHideDuration={3000}
        onClose={() => setToast(null)}
        message={toast}
      />
    </>
  );
}
