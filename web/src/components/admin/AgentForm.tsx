"use client";

import { FormEvent, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Divider,
  FormControlLabel,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutlined";
import BoltIcon from "@mui/icons-material/Bolt";
import {
  A2ATargetInput,
  Agent,
  AgentCreatePayload,
  AVAILABLE_TOOLS,
  adminApi,
} from "@/lib/adminApi";

/** slug 允许字母、数字、- 和 _，且首尾必须是字母或数字。 */
const SLUG_PATTERN = /^[a-zA-Z0-9](?:[a-zA-Z0-9_-]*[a-zA-Z0-9])?$/;
/** 系统保留 slug（与后端 RESERVED_SLUGS + 路由中的 default 别名一致）。 */
const RESERVED_SLUGS = ["/", "", "default"];

interface TestState {
  loading?: boolean;
  ok?: boolean;
  message?: string;
}

interface AgentFormProps {
  initial?: Agent | null;
  /** 已存在的 slug 列表，用于提交前的前端冲突提示 */
  existingSlugs?: string[];
  submitting?: boolean;
  submitLabel?: string;
  onSubmit: (payload: AgentCreatePayload) => void | Promise<void>;
}

export default function AgentForm({
  initial = null,
  existingSlugs = [],
  submitting = false,
  submitLabel = "保存",
  onSubmit,
}: AgentFormProps) {
  const isEdit = !!initial;

  const [slug, setSlug] = useState(initial?.slug ?? "");
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [systemPrompt, setSystemPrompt] = useState(initial?.system_prompt ?? "");
  const [targets, setTargets] = useState<A2ATargetInput[]>(
    initial?.a2a_targets?.length
      ? initial.a2a_targets.map((t) => ({ url: t.url, token: t.token }))
      : [{ url: "", token: "" }],
  );
  const [enabledTools, setEnabledTools] = useState<string[]>(initial?.enabled_tools ?? []);
  const [errors, setErrors] = useState<{ slug?: string; name?: string }>({});
  const [tests, setTests] = useState<Record<number, TestState>>({});

  const updateTarget = (idx: number, patch: Partial<A2ATargetInput>) => {
    setTargets((prev) => prev.map((t, i) => (i === idx ? { ...t, ...patch } : t)));
    setTests((prev) => ({ ...prev, [idx]: {} }));
  };

  const addTarget = () => setTargets((prev) => [...prev, { url: "", token: "" }]);

  const removeTarget = (idx: number) => {
    setTargets((prev) => prev.filter((_, i) => i !== idx));
    setTests({});
  };

  const handleTest = async (idx: number) => {
    const target = targets[idx];
    if (!target.url.trim()) {
      setTests((prev) => ({ ...prev, [idx]: { ok: false, message: "请先填写 A2A 目标 URL" } }));
      return;
    }
    setTests((prev) => ({ ...prev, [idx]: { loading: true } }));
    try {
      const res = await adminApi.testConnection({
        url: target.url.trim(),
        token: target.token.trim(),
      });
      setTests((prev) => ({ ...prev, [idx]: { ok: res.ok, message: res.message } }));
    } catch (err) {
      setTests((prev) => ({
        ...prev,
        [idx]: { ok: false, message: err instanceof Error ? err.message : "测试失败" },
      }));
    }
  };

  const toggleTool = (toolName: string) => {
    setEnabledTools((prev) =>
      prev.includes(toolName) ? prev.filter((t) => t !== toolName) : [...prev, toolName],
    );
  };

  const validate = (): boolean => {
    const next: { slug?: string; name?: string } = {};
    if (!name.trim()) next.name = "请填写名称";
    if (!isEdit) {
      const s = slug.trim();
      if (!s) next.slug = "请填写路由 slug";
      else if (RESERVED_SLUGS.includes(s)) next.slug = "该 slug 为系统保留（默认 Agent），禁止使用";
      else if (!SLUG_PATTERN.test(s)) next.slug = "仅允许字母、数字、- 和 _，且首尾不能是符号";
      else if (existingSlugs.includes(s)) next.slug = `slug '${s}' 已被占用`;
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!validate()) return;
    void onSubmit({
      slug: slug.trim(),
      name: name.trim(),
      description: description.trim(),
      a2a_targets: targets
        .map((t) => ({ url: t.url.trim(), token: t.token.trim() }))
        .filter((t) => t.url),
      system_prompt: systemPrompt.trim() ? systemPrompt : null,
      enabled_tools: enabledTools,
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

        {/* A2A 目标配置 */}
        <Box>
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 1 }}>
            <Typography variant="subtitle1">A2A 目标</Typography>
            <Button size="small" startIcon={<AddIcon />} onClick={addTarget}>
              添加目标
            </Button>
          </Box>
          <Typography variant="caption" color="text.secondary">
            Agent 将通过 A2A 协议调用这里配置的远端 Agent。
          </Typography>

          <Stack spacing={2} sx={{ mt: 1.5 }}>
            {targets.map((target, idx) => {
              const test = tests[idx] || {};
              return (
                <Box
                  key={idx}
                  sx={{ p: 2, border: 1, borderColor: "divider", borderRadius: 1 }}
                >
                  <Stack spacing={1.5}>
                    <TextField
                      label="目标 URL"
                      fullWidth
                      size="small"
                      value={target.url}
                      onChange={(e) => updateTarget(idx, { url: e.target.value })}
                      placeholder="http://host:port/"
                    />
                    <TextField
                      label="认证 Token（Bearer）"
                      fullWidth
                      size="small"
                      value={target.token}
                      onChange={(e) => updateTarget(idx, { token: e.target.value })}
                      placeholder="可留空"
                    />
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Button
                        size="small"
                        variant="outlined"
                        startIcon={
                          test.loading ? (
                            <CircularProgress size={14} color="inherit" />
                          ) : (
                            <BoltIcon />
                          )
                        }
                        disabled={!!test.loading}
                        onClick={() => handleTest(idx)}
                      >
                        测试连接
                      </Button>
                      {targets.length > 1 && (
                        <IconButton size="small" color="error" onClick={() => removeTarget(idx)}>
                          <DeleteOutlineIcon fontSize="small" />
                        </IconButton>
                      )}
                      {test.message && (
                        <Alert
                          severity={test.ok ? "success" : "error"}
                          sx={{ py: 0, flex: 1, "& .MuiAlert-message": { py: 0.5 } }}
                        >
                          {test.message}
                        </Alert>
                      )}
                    </Box>
                  </Stack>
                </Box>
              );
            })}
          </Stack>
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

        <Divider />

        {/* 工具集 */}
        <Box>
          <Typography variant="subtitle1" gutterBottom>
            工具集
          </Typography>
          <Typography variant="caption" color="text.secondary">
            核心工具 a2a_call（调用绑定的 A2A 目标）默认启用，无需勾选。
          </Typography>
          <Stack sx={{ mt: 1 }}>
            {AVAILABLE_TOOLS.map((tool) => (
              <FormControlLabel
                key={tool.name}
                control={
                  <Checkbox
                    size="small"
                    checked={enabledTools.includes(tool.name)}
                    onChange={() => toggleTool(tool.name)}
                  />
                }
                label={
                  <Box>
                    <Typography variant="body2">{tool.label}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {tool.description}
                    </Typography>
                  </Box>
                }
              />
            ))}
          </Stack>
        </Box>

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
