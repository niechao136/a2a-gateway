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
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  A2AEndpoint,
  A2ATargetInput,
  Agent,
  AgentCreatePayload,
  LLMModel,
  ManualMcpServerInput,
  McpServer,
  Skill,
} from "@/lib/adminApi";
import ManualA2ABinding from "@/components/admin/ManualA2ABinding";
import ManualMcpBinding from "@/components/admin/ManualMcpBinding";
import { MAX_BINDING_CONTENT_BYTES, estimateResidentBytes, formatBytes } from "@/lib/skillUtils";

/** slug 允许字母、数字、- 和 _，且首尾必须是字母或数字。 */
const SLUG_PATTERN = /^[a-zA-Z0-9](?:[a-zA-Z0-9_-]*[a-zA-Z0-9])?$/;
/** 系统保留 slug（与后端 RESERVED_SLUGS + 路由中的 default 别名一致）。 */
const RESERVED_SLUGS = ["/", "", "default", "a2a"];

/** 未通过审核的技能：不可勾选，用标签说明原因。 */
const SKILL_STATUS_LABEL: Record<string, string> = {
  pending: "待审核",
  rejected: "已拒绝",
};

interface AgentFormProps {
  initial?: Agent | null;
  /** 已存在的 slug 列表，用于提交前的前端冲突提示 */
  existingSlugs?: string[];
  /** 「A2A 管理」中登记的目标，供勾选绑定 */
  a2aEndpoints?: A2AEndpoint[];
  /** 「MCP 管理」中登记的服务，供勾选启用 */
  mcpServers?: McpServer[];
  /** 「Skill 管理」中的技能，供勾选绑定（只有 approved 的可以勾选） */
  skills?: Skill[];
  /** 「模型管理」中的模型，供单选绑定 */
  llmModels?: LLMModel[];
  submitting?: boolean;
  submitLabel?: string;
  onSubmit: (payload: AgentCreatePayload) => void | Promise<void>;
}

export default function AgentForm({
  initial = null,
  existingSlugs = [],
  a2aEndpoints = [],
  mcpServers = [],
  skills = [],
  llmModels = [],
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
  // 技能绑定：只保存注册表 id，正文由后端按快照解析
  const [selectedSkillIds, setSelectedSkillIds] = useState<number[]>(
    initial?.skill_ids ?? [],
  );
  // 手动绑定（未在注册表登记）：与勾选并存
  const [manualTargets, setManualTargets] = useState<A2ATargetInput[]>([]);
  const [manualMcp, setManualMcp] = useState<ManualMcpServerInput[]>([]);
  // 模型绑定：单选；null = 回落全局 LLM_* 环境变量
  const [modelId, setModelId] = useState<number | null>(initial?.model_id ?? null);
  const [modelTemperature, setModelTemperature] = useState<string>(
    typeof initial?.model_snapshot?.temperature === "number"
      ? String(initial.model_snapshot.temperature)
      : "",
  );
  const [modelMaxTokens, setModelMaxTokens] = useState<string>(
    typeof initial?.model_snapshot?.max_tokens === "number"
      ? String(initial.model_snapshot.max_tokens)
      : "",
  );
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

  const toggleSkill = (id: number) => {
    setSelectedSkillIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  /**
   * 已绑定、但当前审核未通过的技能（例：在「Skill 管理」里用覆盖导入，审核被重置为 pending）。
   *
   * 只处理「能确认存在且非 approved」的 id：技能列表加载失败时 skills 为空，此时不做任何
   * 剔除，避免把绑定误删。
   */
  const blockedSkillIds = selectedSkillIds.filter((id) =>
    skills.some((s) => s.id === id && s.review_status !== "approved"),
  );
  const blockedSkills = skills.filter((s) => blockedSkillIds.includes(s.id));
  /**
   * 随本次保存提交的绑定（只含审核通过的技能）。
   *
   * 这类技能的复选框按「只有 approved 可勾选」是 disabled 的，用户无法取消勾选；若原样提交，
   * 后端门禁（validate_skill_bindings 的 review_status 分支）会 400，导致整个表单（连改名）
   * 都保存不了。因此保存时自动解绑，并在分区内明确提示。
   */
  const submittedSkillIds = selectedSkillIds.filter((id) => !blockedSkillIds.includes(id));

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
      skill_ids: submittedSkillIds,
      a2a_targets: targets,
      mcp_servers: mcps,
      system_prompt: systemPrompt.trim() ? systemPrompt : null,
      // 模型绑定：整体替换语义（model_id 为 null = 清除绑定回落全局）
      model: {
        model_id: modelId,
        temperature: modelTemperature === "" ? null : Number(modelTemperature),
        max_tokens: modelMaxTokens === "" ? null : Number(modelMaxTokens),
      },
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

        {/* 技能：从注册表勾选（只有审核通过的可以绑定） */}
        <Box>
          <Typography variant="subtitle1" gutterBottom>
            技能（Skills）
          </Typography>
          <Typography variant="caption" color="text.secondary">
            勾选后技能以方法论形式注入 Agent 编排。只有「已通过」审核的技能可选；技能在
            「Skill 管理」中统一维护。
          </Typography>
          {/* 预算按「本次保存实际提交的绑定」估算：未通过审核的技能不会提交，也不参与运行时注入 */}
          {(() => {
            const residentBytes = estimateResidentBytes(
              skills.filter((s) => submittedSkillIds.includes(s.id)),
            );
            return (
              <Box sx={{ mt: 0.5 }}>
                <Typography variant="caption" sx={{ display: "block" }}>
                  常驻预算预估：{formatBytes(residentBytes)}（UTF-8 字节口径，仅统计 always 技能正文）
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                  后端保存校验：绑定技能正文合计 ≤ {formatBytes(MAX_BINDING_CONTENT_BYTES)}
                  （UTF-8 字节，含按需技能正文）
                </Typography>
              </Box>
            );
          })()}

          {blockedSkills.length > 0 && (
            <Alert severity="warning" sx={{ mt: 1.5 }}>
              {`已绑定但当前未通过审核：${blockedSkills
                .map((s) => `${s.name}（${SKILL_STATUS_LABEL[s.review_status] ?? "未通过审核"}）`)
                .join("、")}。本次保存会自动解绑这些技能，审核通过后可在本节再次勾选。`}
            </Alert>
          )}

          {skills.length === 0 ? (
            <Alert
              severity="info"
              sx={{ mt: 1.5 }}
              action={
                <Button component={Link} href="/admin/skills" size="small">
                  前往 Skill 管理
                </Button>
              }
            >
              还没有任何 Skill，先去「Skill 管理」导入
            </Alert>
          ) : (
            <Stack sx={{ mt: 1 }}>
              {skills.map((skill) => (
                <FormControlLabel
                  key={skill.id}
                  control={
                    <Checkbox
                      size="small"
                      checked={selectedSkillIds.includes(skill.id)}
                      onChange={() => toggleSkill(skill.id)}
                      disabled={skill.review_status !== "approved"}
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2">
                        {skill.name}
                        {skill.load_mode === "always" && (
                          <Chip size="small" label="常驻" sx={{ ml: 0.5, height: 18 }} />
                        )}
                        {skill.review_status !== "approved" && (
                          <Chip
                            size="small"
                            label={SKILL_STATUS_LABEL[skill.review_status] ?? "未通过审核"}
                            sx={{ ml: 0.5, height: 18 }}
                          />
                        )}
                      </Typography>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ display: "block" }}
                      >
                        {skill.description}
                      </Typography>
                    </Box>
                  }
                />
              ))}
            </Stack>
          )}
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

        {/* 模型：从注册表单选（不选则使用全局配置） */}
        <Box>
          <Typography variant="subtitle1" gutterBottom>
            模型
          </Typography>
          <Typography variant="caption" color="text.secondary">
            绑定后该 Agent 使用指定模型，可覆盖推理参数；不选则回落全局 LLM_* 配置。模型在
            「模型管理」中统一维护。
          </Typography>
          <Stack spacing={2} sx={{ mt: 1.5 }}>
            <FormControl fullWidth size="small">
              <InputLabel id="agent-model-label">模型</InputLabel>
              <Select
                labelId="agent-model-label"
                label="模型"
                value={modelId === null ? "" : String(modelId)}
                onChange={(e) => setModelId(e.target.value === "" ? null : Number(e.target.value))}
              >
                <MenuItem value="">不指定（使用全局配置）</MenuItem>
                {llmModels.map((m) => (
                  <MenuItem key={m.id} value={String(m.id)}>
                    {m.name}（{m.model}）
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            {modelId !== null && (
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <TextField
                  label="Temperature 覆盖"
                  type="number"
                  size="small"
                  fullWidth
                  slotProps={{ htmlInput: { step: "0.1", min: 0, max: 2 } }}
                  value={modelTemperature}
                  onChange={(e) => setModelTemperature(e.target.value)}
                  helperText="留空使用运行时默认"
                />
                <TextField
                  label="Max Tokens 覆盖"
                  type="number"
                  size="small"
                  fullWidth
                  slotProps={{ htmlInput: { min: 1 } }}
                  value={modelMaxTokens}
                  onChange={(e) => setModelMaxTokens(e.target.value)}
                  helperText="留空使用运行时默认"
                />
              </Stack>
            )}
            {modelId !== null && !llmModels.some((m) => m.id === modelId) && (
              <Alert severity="warning">
                已绑定的模型不在当前列表中（可能已被删除）；保存后将回落全局配置。
              </Alert>
            )}
          </Stack>
          {llmModels.length === 0 && (
            <Alert
              severity="info"
              sx={{ mt: 1.5 }}
              action={
                <Button component={Link} href="/admin/models" size="small">
                  前往模型管理
                </Button>
              }
            >
              尚未登记任何模型，当前 Agent 将使用全局 LLM_* 配置。
            </Alert>
          )}
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
