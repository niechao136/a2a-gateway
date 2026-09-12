"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Snackbar,
  Stack,
  Typography,
} from "@mui/material";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import PublishIcon from "@mui/icons-material/Publish";
import UnpublishedIcon from "@mui/icons-material/Unpublished";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import ForumOutlinedIcon from "@mui/icons-material/ForumOutlined";
import AgentForm from "@/components/admin/AgentForm";
import AgentApiKeys from "@/components/admin/AgentApiKeys";
import TestChatDialog from "@/components/admin/TestChatDialog";
import { A2AEndpoint, Agent, AgentCreatePayload, McpServer, adminApi } from "@/lib/adminApi";

interface EditAgentPageProps {
  params: Promise<{ id: string }>;
}

export default function EditAgentPage({ params }: EditAgentPageProps) {
  const { id } = use(params);
  const agentId = Number(id);
  const router = useRouter();

  const [agent, setAgent] = useState<Agent | null>(null);
  // 注册表数据：供 Agent 表单勾选绑定
  const [a2aEndpoints, setA2aEndpoints] = useState<A2AEndpoint[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [testOpen, setTestOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, endpoints, servers] = await Promise.all([
        adminApi.listAgents(),
        adminApi.listA2AEndpoints(),
        adminApi.listMcpServers(),
      ]);
      const found = list.find((a) => a.id === agentId) ?? null;
      setAgent(found);
      setA2aEndpoints(endpoints);
      setMcpServers(servers);
      if (!found) setError("Agent 不存在或已被删除");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSubmit = async (payload: AgentCreatePayload) => {
    setSubmitting(true);
    setError(null);
    try {
      await adminApi.updateAgent(agentId, payload);
      setToast("保存成功");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSubmitting(false);
    }
  };

  const handleTogglePublish = async () => {
    if (!agent) return;
    setBusy(true);
    setError(null);
    try {
      if (agent.status === "published") await adminApi.unpublish(agent.id);
      else await adminApi.publish(agent.id);
      setToast(agent.status === "published" ? "已下线" : "已发布");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!agent) return;
    if (!window.confirm(`确认删除 Agent「${agent.name}」？该操作不可恢复。`)) return;
    setBusy(true);
    setError(null);
    try {
      await adminApi.deleteAgent(agent.id);
      router.replace("/admin");
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  const isDefault = agent?.slug === "/";

  return (
    <Box sx={{ maxWidth: 760, mx: "auto" }}>
      <Button component={Link} href="/admin" size="small" startIcon={<ArrowBackIcon />} sx={{ mb: 1 }}>
        返回列表
      </Button>

      <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 2, flexWrap: "wrap" }}>
        <Typography variant="h6" sx={{ flex: 1 }} noWrap>
          {agent ? agent.name : "Agent"}
        </Typography>
        {agent && (
          <Chip
            size="small"
            label={agent.status === "published" ? "已发布" : "草稿"}
            color={agent.status === "published" ? "success" : "default"}
          />
        )}
        {agent && (
          <Button
            size="small"
            variant="outlined"
            startIcon={<ForumOutlinedIcon />}
            onClick={() => setTestOpen(true)}
            disabled={busy}
          >
            测试对话
          </Button>
        )}
        {agent && (
          <Button
            size="small"
            variant="outlined"
            startIcon={agent.status === "published" ? <UnpublishedIcon /> : <PublishIcon />}
            onClick={handleTogglePublish}
            disabled={busy || (isDefault && agent.status === "published")}
          >
            {agent.status === "published" ? "下线" : "发布"}
          </Button>
        )}
        {agent && !isDefault && (
          <Button
            size="small"
            color="error"
            variant="outlined"
            startIcon={<DeleteOutlinedIcon />}
            onClick={handleDelete}
            disabled={busy}
          >
            删除
          </Button>
        )}
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {agent ? (
        <>
          <Paper variant="outlined" sx={{ p: 3 }}>
            <AgentForm
              initial={agent}
              a2aEndpoints={a2aEndpoints}
              mcpServers={mcpServers}
              submitting={submitting}
              submitLabel="保存修改"
              onSubmit={handleSubmit}
            />
          </Paper>
          {/* 每个 Agent 独立的对外 A2A 调用凭据 */}
          <Paper variant="outlined" sx={{ p: 3, mt: 2 }}>
            <AgentApiKeys agentId={agent.id} />
          </Paper>
        </>
      ) : (
        <Stack spacing={2}>
          <Typography color="text.secondary">未找到该 Agent。</Typography>
          <Button component={Link} href="/admin" variant="contained" sx={{ alignSelf: "flex-start" }}>
            返回列表
          </Button>
        </Stack>
      )}

      <TestChatDialog
        open={testOpen}
        agentId={agent?.id ?? null}
        agentName={agent?.name ?? ""}
        onClose={() => setTestOpen(false)}
      />

      <Snackbar
        open={!!toast}
        autoHideDuration={2500}
        onClose={() => setToast(null)}
        message={toast ?? ""}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      />
    </Box>
  );
}
