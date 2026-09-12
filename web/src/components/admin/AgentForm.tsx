"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  A2AEndpoint,
  A2ATargetInput,
  Agent,
  AgentCreatePayload,
  ManualMcpServerInput,
  McpServer,
} from "@/lib/adminApi";
import ManualA2ABinding from "@/components/admin/ManualA2ABinding";
import ManualMcpBinding from "@/components/admin/ManualMcpBinding";

/** slug 允许字母、数字、- 和 _，且首尾必须是字母或数字。 */
const SLUG_PATTERN = /^[a-zA-Z0-9](?:[a-zA-Z0-9_-]*[a-zA-Z0-9])?$/;
/** 系统保留 slug（与后端 RESERVED_SLUGS + 路由中的 default 别名一致）。 */
const RESERVED_SLUGS = ["/", "", "default", "a2a"];

interface AgentFormProps {
  initial?: Agent | null;
  /** 已存在的 slug 列表，用于提交前的前端冲突提示 */
  existingSlugs?: string[];
  /** 「A2A 管理」中登记的目标，供勾选绑定 */
  a2aEndpoints?: A2AEndpoint[];
  /** 「MCP 管理」中登记的服务，供勾选启用 */
  mcpServers?: McpServer[];
  submitting?: boolean;
  submitLabel?: string;
  onSubmit: (payload: AgentCreatePayload) => void | Promise<void>;
}

export default function AgentForm({
  initial = null,
  existingSlugs = [],
  a2aEndpoints = [],
  mcpServers = [],
  submitting = false,
  submitLabel = "保存",
  onSubmit,
}: AgentFormProps) {
  const isEdit = !!initial;

  const [slug, setSlug] = useState(initial?.slug ?? "");
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [systemPrompt, setSystemPrompt] = useState(initial?.system_prompt ?? "");
  // 选择式绑定：只保存注册表 id，url/token 由后端解析
  const [selectedTargetIds, setSelectedTargetIds] = useState<number[]>(
    initial?.a2a_target_ids ?? [],
  );
  const [selectedMcpIds, setSelectedMcpIds] = useState<number[]>(
    initial?.mcp_server_ids ?? [],
  );
  // 手动绑定（未在注册表登记）：与勾选并存
  const [manualTargets, setManualTargets] = useState<A2ATargetInput[]>([]);
  const [manualMcp, setManualMcp] = useState<ManualMcpServerInput[]>([]);
  const [errors, setErrors] = useState<{ slug?: string; name?: string; manualA2a?: string }>({});

  /**
   * 编辑时把「不在当前勾选的注册表条目里」的既有绑定快照识别为手动绑定，
   * 使其在表单中可见、可编辑，保存后不会静默丢失。
   */
  useEffect(() => {
    if (!initial) return;
    const selectedUrls = new Set(
      a2aEndpoints.filter((ep) => selectedTargetIds.includes(ep.id)).map((ep) => ep.url),
    );
    setManualTargets(
      (initial.a2a_targets ?? [])
        .filter((t) => t.url && !selectedUrls.has(t.url))
        .map((t) => ({
          url: t.url,
          token: t.token ?? "",
          description: t.description ?? "",
          auth_type: t.auth_type,
          auth_name: t.auth_name,
        })),
    );
    const selectedMcpNames = new Set(
      mcpServers
        .filter((s) => selectedMcpIds.includes(s.id))
        .map((s) => s.name),
    );
    setManualMcp(
      (initial.mcp_servers ?? [])
        .filter((m) => m.name && !selectedMcpNames.has(m.name))
        .map((m) => ({
          name: m.name,
          description: m.description ?? "",
          transport: m.transport ?? "streamable_http",
          url: m.url ?? "",
          command: m.command ?? "",
          args: m.args ?? [],
          env: m.env ?? {},
          token: m.token ?? "",
          auth_type: m.auth_type,
          auth_name: m.auth_name,
        })),
    );
    // 仅在打开表单时初始化一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleTarget = (id: number) => {
    setSelectedTargetIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  const toggleMcpServer = (id: number) => {
    setSelectedMcpIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  const validate = (): boolean => {
    const next: { slug?: string; name?: string; manualA2a?: string } = {};
    if (!name.trim()) next.name = "请填写名称";
    if (!isEdit) {
      const s = slug.trim();
      if (!s) next.slug = "请填写路由 slug";
      else if (RESERVED_SLUGS.includes(s)) next.slug = "该 slug 为系统保留（默认 Agent / A2A 地址前缀），禁止使用";
      else if (!SLUG_PATTERN.test(s)) next.slug = "仅允许字母、数字、- 和 _，且首尾不能是符号";
      else if (existingSlugs.includes(s)) next.slug = `slug '${s}' 已被占用`;
    }
    // 手动 A2A 目标：非空行必须填写地址
    const filled = manualTargets.filter(
      (t) => (t.url ?? "").trim() || (t.token ?? "").trim() || (t.description ?? "").trim(),
    );
    if (filled.some((t) => !(t.url ?? "").trim())) {
      next.manualA2a = "手动绑定的 A2A 目标需填写地址";
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!validate()) return;
    // 过滤完全为空的手动行
    const targets = manualTargets.filter((t) => (t.url ?? "").trim());
    const mcps = manualMcp.filter((m) => (m.name ?? "").trim());
    void onSubmit({
      slug: slug.trim(),
      name: name.trim(),
      description: description.trim(),
      a2a_target_ids: selectedTargetIds,
      mcp_server_ids: selectedMcpIds,
      a2a_targets: targets,
      mcp_servers: mcps,
      system_prompt: systemPrompt.trim() ? systemPrompt : null,
    });
  };

  return (
    <Box component="form" onSubmit={handleSubmit} noValidate>
      <Stack spacing={3}>
        {/* 基本信息 */}
        <Box>
          <Typography variant="subtitle1" gutterBottom>
            基本信息
          </Typography>
          <Stack spacing={2}>
            <TextField
              label="名称"
              required
              fullWidth
              size="small"
              value={name}
              onChange={(e) => setName(e.target.value)}
              error={!!errors.name}
              helperText={errors.name}
              placeholder="例如：代码助手"
            />
            <TextField
              label="路由 slug"
              required={!isEdit}
              fullWidth
              size="small"
              value={slug}
              disabled={isEdit}
              onChange={(e) => setSlug(e.target.value)}
              error={!!errors.slug}
              helperText={
                errors.slug ??
                (isEdit
                  ? "路由创建后不可修改"
                  : "对外访问路径，例如填 code-assistant 则可通过 /code-assistant 访问")
              }
              placeholder="例如：code-assistant"
            />
            <TextField
              label="描述"
              fullWidth
              size="small"
              multiline
              minRows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </Stack>
        </Box>

        <Divider />

        {/* A2A 目标：从注册表勾选 */}
        <Box>
          <Typography variant="subtitle1" gutterBottom>
            A2A 目标
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Agent 将通过 A2A 协议调用这里勾选的远端 Agent。目标在「A2A 管理」中统一维护。
          </Typography>

          {errors.manualA2a && (
            <Alert severity="error" sx={{ mt: 1.5 }}>
              {errors.manualA2a}
            </Alert>
          )}

          {a2aEndpoints.length === 0 ? (
            <Alert
              severity="info"
              sx={{ mt: 1.5 }}
              action={
                <Button component={Link} href="/admin/a2a" size="small">
                  前往 A2A 管理
                </Button>
              }
            >
              还没有注册任何 A2A 目标
            </Alert>
          ) : (
            <Stack sx={{ mt: 1 }}>
              {a2aEndpoints.map((endpoint) => (
                <FormControlLabel
                  key={endpoint.id}
                  control={
                    <Checkbox
                      size="small"
                      checked={selectedTargetIds.includes(endpoint.id)}
                      onChange={() => toggleTarget(endpoint.id)}
                      disabled={!endpoint.enabled}
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2">
                        {endpoint.name}
                        {!endpoint.enabled && (
                          <Chip size="small" label="已停用" sx={{ ml: 0.5, height: 18 }} />
                        )}
                      </Typography>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ fontFamily: "monospace", display: "block" }}
                      >
                        {endpoint.url}
                      </Typography>
                      {endpoint.description && (
                        <Typography variant="caption" color="text.secondary">
                          {endpoint.description}
                        </Typography>
                      )}
                    </Box>
                  }
                />
              ))}
            </Stack>
          )}

          <ManualA2ABinding value={manualTargets} onChange={setManualTargets} />
        </Box>

        <Divider />

        {/* MCP 服务：从注册表勾选 */}
        <Box>
          <Typography variant="subtitle1" gutterBottom>
            MCP 服务
          </Typography>
          <Typography variant="caption" color="text.secondary">
            勾选后，Agent 可通过 mcp_call 工具调用这些服务上的工具。服务在「MCP 管理」中统一维护。
          </Typography>

          {mcpServers.length === 0 ? (
            <Alert
              severity="info"
              sx={{ mt: 1.5 }}
              action={
                <Button component={Link} href="/admin/mcp" size="small">
                  前往 MCP 管理
                </Button>
              }
            >
              还没有注册任何 MCP 服务
            </Alert>
          ) : (
            <Stack sx={{ mt: 1 }}>
              {mcpServers.map((server) => (
                <FormControlLabel
                  key={server.id}
                  control={
                    <Checkbox
                      size="small"
                      checked={selectedMcpIds.includes(server.id)}
                      onChange={() => toggleMcpServer(server.id)}
                      disabled={!server.enabled}
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2">
                        {server.name}
                        {!server.enabled && (
                          <Chip size="small" label="已停用" sx={{ ml: 0.5, height: 18 }} />
                        )}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {server.transport}
                        {server.url ? ` · ${server.url}` : ""}
                        {server.command ? ` · ${server.command}` : ""}
                      </Typography>
                      {server.description && (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: "block" }}
                        >
                          {server.description}
                        </Typography>
                      )}
                    </Box>
                  }
                />
              ))}
            </Stack>
          )}

          <ManualMcpBinding value={manualMcp} onChange={setManualMcp} />
        </Box>

        <Divider />

        {/* System Prompt */}
        <Box>
          <Typography variant="subtitle1" gutterBottom>
            System Prompt
          </Typography>
          <TextField
            fullWidth
            multiline
            minRows={4}
            size="small"
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="留空则使用默认的人设与行为约束"
          />
        </Box>

        {/* 工具集已移除：能力扩展统一由「MCP 管理」勾选服务后自动绑定工具 */}

        <Box sx={{ display: "flex", gap: 1.5 }}>
          <Button
            type="submit"
            variant="contained"
            disabled={submitting}
            startIcon={submitting ? <CircularProgress size={16} color="inherit" /> : null}
          >
            {submitting ? "保存中..." : submitLabel}
          </Button>
        </Box>
      </Stack>
    </Box>
  );
}
