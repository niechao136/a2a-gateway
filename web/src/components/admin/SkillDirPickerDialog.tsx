"use client";

import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  FormControlLabel,
  Typography,
} from "@mui/material";
import {
  MAX_FILES,
  MAX_SKILL_BYTES,
  MAX_TEXT_BYTES,
  collectDirCandidates,
  stripTopDir,
  type DirCandidate,
  type DirEntryInput,
  type SkillFilePayload,
} from "@/lib/skillForm";
import { formatBytes } from "@/lib/skillUtils";
import DialogTitleBar from "./DialogTitleBar";
import { useIsMobile } from "@/lib/breakpoints";

interface Props {
  /** 现有附件路径：目录里的同名文件标为"已存在"且不可勾选。 */
  existingPaths: string[];
  /** 现有附件总字节：与选中的文件一起校验 4 MB 总量上限。 */
  existingSizeBytes: number;
  open: boolean;
  onClose: () => void;
  /** 确认后回传待加入的附件 payload。 */
  onAdd: (payloads: SkillFilePayload[]) => void;
}

/**
 * 读取目录内容 → 候选条目（path 已剥掉顶层目录名）。
 *
 * 超过 MAX_TEXT_BYTES 的文件不读进内存：任何类型超过该值都必然被 collectDirCandidates
 * 判为超限，只上报 size 即可 —— 目录里混进几百 MB 的视频时不该把标签页拖死。
 */
async function readDirEntries(fileList: FileList): Promise<DirEntryInput[]> {
  const out: DirEntryInput[] = [];
  for (const file of Array.from(fileList)) {
    const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
    const path = stripTopDir(rel);
    if (file.size > MAX_TEXT_BYTES) {
      out.push({ path, size: file.size });
      continue;
    }
    out.push({ path, bytes: new Uint8Array(await file.arrayBuffer()) });
  }
  return out;
}

/** 可勾选的候选：有 payload 且不与现有附件重名。 */
function selectableOf(items: DirCandidate[]): DirCandidate[] {
  return items.filter((c) => c.payload !== null && !c.duplicate);
}

/**
 * 整目录上传的勾选面板：列出目录里的文件与各自结果，默认全勾选可加入项。
 *
 * 跳过项（SKILL.md / 二进制 / 超限 / 路径非法）不阻断其余文件，单独列在下方并给原因；
 * 与现有附件重名的项不可勾选 —— 编辑弹窗的既有语义是"重复路径即报错"，
 * 要替换内容应直接编辑上方那条附件。
 */
export default function SkillDirPickerDialog({
  existingPaths,
  existingSizeBytes,
  open,
  onClose,
  onAdd,
}: Props) {
  const isMobile = useIsMobile();
  const [candidates, setCandidates] = useState<DirCandidate[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [picked, setPicked] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");

  const reset = () => {
    setCandidates([]);
    setSelected(new Set());
    setPicked(false);
    setError("");
  };

  const close = () => {
    reset();
    onClose();
  };

  const handleDir = async (fileList: FileList | null) => {
    if (!fileList?.length) return;
    setReading(true);
    setError("");
    try {
      const items = collectDirCandidates(await readDirEntries(fileList), existingPaths);
      setCandidates(items);
      setPicked(true);
      setSelected(new Set(selectableOf(items).map((i) => i.path)));
    } catch {
      setError("读取目录失败，请重试");
    } finally {
      setReading(false);
    }
  };

  const chosen = candidates.filter((c) => selected.has(c.path));
  const chosenBytes = chosen.reduce((sum, c) => sum + c.size, 0);
  const skipped = candidates.filter((c) => c.skipReason !== null);
  const duplicates = candidates.filter((c) => c.skipReason === null && c.duplicate);
  const overCount = existingPaths.length + chosen.length > MAX_FILES;
  const overBytes = existingSizeBytes + chosenBytes > MAX_SKILL_BYTES;

  const toggle = (path: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const confirm = () => {
    const payloads = chosen.map((c) => c.payload).filter((p): p is SkillFilePayload => p !== null);
    if (payloads.length === 0) return;
    onAdd(payloads);
    close();
  };

  return (
    <Dialog open={open} onClose={close} fullWidth maxWidth="sm" fullScreen={isMobile}>
      <DialogTitleBar title="从目录添加附件" onClose={close} />
      <DialogContent>
        <Button variant="outlined" component="label" sx={{ mt: 1 }} disabled={reading}>
          {picked ? "重新选择目录" : "选择本地目录"}
          <input
            type="file"
            hidden
            multiple
            // @ts-expect-error webkitdirectory 为非标准属性
            webkitdirectory=""
            onChange={(e) => {
              void handleDir(e.target.files);
              e.target.value = ""; // 允许重复选择同一目录
            }}
          />
        </Button>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 1, wordBreak: "break-word" }}
        >
          选中目录即技能包根，其中文件的相对层级会原样保留（如 references/a.md）；SKILL.md 不进附件。
        </Typography>

        {reading && (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, mt: 2 }}>
            <CircularProgress size={16} />
            <Typography variant="body2" color="text.secondary">
              正在读取目录…
            </Typography>
          </Box>
        )}
        {error && (
          <Alert severity="error" sx={{ mt: 1.5 }}>
            {error}
          </Alert>
        )}

        {picked && (
          <>
            <Box sx={{ mt: 2 }}>
              <Typography variant="subtitle2">
                可加入（{chosen.length}/{selectableOf(candidates).length} 个已选 ·{" "}
                {formatBytes(chosenBytes)}）
              </Typography>
              <Box sx={{ maxHeight: 260, overflowY: "auto" }}>
                {selectableOf(candidates).map((c) => (
                  <FormControlLabel
                    key={c.path}
                    sx={{ display: "flex", alignItems: "center", m: 0, py: 0.25 }}
                    control={
                      <Checkbox
                        size="small"
                        checked={selected.has(c.path)}
                        onChange={() => toggle(c.path)}
                      />
                    }
                    label={
                      <Typography variant="body2" sx={{ wordBreak: "break-all" }}>
                        {c.path}
                        <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                          {formatBytes(c.size)}
                        </Typography>
                      </Typography>
                    }
                  />
                ))}
                {selectableOf(candidates).length === 0 && (
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                    该目录没有可加入的文本文件。
                  </Typography>
                )}
              </Box>
            </Box>

            {duplicates.length > 0 && (
              <Box sx={{ mt: 2 }}>
                <Typography variant="subtitle2">已存在（需先在上方附件列表移除，或在原条目上编辑）</Typography>
                {duplicates.map((c) => (
                  <Typography
                    key={c.path}
                    variant="body2"
                    color="warning.main"
                    sx={{ wordBreak: "break-all" }}
                  >
                    {c.path}
                  </Typography>
                ))}
              </Box>
            )}

            {skipped.length > 0 && (
              <Box sx={{ mt: 2 }}>
                <Typography variant="subtitle2">已跳过（{skipped.length} 个）</Typography>
                <Box sx={{ maxHeight: 160, overflowY: "auto" }}>
                  {skipped.map((c) => (
                    <Typography
                      key={c.path}
                      variant="caption"
                      color="text.secondary"
                      sx={{ display: "block", wordBreak: "break-all" }}
                    >
                      {c.path} —— {c.skipReason}
                    </Typography>
                  ))}
                </Box>
              </Box>
            )}

            {(overCount || overBytes) && (
              <Alert severity="error" sx={{ mt: 1.5 }}>
                {overCount && `附件数将超过 ${MAX_FILES} 上限；`}
                {overBytes && `附件总量将超过 ${formatBytes(MAX_SKILL_BYTES)} 上限；`}
                请减少选中文件。
              </Alert>
            )}
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={close}>取消</Button>
        <Button
          variant="contained"
          onClick={confirm}
          disabled={chosen.length === 0 || overCount || overBytes}
        >
          加入选中（{chosen.length}）
        </Button>
      </DialogActions>
    </Dialog>
  );
}
