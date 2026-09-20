"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Alert, Box, Button, Paper, Typography } from "@mui/material";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import AgentForm from "@/components/admin/AgentForm";
import { A2AEndpoint, AgentCreatePayload, McpServer, Skill, adminApi } from "@/lib/adminApi";

export default function NewAgentPage() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [existingSlugs, setExistingSlugs] = useState<string[]>([]);
  // 注册表数据：供 Agent 表单勾选绑定
  const [a2aEndpoints, setA2aEndpoints] = useState<A2AEndpoint[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);

  useEffect(() => {
    // 预取已有 slug，用于提交前的前端冲突提示
    adminApi
      .listAgents()
      .then((agents) => setExistingSlugs(agents.map((a) => a.slug)))
      .catch(() => {
        /* 预取失败不影响创建（后端仍有唯一性校验） */
      });

    Promise.all([adminApi.listA2AEndpoints(), adminApi.listMcpServers(), adminApi.listSkills()])
      .then(([endpoints, servers, skillList]) => {
        setA2aEndpoints(endpoints);
        setMcpServers(servers);
        setSkills(skillList);
      })
      .catch(() => {
        /* 注册表加载失败时表单显示为空，可稍后刷新重试 */
      });
  }, []);

  const handleSubmit = async (payload: AgentCreatePayload) => {
    setSubmitting(true);
    setError(null);
    try {
      const agent = await adminApi.createAgent(payload);
      router.replace(`/admin/agents/${agent.id}/edit`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Box sx={{ maxWidth: 760, mx: "auto" }}>
      <Button
        component={Link}
        href="/admin"
        size="small"
        startIcon={<ArrowBackIcon />}
        sx={{ mb: 1 }}
      >
        返回列表
      </Button>
      <Typography variant="h6" gutterBottom>
        新建 Agent
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <Paper variant="outlined" sx={{ p: 3 }}>
        <AgentForm
          existingSlugs={existingSlugs}
          a2aEndpoints={a2aEndpoints}
          mcpServers={mcpServers}
          skills={skills}
          submitting={submitting}
          submitLabel="创建 Agent"
          onSubmit={handleSubmit}
        />
      </Paper>
    </Box>
  );
}
