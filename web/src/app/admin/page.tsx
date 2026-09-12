"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
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
import PublishIcon from "@mui/icons-material/Publish";
import UnpublishedIcon from "@mui/icons-material/Unpublished";
import ForumOutlinedIcon from "@mui/icons-material/ForumOutlined";
import ChatOutlinedIcon from "@mui/icons-material/ChatOutlined";
import { Agent, a2aPathForAgent, adminApi } from "@/lib/adminApi";
import TestChatDialog from "@/components/admin/TestChatDialog";

export default function AdminAgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [testAgent, setTestAgent] = useState<Agent | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setAgents(await adminApi.listAgents());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 Agent 列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (id: number, fn: () => Promise<unknown>, okMessage: string) => {
    setBusyId(id);
    setError(null);
    try {
      await fn();
      setToast(okMessage);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusyId(null);
    }
  };

  const handleTogglePublish = (agent: Agent) => {
    if (agent.status === "published") {
      void run(agent.id, () => adminApi.unpublish(agent.id), `已下线：${agent.name}`);
    } else {
      void run(agent.id, () => adminApi.publish(agent.id), `已发布：${agent.name}`);
    }
  };

  const handleDelete = (agent: Agent) => {
    if (!window.confirm(`确认删除 Agent「${agent.name}」？该操作不可恢复。`)) return;
    void run(agent.id, () => adminApi.deleteAgent(agent.id), `已删除：${agent.name}`);
  };

  const targetSummary = (agent: Agent): string => {
    const urls = (agent.a2a_targets || []).map((t) => t.url).filter(Boolean);
    if (urls.length === 0) return "未绑定";
    return urls.length === 1 ? urls[0] : `${urls[0]} 等 ${urls.length} 个`;
  };

  return (
    <>
      <Box sx={{ display: "flex", alignItems: "center", mb: 2 }}>
        <Typography variant="h6" sx={{ flex: 1 }}>
          Agent 管理
        </Typography>
        <Button component={Link} href="/admin/agents/new" variant="contained" startIcon={<AddIcon />}>
          新建 Agent
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
              <TableCell>路由</TableCell>
              <TableCell>A2A 地址</TableCell>
              <TableCell>状态</TableCell>
              <TableCell>A2A 目标</TableCell>
              <TableCell>更新时间</TableCell>
              <TableCell align="right">操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={7} align="center" sx={{ py: 4 }}>
                  <CircularProgress size={24} />
                </TableCell>
              </TableRow>
            ) : agents.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} align="center" sx={{ py: 4, color: "text.secondary" }}>
                  暂无 Agent，点击右上角「新建 Agent」开始
                </TableCell>
              </TableRow>
            ) : (
              agents.map((agent) => {
                const isDefault = agent.slug === "/";
                const busy = busyId === agent.id;
                return (
                  <TableRow key={agent.id} hover>
                    <TableCell>
                      <Typography variant="body2">{agent.name}</Typography>
                      {agent.description && (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          noWrap
                          sx={{ display: "block", maxWidth: 240 }}
                        >
                          {agent.description}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <code>{isDefault ? "/（默认）" : `/${agent.slug}`}</code>
                    </TableCell>
                    <TableCell>
                      {agent.status === "published" ? (
                        <Tooltip title="其他 Agent 通过 A2A 调用本 Agent 的地址（调用时需携带 API Key）">
                          <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
                            {a2aPathForAgent(agent)}
                          </Typography>
                        </Tooltip>
                      ) : (
                        <Typography variant="caption" color="text.secondary">
                          发布后可用
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={agent.status === "published" ? "已发布" : "草稿"}
                        color={agent.status === "published" ? "success" : "default"}
                      />
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption" color="text.secondary">
                        {targetSummary(agent)}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption" color="text.secondary">
                        {agent.updated_at ? new Date(agent.updated_at).toLocaleString() : "-"}
                      </Typography>
                    </TableCell>
                    <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                      {busy && <CircularProgress size={16} sx={{ mr: 1 }} />}
                      <Tooltip title="编辑">
                        <IconButton
                          size="small"
                          component={Link}
                          href={`/admin/agents/${agent.id}/edit`}
                          disabled={busy}
                        >
                          <EditOutlinedIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip
                        title={
                          agent.status === "published"
                            ? "前往对话"
                            : "草稿未发布，发布后可对话"
                        }
                      >
                        <span>
                          <IconButton
                            size="small"
                            component={Link}
                            href={isDefault ? "/" : `/${agent.slug}`}
                            disabled={busy || agent.status !== "published"}
                          >
                            <ChatOutlinedIcon fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                      <Tooltip title={agent.status === "published" ? "下线" : "发布"}>
                        <span>
                          <IconButton
                            size="small"
                            onClick={() => handleTogglePublish(agent)}
                            disabled={busy || (isDefault && agent.status === "published")}
                          >
                            {agent.status === "published" ? (
                              <UnpublishedIcon fontSize="small" />
                            ) : (
                              <PublishIcon fontSize="small" />
                            )}
                          </IconButton>
                        </span>
                      </Tooltip>
                      <Tooltip title="测试对话">
                        <IconButton size="small" onClick={() => setTestAgent(agent)} disabled={busy}>
                          <ForumOutlinedIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title={isDefault ? "默认 Agent 不可删除" : "删除"}>
                        <span>
                          <IconButton
                            size="small"
                            color="error"
                            onClick={() => handleDelete(agent)}
                            disabled={busy || isDefault}
                          >
                            <DeleteOutlinedIcon fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <TestChatDialog
        open={!!testAgent}
        agentId={testAgent?.id ?? null}
        agentName={testAgent?.name ?? ""}
        onClose={() => setTestAgent(null)}
      />

      <Snackbar
        open={!!toast}
        autoHideDuration={2500}
        onClose={() => setToast(null)}
        message={toast ?? ""}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      />
    </>
  );
}
