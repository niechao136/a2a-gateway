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
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import { styled } from "@mui/material/styles";
import AddIcon from "@mui/icons-material/Add";
import BoltIcon from "@mui/icons-material/Bolt";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import { ApiError, LLM_PROVIDER_LABELS, LLMModel, adminApi } from "@/lib/adminApi";
import ModelDialog from "@/components/admin/ModelDialog";
import { useIsMobile, useIsTablet } from "@/lib/breakpoints";

const MonoText = styled(Typography)({
  fontFamily: "monospace",
  fontSize: 12,
  wordBreak: "break-all",
});

/** 模型注册表管理页：集中登记可复用的 LLM 模型，Agent 侧单选绑定。 */
export default function ModelsAdminPage() {
  const isMobile = useIsMobile();
  const showOptionalCols = !useIsTablet(); // 平板档隐藏「Base URL」「API Key」
  const [items, setItems] = useState<LLMModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<LLMModel | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await adminApi.listModels());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载模型列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };

  const openEdit = (item: LLMModel) => {
    setEditing(item);
    setDialogOpen(true);
  };

  const handleTest = async (item: LLMModel) => {
    setBusyId(item.id);
    setError(null);
    try {
      const res = await adminApi.testModel(item.id);
      setToast(res.ok ? `✅ ${res.message}` : `❌ ${res.message}`);
    } catch (err) {
      setToast(`❌ ${err instanceof Error ? err.message : "测试失败"}`);
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (item: LLMModel) => {
    if (!window.confirm(`确认删除模型「${item.name}」？`)) return;
    setBusyId(item.id);
    setError(null);
    try {
      await adminApi.deleteModel(item.id);
      setToast("已删除");
      await load();
      return;
    } catch (err) {
      const message = err instanceof Error ? err.message : "删除失败";
      const inUse = err instanceof ApiError && err.status === 409;
      if (inUse && window.confirm(`${message}\n\n是否强制删除，并从所有引用它的 Agent 上自动解绑？`)) {
        try {
          await adminApi.deleteModel(item.id, true);
          setToast("已强制删除并解绑");
          await load();
          return;
        } catch (err2) {
          setError(err2 instanceof Error ? err2.message : "强制删除失败");
        }
      } else {
        setError(message);
      }
    } finally {
      setBusyId(null);
    }
  };

  /** 列表操作区（移动端卡片与桌面表格共用） */
  const renderActions = (item: LLMModel) => (
    <>
      {busyId === item.id && <CircularProgress size={16} sx={{ mr: 0.5 }} />}
      <Tooltip title="测试连接">
        <IconButton size="small" onClick={() => handleTest(item)} disabled={busyId === item.id}>
          <BoltIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title="编辑">
        <IconButton size="small" onClick={() => openEdit(item)} disabled={busyId === item.id}>
          <EditOutlinedIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title="删除">
        <IconButton
          size="small"
          color="error"
          onClick={() => handleDelete(item)}
          disabled={busyId === item.id}
        >
          <DeleteOutlinedIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </>
  );

  const hasCustomUrl = (item: LLMModel) => item.provider === "openai" || !!item.base_url;

  return (
    <>
      <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1, flexWrap: "wrap", mb: 2 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="h6">模型管理</Typography>
          <Typography variant="caption" color="text.secondary">
            集中登记 LLM 模型（OpenAI 兼容 / Anthropic）；Agent 侧单选绑定，未绑定时回落全局配置。
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<AddIcon />} onClick={openCreate}>
          新建模型
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
              暂无模型，点击右上角「新建模型」添加
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
                    label={LLM_PROVIDER_LABELS[item.provider] ?? item.provider}
                    color={item.provider === "anthropic" ? "secondary" : "primary"}
                    variant="outlined"
                  />
                </Box>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                  {item.model}
                  {item.updated_at ? ` · ${new Date(item.updated_at).toLocaleString()}` : ""}
                </Typography>
                {hasCustomUrl(item) && (
                  <MonoText variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                    {item.base_url || "（官方默认地址）"}
                  </MonoText>
                )}
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "flex-end",
                    alignItems: "center",
                    gap: 0.5,
                    mt: 1.5,
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
                <TableCell>供应商</TableCell>
                <TableCell>模型标识</TableCell>
                {showOptionalCols && <TableCell>Base URL</TableCell>}
                {showOptionalCols && <TableCell>API Key</TableCell>}
                <TableCell align="right">操作</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={showOptionalCols ? 6 : 4} align="center" sx={{ py: 4 }}>
                    <CircularProgress size={24} />
                  </TableCell>
                </TableRow>
              ) : items.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={showOptionalCols ? 6 : 4}
                    align="center"
                    sx={{ py: 4, color: "text.secondary" }}
                  >
                    暂无模型，点击右上角「新建模型」添加
                  </TableCell>
                </TableRow>
              ) : (
                items.map((item) => (
                  <TableRow key={item.id} hover>
                    <TableCell>
                      <Typography variant="body2">{item.name}</Typography>
                      {item.description && (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          noWrap
                          sx={{ display: "block", maxWidth: 240 }}
                        >
                          {item.description}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={LLM_PROVIDER_LABELS[item.provider] ?? item.provider}
                        color={item.provider === "anthropic" ? "secondary" : "primary"}
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell>
                      <MonoText variant="caption">{item.model}</MonoText>
                    </TableCell>
                    {showOptionalCols && (
                      <TableCell sx={{ maxWidth: 260 }}>
                        <MonoText variant="caption" color="text.secondary">
                          {item.base_url || "（官方默认）"}
                        </MonoText>
                      </TableCell>
                    )}
                    {showOptionalCols && (
                      <TableCell>
                        <MonoText variant="caption" color="text.secondary">
                          {item.api_key_masked || "-"}
                        </MonoText>
                      </TableCell>
                    )}
                    <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                      {renderActions(item)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {/* 仅在打开时挂载，弹窗初始值直接由 initial 决定 */}
      {dialogOpen && (
        <ModelDialog
          initial={editing}
          existingNames={items.map((i) => i.name)}
          onClose={() => setDialogOpen(false)}
          onSaved={load}
        />
      )}

      <Snackbar
        open={!!toast}
        autoHideDuration={3200}
        onClose={() => setToast(null)}
        message={toast ?? ""}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      />
    </>
  );
}
