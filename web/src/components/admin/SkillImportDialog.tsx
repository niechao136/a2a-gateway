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
  DialogTitle,
  FormControlLabel,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import {
  ApiError,
  SkillImportPayload,
  SkillImportPreview,
  SkillImportPreviewItem,
  adminApi,
} from "@/lib/adminApi";
import { formatBytes } from "@/lib/skillUtils";

interface Props {
  open: boolean;
  onClose: () => void;
  onImported: () => void;
}

/** 单文件上限（与后端 MAX_FILE_BYTES 一致），超限文件不上报，避免整包被后端拒收。 */
const MAX_FILE_BYTES = 1024 * 1024;

/** 前端直接跳过的二进制扩展名（后端另按内容判定，这里只是少传一些无用字节）。 */
const BINARY_EXT = /\.(png|jpe?g|gif|webp|pdf|woff2?|ttf|otf|zip|gz|exe|dll|so)$/i;

/** 前端目录上传：webkitdirectory 上报的 File[] → [{path, content}]（仅文本）。 */
async function readDirFiles(fileList: FileList): Promise<{ path: string; content: string }[]> {
  const files = Array.from(fileList).filter((f) => f.size <= MAX_FILE_BYTES);
  const out: { path: string; content: string }[] = [];
  for (const file of files) {
    const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
    if (BINARY_EXT.test(rel)) continue; // 二进制跳过
    out.push({ path: rel, content: await file.text() });
  }
  return out;
}

/** File → 纯 base64（不带 data: 前缀，后端直接 base64.b64decode）。 */
async function toBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  // 分片拼接：避免超大包一次性构造字符串
  for (let i = 0; i < bytes.length; i += 8192) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  }
  return btoa(binary);
}

/** Skill 导入弹窗：粘贴 / URL / zip / 本地目录四种来源，预览后勾选落库。 */
export default function SkillImportDialog({ open, onClose, onImported }: Props) {
  const [tab, setTab] = useState(0);
  const [skillMd, setSkillMd] = useState("");
  const [url, setUrl] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<SkillImportPreview | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  /**
   * 落库必须与预览同源：后端 commit 会按 payload 重新解析一次
   * （无服务端预览态），zip/目录数据必须原样重发。
   */
  const [previewPayload, setPreviewPayload] = useState<SkillImportPayload | null>(null);

  const reset = () => {
    setSkillMd("");
    setUrl("");
    setPreview(null);
    setPreviewPayload(null);
    setSelected(new Set());
    setError("");
  };

  /** 由当前输入构造 payload（仅粘贴 / URL 两种来源）。 */
  const buildPayload = (): SkillImportPayload => {
    if (tab === 0) return { source: "text", skill_md: skillMd };
    return { source: "url", url: url.trim() };
  };

  const runPreview = async (payload: SkillImportPayload) => {
    setBusy(true);
    setError("");
    try {
      const result = await adminApi.previewSkillImport(payload);
      setPreviewPayload(payload);
      setPreview(result);
      setSelected(new Set(result.items.filter((i) => i.name && !i.error).map((i) => i.name)));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleDir = async (fileList: FileList | null) => {
    if (!fileList?.length) return;
    const dirFiles = await readDirFiles(fileList);
    if (dirFiles.length === 0) {
      setError("目录中没有可上传的文本文件（单文件需 ≤ 1 MB）");
      return;
    }
    await runPreview({ source: "dir", dir_files: dirFiles, overwrite });
  };

  const handleZip = async (file: File | null) => {
    if (!file) return;
    await runPreview({ source: "zip", zip_b64: await toBase64(file), overwrite });
  };

  const commit = async () => {
    if (!preview || !previewPayload) return;
    setBusy(true);
    setError("");
    try {
      await adminApi.commitSkillImport({
        ...previewPayload,
        overwrite,
        names: [...selected],
      });
      onImported();
      reset();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const canPreview = tab === 0 ? !!skillMd.trim() : !!url.trim();

  const renderItem = (item: SkillImportPreviewItem) => (
    <Box key={item.name || item.error} sx={{ py: 1, borderBottom: "1px dashed divider" }}>
      {item.error ? (
        <Alert severity="error">{item.error}</Alert>
      ) : (
        <FormControlLabel
          control={
            <Checkbox checked={selected.has(item.name)} onChange={() => toggle(item.name)} />
          }
          label={
            <Box>
              <Typography variant="body2">
                <strong>{item.name}</strong>
                {item.conflict && (
                  <Typography
                    component="span"
                    variant="caption"
                    color="warning.main"
                    sx={{ ml: 1 }}
                  >
                    已存在（覆盖将重置审核为 pending）
                  </Typography>
                )}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                {item.description} · {formatBytes(item.total_bytes)} · 附件 {item.file_count} 个
                {item.skipped_binary.length > 0 &&
                  ` · 已跳过 ${item.skipped_binary.length} 个非文本文件`}
              </Typography>
            </Box>
          }
        />
      )}
    </Box>
  );

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>导入 Skill</DialogTitle>
      <DialogContent>
        <Tabs
          value={tab}
          onChange={(_, v) => {
            setTab(v);
            setPreview(null);
            setPreviewPayload(null);
            setSelected(new Set());
            setError("");
          }}
        >
          <Tab label="粘贴" />
          <Tab label="URL" />
          <Tab label="zip" />
          <Tab label="目录" />
        </Tabs>

        {tab === 0 && (
          <TextField
            label="SKILL.md 全文"
            multiline
            minRows={8}
            fullWidth
            margin="normal"
            value={skillMd}
            onChange={(e) => setSkillMd(e.target.value)}
            placeholder={"---\nname: my-skill\ndescription: 说明\n---\n正文"}
          />
        )}
        {tab === 1 && (
          <TextField
            label="SKILL.md raw 地址或 zip 地址"
            fullWidth
            margin="normal"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://raw.githubusercontent.com/.../SKILL.md"
          />
        )}
        {tab === 2 && (
          <Button variant="outlined" component="label" sx={{ mt: 2 }} disabled={busy}>
            选择 zip 文件
            <input
              type="file"
              accept=".zip"
              hidden
              onChange={(e) => void handleZip(e.target.files?.[0] ?? null)}
            />
          </Button>
        )}
        {tab === 3 && (
          <>
            <Button variant="outlined" component="label" sx={{ mt: 2 }} disabled={busy}>
              选择本地目录
              <input
                type="file"
                hidden
                multiple
                // @ts-expect-error webkitdirectory 为非标准属性
                webkitdirectory=""
                onChange={(e) => void handleDir(e.target.files)}
              />
            </Button>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
              选择目录后自动读取其中的文本文件（二进制与超 1 MB 的文件会被跳过）。
            </Typography>
          </>
        )}

        <FormControlLabel
          sx={{ mt: 1 }}
          control={<Checkbox checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />}
          label="重名时覆盖（内容变更会重置审核为 pending）"
        />

        {error && (
          <Alert severity="error" sx={{ mt: 1 }}>
            {error}
          </Alert>
        )}
        {preview?.errors.map((e) => (
          <Alert key={e} severity="warning" sx={{ mt: 1 }}>
            {e}
          </Alert>
        ))}
        {preview && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="subtitle2">预览（勾选后落库）</Typography>
            {preview.items.map(renderItem)}
            {preview.items.length === 0 && (
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                该来源没有解析出任何 Skill
              </Typography>
            )}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        {tab <= 1 && (
          <Button
            onClick={() => void runPreview({ ...buildPayload(), overwrite })}
            disabled={busy || !canPreview}
          >
            预览
          </Button>
        )}
        <Button onClick={onClose}>取消</Button>
        <Button
          variant="contained"
          onClick={() => void commit()}
          disabled={busy || !preview || selected.size === 0}
          startIcon={busy ? <CircularProgress size={16} color="inherit" /> : null}
        >
          导入选中（{selected.size}）
        </Button>
      </DialogActions>
    </Dialog>
  );
}
