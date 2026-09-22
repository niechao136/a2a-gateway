"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Snackbar,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import EditIcon from "@mui/icons-material/Edit";
import VisibilityIcon from "@mui/icons-material/Visibility";
import { ApiError, Skill, SkillReviewStatus, adminApi } from "@/lib/adminApi";
import SkillDetailDialog from "@/components/admin/SkillDetailDialog";
import SkillEditDialog from "@/components/admin/SkillEditDialog";
import SkillImportDialog from "@/components/admin/SkillImportDialog";
import { formatBytes } from "@/lib/skillUtils";
import { useIsMobile, useIsTablet } from "@/lib/breakpoints";

const STATUS_CHIP: Record<SkillReviewStatus, { label: string; color: "warning" | "success" | "error" }> = {
  pending: { label: "待审核", color: "warning" },
  approved: { label: "已通过", color: "success" },
  rejected: { label: "已拒绝", color: "error" },
};

/** Skill 注册表管理页：导入、审核、删除；只有「已通过」的技能可被 Agent 勾选。 */
export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const isMobile = useIsMobile();
  const showOptionalCols = !useIsTablet(); // 平板档隐藏「加载模式」「来源」
  const [importOpen, setImportOpen] = useState(false);
  const [detailSkill, setDetailSkill] = useState<Skill | null>(null);
  const [editSkill, setEditSkill] = useState<Skill | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSkills(await adminApi.listSkills());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "加载 Skill 失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const review = async (skill: Skill, status: SkillReviewStatus) => {
    setBusyId(skill.id);
    setError(null);
    try {
      await adminApi.reviewSkill(skill.id, { status });
      setToast(status === "approved" ? `已通过「${skill.name}」` : `已拒绝「${skill.name}」`);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "审核失败");
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (skill: Skill) => {
    if (!window.confirm(`确认删除技能「${skill.name}」？`)) return;
    setBusyId(skill.id);
    setError(null);
    try {
      await adminApi.deleteSkill(skill.id, false);
      setToast("已删除");
      await load();
      return;
    } catch (e) {
      const message = e instanceof ApiError ? e.message : "删除失败";
      // 409 被引用：引导强制删除（自动从所有 Agent 解绑）
      const inUse = e instanceof ApiError && e.status === 409;
      if (
        inUse &&
        window.confirm(`${message}\n\n是否强制删除，并从所有引用它的 Agent 上自动解绑？`)
      ) {
        try {
          await adminApi.deleteSkill(skill.id, true);
          setToast("已强制删除并解绑");
          await load();
          return;
        } catch (e2) {
          setError(e2 instanceof ApiError ? e2.message : "强制删除失败");
        }
      } else {
        setError(message);
      }
    } finally {
      setBusyId(null);
    }
  };

  return (
    <>
      <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1, flexWrap: "wrap", mb: 2 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="h6">Skill 管理</Typography>
          <Typography variant="caption" color="text.secondary">
            集中导入并审核 Skill；只有「已通过」的技能可以在 Agent 表单中被勾选绑定。
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => setImportOpen(true)}>
          导入 Skill
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
          ) : skills.length === 0 ? (
            <Typography variant="body2" color="text.secondary" align="center" sx={{ py: 4 }}>
              还没有导入任何 Skill，点击右上角「导入 Skill」添加
            </Typography>
          ) : (
            skills.map((skill) => (
              <Paper key={skill.id} variant="outlined" sx={{ p: 2 }}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Typography variant="body1" sx={{ flex: 1, minWidth: 0, fontWeight: 600 }} noWrap>
                    {skill.name}
                  </Typography>
                  <Chip size="small" {...STATUS_CHIP[skill.review_status]} />
                </Box>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                  {skill.load_mode === "always" ? "常驻" : "按需"} · {formatBytes(skill.size_bytes)} /{" "}
                  {skill.file_count} 附件 · {skill.source}
                </Typography>
                {skill.description && (
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{
                      mt: 0.5,
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }}
                  >
                    {skill.description}
                  </Typography>
                )}
                {!skill.enabled && <Chip size="small" label="已停用" sx={{ mt: 1 }} />}
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "flex-end",
                    alignItems: "center",
                    gap: 0.5,
                    mt: 1.5,
                    minHeight: 44,
                    flexWrap: "wrap",
                  }}
                >
                  {busyId === skill.id && <CircularProgress size={16} sx={{ mr: 0.5 }} />}
                  <Button size="small" startIcon={<VisibilityIcon />} onClick={() => setDetailSkill(skill)}>
                    详情
                  </Button>
                  <Button
                    size="small"
                    startIcon={<EditIcon />}
                    onClick={() => setEditSkill(skill)}
                    disabled={busyId === skill.id}
                  >
                    编辑
                  </Button>
                  {skill.review_status !== "approved" && (
                    <Button size="small" onClick={() => void review(skill, "approved")} disabled={busyId === skill.id}>
                      通过
                    </Button>
                  )}
                  {skill.review_status !== "rejected" && (
                    <Button
                      size="small"
                      color="warning"
                      onClick={() => void review(skill, "rejected")}
                      disabled={busyId === skill.id}
                    >
                      拒绝
                    </Button>
                  )}
                  <Button size="small" color="error" onClick={() => void remove(skill)} disabled={busyId === skill.id}>
                    删除
                  </Button>
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
              <TableCell>说明</TableCell>
              {showOptionalCols && <TableCell>加载模式</TableCell>}
              <TableCell>大小</TableCell>
              {showOptionalCols && <TableCell>来源</TableCell>}
              <TableCell>状态</TableCell>
              <TableCell align="right">操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={showOptionalCols ? 7 : 5} align="center" sx={{ py: 4 }}>
                  <CircularProgress size={24} />
                </TableCell>
              </TableRow>
            ) : skills.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={showOptionalCols ? 7 : 5}
                  align="center"
                  sx={{ py: 4, color: "text.secondary" }}
                >
                  还没有导入任何 Skill，点击右上角「导入 Skill」添加
                </TableCell>
              </TableRow>
            ) : (
              skills.map((skill) => (
                <TableRow key={skill.id} hover>
                  <TableCell>
                    <Typography variant="body2">{skill.name}</Typography>
                    {!skill.enabled && (
                      <Chip size="small" label="已停用" sx={{ mt: 0.5 }} />
                    )}
                  </TableCell>
                  <TableCell sx={{ maxWidth: 240 }}>
                    <Typography variant="caption" color="text.secondary">
                      {skill.description}
                    </Typography>
                  </TableCell>
                  {showOptionalCols && (
                    <TableCell>
                      <Typography variant="caption" color="text.secondary">
                        {skill.load_mode === "always" ? "常驻" : "按需"}
                      </Typography>
                    </TableCell>
                  )}
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {formatBytes(skill.size_bytes)} / {skill.file_count} 附件
                    </Typography>
                  </TableCell>
                  {showOptionalCols && (
                    <TableCell>
                      <Typography variant="caption" color="text.secondary">
                        {skill.source}
                      </Typography>
                    </TableCell>
                  )}
                  <TableCell>
                    <Chip size="small" {...STATUS_CHIP[skill.review_status]} />
                  </TableCell>
                  <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                    {busyId === skill.id && <CircularProgress size={16} sx={{ mr: 1 }} />}
                    <Button
                      size="small"
                      startIcon={<VisibilityIcon />}
                      onClick={() => setDetailSkill(skill)}
                    >
                      详情
                    </Button>
                    <Button
                      size="small"
                      startIcon={<EditIcon />}
                      onClick={() => setEditSkill(skill)}
                      disabled={busyId === skill.id}
                    >
                      编辑
                    </Button>
                    {skill.review_status !== "approved" && (
                      <Button
                        size="small"
                        onClick={() => void review(skill, "approved")}
                        disabled={busyId === skill.id}
                      >
                        通过
                      </Button>
                    )}
                    {skill.review_status !== "rejected" && (
                      <Button
                        size="small"
                        color="warning"
                        onClick={() => void review(skill, "rejected")}
                        disabled={busyId === skill.id}
                      >
                        拒绝
                      </Button>
                    )}
                    <Button
                      size="small"
                      color="error"
                      onClick={() => void remove(skill)}
                      disabled={busyId === skill.id}
                    >
                      删除
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableContainer>
      )}

      {/* 仅在打开时挂载，弹窗每次打开都是干净的初始态 */}
      {importOpen && (
        <SkillImportDialog
          open
          onClose={() => setImportOpen(false)}
          onImported={() => void load()}
        />
      )}
      {detailSkill && (
        <SkillDetailDialog skill={detailSkill} open onClose={() => setDetailSkill(null)} />
      )}
      {editSkill && (
        <SkillEditDialog
          skill={editSkill}
          open
          onClose={() => setEditSkill(null)}
          onSaved={() => {
            setToast("已保存（内容变更时审核状态已重置为 pending）");
            void load();
          }}
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
