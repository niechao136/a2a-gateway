"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import MessageBubble from "@/components/MessageBubble";
import ChatInput from "@/components/ChatInput";
import { ChatMessage, SSEEvent, streamChatUrl } from "@/lib/api";
import { adminAuthHeaders, adminTestChatUrl } from "@/lib/adminApi";
import { generateId } from "@/lib/conversations";

interface TestChatDialogProps {
  open: boolean;
  agentId: number | null;
  agentName: string;
  onClose: () => void;
}

/**
 * 管理中心内的即时测试对话（draft 状态也可测）。
 * 复用公开对话的渲染组件与 SSE 解析逻辑。
 */
export default function TestChatDialog({
  open,
  agentId,
  agentName,
  onClose,
}: TestChatDialogProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const threadIdRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // 每次打开/切换 Agent 都重置会话
  useEffect(() => {
    if (open) {
      setMessages([]);
      setError(null);
      setLoading(false);
      threadIdRef.current = null;
    }
  }, [open, agentId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const handleSend = useCallback(
    async (text: string) => {
      if (!agentId) return;
      setError(null);
      setLoading(true);
      if (!threadIdRef.current) threadIdRef.current = generateId();

      setMessages((prev) => [
        ...prev,
        { role: "user", content: text },
        { role: "assistant", content: "" },
      ]);

      try {
        await streamChatUrl(
          adminTestChatUrl(agentId),
          text,
          threadIdRef.current,
          (e: SSEEvent) => {
            if (e.type === "token") {
              setMessages((prev) => {
                const next = [...prev];
                for (let i = next.length - 1; i >= 0; i--) {
                  if (next[i].role === "assistant") {
                    next[i] = { ...next[i], content: next[i].content + e.content };
                    break;
                  }
                }
                return next;
              });
            } else if (e.type === "tool_start") {
              setMessages((prev) => {
                const next = [...prev];
                const toolMsg: ChatMessage = { role: "tool", content: "", toolName: e.name };
                const last = next[next.length - 1];
                if (last && last.role === "assistant") next.splice(next.length - 1, 0, toolMsg);
                else next.push(toolMsg);
                return next;
              });
            } else if (e.type === "tool_end") {
              setMessages((prev) => {
                const next = [...prev];
                for (let i = next.length - 1; i >= 0; i--) {
                  if (next[i].role === "tool" && !next[i].toolOutput) {
                    next[i] = { ...next[i], toolOutput: e.output };
                    break;
                  }
                }
                return next;
              });
            } else if (e.type === "error") {
              setError(e.detail || "对话处理失败");
            }
          },
          adminAuthHeaders(),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : "网络错误，请稍后重试");
      } finally {
        setLoading(false);
      }
    },
    [agentId],
  );

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Typography variant="h6" sx={{ flex: 1, fontSize: 18 }} noWrap>
          测试对话 · {agentName}
        </Typography>
        <IconButton size="small" onClick={onClose}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent
        dividers
        sx={{ p: 0, display: "flex", flexDirection: "column", height: "65vh", bgcolor: "background.default" }}
      >
        <Box ref={scrollRef} sx={{ flex: 1, overflowY: "auto", px: 2, py: 2 }}>
          {messages.length === 0 ? (
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                height: "100%",
                color: "text.secondary",
              }}
            >
              <Typography variant="body2">发送消息即可验证该 Agent 的配置</Typography>
            </Box>
          ) : (
            messages.map((msg, idx) => <MessageBubble key={idx} message={msg} />)
          )}
          {loading && (
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, ml: 6 }}>
              <CircularProgress size={16} />
              <Typography variant="caption" color="text.secondary">
                正在思考...
              </Typography>
            </Box>
          )}
        </Box>

        {error && (
          <Alert severity="error" sx={{ mx: 2, mb: 1 }}>
            {error}
          </Alert>
        )}

        <ChatInput onSend={handleSend} disabled={loading} />
      </DialogContent>
    </Dialog>
  );
}
