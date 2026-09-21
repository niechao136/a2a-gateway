"use client";

import { useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  MenuItem,
  Paper,
  TextField,
  Typography,
} from "@mui/material";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import { ApiError, Skill, SkillScriptRunResult, adminApi } from "@/lib/adminApi";

interface Props {
  skill: Skill;
}

/** 脚本试跑面板（规格 §10.2）：pending 状态也可试跑，辅助审核。 */
export default function ScriptRunPanel({ skill }: Props) {
  const scripts = useMemo(
    () => skill.files.filter((f) => f.entry_type === "script"),
    [skill.files],
  );
  const [scriptPath, setScriptPath] = useState("");
  const [argv, setArgv] = useState("");
  const [stdin, setStdin] = useState("");
  const [timeoutS, setTimeoutS] = useState(30);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SkillScriptRunResult | null>(null);

  if (scripts.length === 0) return null;

  const run = async () => {
    if (!scriptPath) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(
        await adminApi.runSkillScript(skill.id, {
          path: scriptPath,
          argv: argv.trim() ? argv.trim().split(/\s+/) : [],
          stdin,
          timeout_s: timeoutS,
        }),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "试跑失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Paper variant="outlined" sx={{ p: 2, mt: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        脚本试跑（沙箱内执行：无网络、无凭据，仅标准库可用）
      </Typography>
      <Box sx={{ display: "flex", gap: 1.5, flexWrap: "wrap", alignItems: "center" }}>
        <TextField
          select
          size="small"
          label="脚本"
          sx={{ minWidth: 220 }}
          value={scriptPath}
          onChange={(e) => setScriptPath(e.target.value)}
        >
          {scripts.map((f) => (
            <MenuItem key={f.path} value={f.path}>
              {f.path}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          size="small"
          label="argv（空格分隔）"
          sx={{ width: 200 }}
          value={argv}
          onChange={(e) => setArgv(e.target.value)}
        />
        <TextField
          size="small"
          type="number"
          label="超时（秒）"
          sx={{ width: 110 }}
          value={timeoutS}
          onChange={(e) => setTimeoutS(Math.max(1, Math.min(120, Number(e.target.value) || 30)))}
        />
        <Button
          variant="contained"
          startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <PlayArrowIcon />}
          onClick={() => void run()}
          disabled={busy || !scriptPath}
        >
          试跑
        </Button>
      </Box>
      <TextField
        size="small"
        label="stdin（可选）"
        fullWidth
        multiline
        minRows={2}
        margin="normal"
        value={stdin}
        onChange={(e) => setStdin(e.target.value)}
      />
      {error && (
        <Alert severity="error" sx={{ mt: 1 }}>
          {error}
        </Alert>
      )}
      {result && (
        <Box sx={{ mt: 1.5 }}>
          <Typography variant="caption" color="text.secondary">
            exit_code: {result.exit_code ?? "null（超时被杀）"} · 耗时 {result.duration_ms}ms
            {result.timeout && " · 超时"} {result.truncated && " · 输出已截断"}
            {result.error && ` · 错误: ${result.error}`}
          </Typography>
          <TextField
            label="stdout"
            fullWidth
            multiline
            minRows={3}
            margin="normal"
            value={result.stdout}
            slotProps={{ input: { readOnly: true } }}
          />
          {result.stderr && (
            <TextField
              label="stderr"
              fullWidth
              multiline
              minRows={2}
              margin="normal"
              value={result.stderr}
              slotProps={{ input: { readOnly: true } }}
            />
          )}
        </Box>
      )}
    </Paper>
  );
}
