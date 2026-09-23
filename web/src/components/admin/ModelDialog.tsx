"use client";

import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  TextField,
} from "@mui/material";
import {
  LLM_PROVIDER_LABELS,
  LLMModel,
  LLMModelCreatePayload,
  LLMProvider,
  adminApi,
} from "@/lib/adminApi";
import DialogTitleBar from "./DialogTitleBar";
import { useIsMobile } from "@/lib/breakpoints";

interface ModelDialogProps {
  /** 传入则为编辑，否则为新建 */
  initial: LLMModel | null;
  existingNames: string[];
  onClose: () => void;
  onSaved: () => void;
}

const BASE_URL_HINT: Record<LLMProvider, string> = {
  openai: "例如：https://api.deepseek.com/v1（通常以 /v1 结尾）",
  anthropic: "留空使用官方默认 https://api.anthropic.com",
};

/**
 * 模型的新建/编辑弹窗。
 * 由父组件在打开时才挂载，初始值直接用 useState 初始化，无需回填副作用。
 */
export default function ModelDialog({
  initial,
  existingNames,
  onClose,
  onSaved,
}: ModelDialogProps) {
  const isEdit = !!initial;

  const [name, setName] = useState(initial?.name ?? "");
  const [provider, setProvider] = useState<LLMProvider>(initial?.provider ?? "openai");
  const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? "");
  // 编辑时不回显明文 key（后端只给脱敏值）；留空 = 保持原值
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(initial?.model ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [errors, setErrors] = useState<{ name?: string; baseUrl?: string; model?: string }>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  const isMobile = useIsMobile();

  const validate = (): boolean => {
    const next: typeof errors = {};
    const n = name.trim();
    if (!n) next.name = "请填写名称";
    else if (n !== initial?.name && existingNames.includes(n)) {
      next.name = `名称 '${n}' 已存在`;
    }
    if (!model.trim()) next.model = "请填写模型标识";
    if (provider === "openai" && !baseUrl.trim()) {
      next.baseUrl = "OpenAI 兼容端点必须填写 Base URL";
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const buildPayload = (): LLMModelCreatePayload => ({
    name: name.trim(),
    provider,
    base_url: baseUrl.trim(),
    model: model.trim(),
    description: description.trim(),
    // 编辑时留空不提交该字段（后端保持原值）
    ...(apiKey ? { api_key: apiKey } : {}),
  });

  const handleTest = async () => {
    if (!validate()) return;
    setTesting(true);
    setTestResult(null);
    try {
      const res = await adminApi.testModelForm(buildPayload());
      setTestResult(res);
    } catch (err) {
      setTestResult({
        ok: false,
        message: err instanceof Error ? err.message : "测试失败",
      });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    if (!validate()) return;
    setSaving(true);
    setError(null);
    try {
      if (isEdit && initial) await adminApi.updateModel(initial.id, buildPayload());
      else await adminApi.createModel(buildPayload());
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth fullScreen={isMobile}>
      <DialogTitleBar title={isEdit ? "编辑模型" : "新建模型"} onClose={onClose} />
      <DialogContent dividers>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2, pt: 1 }}>
          <TextField
            label="名称"
            required
            fullWidth
            size="small"
            value={name}
            onChange={(e) => setName(e.target.value)}
            error={!!errors.name}
            helperText={errors.name}
            placeholder="例如：DeepSeek V3"
          />

          <FormControl fullWidth size="small">
            <InputLabel id="llm-provider-label">供应商协议</InputLabel>
            <Select
              labelId="llm-provider-label"
              label="供应商协议"
              value={provider}
              onChange={(e) => setProvider(e.target.value as LLMProvider)}
            >
              {(Object.keys(LLM_PROVIDER_LABELS) as LLMProvider[]).map((value) => (
                <MenuItem key={value} value={value}>
                  {LLM_PROVIDER_LABELS[value]}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          <TextField
            label="Base URL"
            required={provider === "openai"}
            fullWidth
            size="small"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            error={!!errors.baseUrl}
            helperText={errors.baseUrl ?? BASE_URL_HINT[provider]}
            placeholder="https://api.deepseek.com/v1"
          />

          <TextField
            label="API Key"
            fullWidth
            size="small"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={isEdit ? `留空保持不变（当前 ${initial?.api_key_masked || "未设置"}）` : "必填"}
            helperText={isEdit ? "出于安全考虑不回显明文，留空表示不修改" : undefined}
          />

          <TextField
            label="模型标识"
            required
            fullWidth
            size="small"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            error={!!errors.model}
            helperText={errors.model}
            placeholder="例如：deepseek-chat / claude-sonnet-4-5"
          />

          <TextField
            label="备注"
            fullWidth
            size="small"
            multiline
            minRows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="可选，例如用途说明"
          />

          <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
            <Button
              variant="outlined"
              size="small"
              onClick={handleTest}
              disabled={testing}
              startIcon={testing ? <CircularProgress size={16} /> : undefined}
            >
              测试连接
            </Button>
            {testResult && (
              <Alert severity={testResult.ok ? "success" : "error"} sx={{ flex: 1, minWidth: 200 }}>
                {testResult.message}
              </Alert>
            )}
          </Box>

          {error && <Alert severity="error">{error}</Alert>}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>取消</Button>
        <Button variant="contained" onClick={handleSave} disabled={saving}>
          保存
        </Button>
      </DialogActions>
    </Dialog>
  );
}
