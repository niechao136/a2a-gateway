"use client";

import { Box, Avatar, Typography } from "@mui/material";
import { ChatMessage } from "@/lib/api";

interface MessageBubbleProps {
  message: ChatMessage;
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const isTool = message.role === "tool";

  if (isTool) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          my: 1,
        }}
      >
        <Box
          sx={{
            px: 2,
            py: 1,
            borderRadius: 2,
            bgcolor: "grey.200",
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
      <Box
        sx={{
          maxWidth: "75%",
          px: 2,
          py: 1.5,
          borderRadius: 3,
          bgcolor: isUser ? "primary.main" : "grey.100",
          color: isUser ? "primary.contrastText" : "text.primary",
          wordBreak: "break-word",
          whiteSpace: "pre-wrap",
        }}
      >
        {message.content}
      </Box>
    </Box>
  );
}
