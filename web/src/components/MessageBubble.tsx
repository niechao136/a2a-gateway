"use client";

import { useState } from "react";
import { Box, Avatar, IconButton, Tooltip, Typography } from "@mui/material";
import ContentCopyOutlinedIcon from "@mui/icons-material/ContentCopyOutlined";
import CheckOutlinedIcon from "@mui/icons-material/CheckOutlined";
import ReplayOutlinedIcon from "@mui/icons-material/ReplayOutlined";
import { ChatMessage } from "@/lib/api";

interface MessageBubbleProps {
  message: ChatMessage;
  /** 传入时在消息上显示重试按钮（仅最后一条 assistant 消息由父组件传入） */
  onRetry?: () => void;
}

export default function MessageBubble({ message, onRetry }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const isTool = message.role === "tool";
  const [copied, setCopied] = useState(false);

  const text = isTool ? (message.toolOutput || "") : message.content;

  const handleCopy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 剪贴板不可用时静默忽略 */
    }
  };

  if (isTool) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "flex-start",
          gap: 0.5,
          my: 1,
        }}
      >
        <Box
          sx={{
            px: 2,
            py: 1,
            borderRadius: 2,
            bgcolor: "action.hover",
            color: "text.secondary",
            fontSize: 12,
            maxWidth: "90%",
          }}
        >
          <Typography variant="caption" sx={{ fontWeight: 600 }}>
            🔧 {message.toolName || "工具"}
          </Typography>
          {message.toolOutput && (
            <Typography variant="caption" sx={{ display: "block", mt: 0.5, wordBreak: "break-all" }}>
              {message.toolOutput}
            </Typography>
          )}
        </Box>
        {message.toolOutput && (
          <Tooltip title="复制工具输出">
            <IconButton size="small" onClick={handleCopy} sx={{ mt: 0.5 }}>
              {copied ? <CheckOutlinedIcon sx={{ fontSize: 16 }} /> : <ContentCopyOutlinedIcon sx={{ fontSize: 16 }} />}
            </IconButton>
          </Tooltip>
        )}
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: isUser ? "row-reverse" : "row",
        gap: 1.5,
        mb: 2,
        alignItems: "flex-start",
      }}
    >
      <Avatar
        sx={{
          width: 36,
          height: 36,
          bgcolor: isUser ? "primary.main" : "secondary.main",
          fontSize: 16,
        }}
      >
        {isUser ? "我" : "AI"}
      </Avatar>
      <Box sx={{ maxWidth: "75%", minWidth: 0 }}>
        <Box
          sx={{
            px: 2,
            py: 1.5,
            borderRadius: 3,
            bgcolor: isUser ? "primary.main" : "background.paper",
            color: isUser ? "primary.contrastText" : "text.primary",
            wordBreak: "break-word",
            whiteSpace: "pre-wrap",
          }}
        >
          {message.content}
        </Box>
        <Box
          sx={{
            display: "flex",
            gap: 0.5,
            mt: 0.25,
            flexDirection: isUser ? "row-reverse" : "row",
          }}
        >
          <Tooltip title="复制">
            <IconButton size="small" onClick={handleCopy} disabled={!message.content}>
              {copied ? (
                <CheckOutlinedIcon sx={{ fontSize: 15 }} />
              ) : (
                <ContentCopyOutlinedIcon sx={{ fontSize: 15 }} />
              )}
            </IconButton>
          </Tooltip>
          {!isUser && onRetry && (
            <Tooltip title="重试（time travel 重新生成）">
              <IconButton size="small" onClick={onRetry}>
                <ReplayOutlinedIcon sx={{ fontSize: 15 }} />
              </IconButton>
            </Tooltip>
          )}
        </Box>
      </Box>
    </Box>
  );
}
