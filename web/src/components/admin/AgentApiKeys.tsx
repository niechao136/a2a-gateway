"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import ContentCopyOutlinedIcon from "@mui/icons-material/ContentCopyOutlined";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import { ApiError, ApiKey, adminApi } from "@/lib/adminApi";

/** Agent 编辑页内的 API Key 管理：该 Agent 对外 A2A 服务的调用凭据。 */
export default function AgentApiKeys({ agentId }: { agentId: number }) {
  const [items, setItems] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await adminApi.listAgentApiKeys(agentId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 API Key 失败");
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const copyKey = async (key: string) => {
    try {
      await navigator.clipboard.writeText(key);
    } catch {
      setError("复制失败，请手动选择复制");
    }
  };

  const handleCreate = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setCreating(true);
    setError(null);
    try {
      const created = await adminApi.createAgentApiKey(agentId, { name: trimmed });
      setName("");
      await load();
      return created;
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
      return null;
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = (item: ApiKey) => {
    if (item.is_default) return;
    if (!window.confirm(`确认删除 API Key「${item.name}」？删除后使用该 Key 的调用方将无法继续访问。`)) {
      return;
    }
    setBusyId(item.id);
    setError(null);
    void adminApi
      .deleteAgentApiKey(agentId, item.id)
      .then(load)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 400) {
          setError(err.message);
        } else {
          setError(err instanceof Error ? err.message : "删除失败");
        }
      })
      .finally(() => setBusyId(null));
  };

  return (
    <Box sx={{ mt: 4 }}>
      <Box sx={{ display: "flex", alignItems: "flex-start", gap: 2, mb: 1.5 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="subtitle1">API Key（A2A 调用凭据）</Typography>
          <Typography variant="caption" color="text.secondary">
            其他 Agent 通过 A2A 调用本 Agent（/a2a/...）时需携带本 Agent 的 Key：请求头
            <code style={{ margin: "0 4px" }}>X-Api-Key</code>或
            <code style={{ margin: "0 4px" }}>Authorization: Bearer</code>。
          </Typography>
        </Box>
      </Box>
      <Divider sx={{ mb: 2 }} />

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <Stack direction="row" sx={{ mb: 2 }} spacing={1}>
        <TextField
          size="small"
          label="新 Key 名称"
          placeholder="如：ci 调用 / 某服务专用"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={creating}
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleCreate();
          }}
          sx={{ width: 280 }}
        />
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => void handleCreate()}
          disabled={creating || !name.trim()}
          sx={{ alignSelf: "center" }}
        >
          新建 Key
        </Button>
      </Stack>

      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>名称</TableCell>
            <TableCell>Key</TableCell>
            <TableCell>状态</TableCell>
            <TableCell align="right">操作</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={4} align="center" sx={{ py: 3 }}>
                <CircularProgress size={22} />
              </TableCell>
            </TableRow>
          ) : items.length === 0 ? (
            <TableRow>
              <TableCell colSpan={4} align="center" sx={{ py: 3, color: "text.secondary" }}>
                暂无 API Key，保存后会自动生成默认 Key
              </TableCell>
            </TableRow>
          ) : (
            items.map((item) => (
              <TableRow key={item.id} hover>
                <TableCell>
                  <Typography variant="body2">{item.name}</Typography>
                </TableCell>
                <TableCell>
                  <Stack direction="row" sx={{ alignItems: "center" }} spacing={0.5}>
                    <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
                      {item.key}
                    </Typography>
                    <Tooltip title="复制">
                      <IconButton size="small" onClick={() => void copyKey(item.key)}>
                        <ContentCopyOutlinedIcon sx={{ fontSize: 16 }} />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                </TableCell>
                <TableCell>
                  <Chip
                    size="small"
                    label={item.is_default ? "默认" : "自定义"}
                    color={item.is_default ? "primary" : "default"}
                  />
                </TableCell>
                <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                  {busyId === item.id && <CircularProgress size={16} sx={{ mr: 1 }} />}
                  <Tooltip title={item.is_default ? "默认 Key 不可删除" : "删除"}>
                    <span>
                      <IconButton
                        size="small"
                        color="error"
                        onClick={() => handleDelete(item)}
                        disabled={busyId === item.id || item.is_default}
                      >
                        <DeleteOutlinedIcon fontSize="small" />
                      </IconButton>
                    </span>
                  </Tooltip>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </Box>
  );
}
