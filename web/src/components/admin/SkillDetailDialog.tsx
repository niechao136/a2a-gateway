"use client";

import { useState } from "react";
import {
  Box,
  Chip,
  Dialog,
  DialogContent,
  DialogTitle,
  Divider,
  Typography,
} from "@mui/material";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Skill } from "@/lib/adminApi";
import { formatBytes } from "@/lib/skillUtils";
import ScriptRunPanel from "./ScriptRunPanel";

interface Props {
  skill: Skill;
  open: boolean;
  onClose: () => void;
}

/** Skill 详情弹窗（规格 §10.2）：摘要 + Markdown 正文 + 附件表 + 试跑面板。 */
export default function SkillDetailDialog({ skill, open, onClose }: Props) {
  const [previewPath, setPreviewPath] = useState<string | null>(null);

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>
        {skill.name}
        <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
          {skill.load_mode === "always" ? "常驻" : "按需"} · {formatBytes(skill.size_bytes)} ·{" "}
          {skill.file_count} 附件 · 来源 {skill.source}
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" sx={{ mb: 1 }}>
          {skill.description}
        </Typography>
        <Chip size="small" label={skill.review_status} sx={{ mr: 0.5 }} />
        <Chip size="small" label={skill.enabled ? "启用" : "停用"} sx={{ mr: 0.5 }} />
        <Chip
          size="small"
          color={skill.allow_scripts ? "warning" : "default"}
          label={skill.allow_scripts ? "允许脚本执行" : "禁止脚本执行"}
        />
        <Divider sx={{ my: 1.5 }} />
        <Box
          sx={{
            "& pre": { bgcolor: "grey.100", p: 1, overflowX: "auto", fontSize: 13 },
            "& code": { fontSize: 13 },
          }}
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{skill.content}</ReactMarkdown>
        </Box>
        <Divider sx={{ my: 1.5 }} />
        <Typography variant="subtitle2" gutterBottom>
          附件
        </Typography>
        {skill.files.length === 0 ? (
          <Typography variant="caption" color="text.secondary">
            （无附件）
          </Typography>
        ) : (
          skill.files.map((f) => (
            <Box key={f.path} sx={{ py: 0.5 }}>
              <Chip
                size="small"
                color={f.entry_type === "script" ? "warning" : "default"}
                label={f.entry_type === "script" ? "脚本" : "文本"}
                sx={{ mr: 1 }}
              />
              <Typography component="span" variant="body2">
                {f.path}
              </Typography>
              <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                {formatBytes(f.size)}
              </Typography>
              {f.entry_type !== "script" && (
                <Typography
                  component="span"
                  variant="caption"
                  color="primary"
                  sx={{ ml: 1, cursor: "pointer" }}
                  onClick={() => setPreviewPath(previewPath === f.path ? null : f.path)}
                >
                  {previewPath === f.path ? "收起" : "预览"}
                </Typography>
              )}
              {previewPath === f.path && (
                <Box
                  sx={{
                    "& pre": {
                      bgcolor: "grey.100",
                      p: 1,
                      overflowX: "auto",
                      fontSize: 13,
                      whiteSpace: "pre-wrap",
                    },
                  }}
                >
                  <pre>{f.content ?? ""}</pre>
                </Box>
              )}
            </Box>
          ))
        )}
        <ScriptRunPanel skill={skill} />
      </DialogContent>
    </Dialog>
  );
}
