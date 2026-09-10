"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import {
  Box,
  Typography,
  CircularProgress,
  Alert,
  Button,
  IconButton,
  Tooltip,
} from "@mui/material";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";
import { streamChat, fetchHistory, ChatMessage, SSEEvent } from "@/lib/api";
import { getThreadId, setThreadId, clearThreadId } from "@/lib/session";

interface ChatPageProps {
  slug: string;
  agentName: string;
}

export default function ChatPage({ slug, agentName }: ChatPageProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const threadIdRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // 初始化：从 localStorage 恢复 thread_id，并拉取历史
  useEffect(() => {
    const tid = getThreadId(slug);
    threadIdRef.current = tid;
    if (tid) {
      fetchHistory(slug, tid)
        .then((hist) => {
          if (hist.length > 0) setMessages(hist);
        })
        .catch(() => {
          /* 历史获取失败不阻塞对话 */
        })
        .finally(() => setHistoryLoaded(true));
    } else {
      setHistoryLoaded(true);
    }
  }, [slug]);

  // 自动滚动到底部
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const handleSend = useCallback(
    async (text: string) => {
      setError(null);
      setLoading(true);

      // 加入用户消息
      setMessages((prev) => [...prev, { role: "user", content: text }]);

      // 占位助手消息（流式填充）
      const assistantMsg: ChatMessage = { role: "assistant", content: "" };
      setMessages((prev) => [...prev, assistantMsg]);

      try {
        await streamChat(
          slug,
          text,
          threadIdRef.current,
          (e: SSEEvent) => {
            if (e.type === "token") {
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last && last.role === "assistant") {
                  next[next.length - 1] = { ...last, content: last.content + e.content };
                }
                return next;
              });
            } else if (e.type === "tool_start") {
              setMessages((prev) => [
                ...prev,
                { role: "tool", content: "", toolName: e.name },
              ]);
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
            } else if (e.type === "done") {
              if (e.thread_id) {
                threadIdRef.current = e.thread_id;
                setThreadId(slug, e.thread_id);
              }
            } else if (e.type === "error") {
              setError(e.detail || "对话处理失败");
            }
          },
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : "网络错误，请稍后重试");
      } finally {
        setLoading(false);
      }
    },
    [slug],
  );

  const handleClear = () => {
    clearThreadId(slug);
    threadIdRef.current = null;
    setMessages([]);
    setError(null);
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        bgcolor: "grey.50",
      }}
    >
      {/* 顶部栏 */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 2,
          py: 1.5,
          bgcolor: "background.paper",
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Box>
          <Typography variant="h6" sx={{ fontSize: 18 }}>
            {agentName}
          </Typography>
          {slug && slug !== "/" && (
            <Typography variant="caption" color="text.secondary">
              /{slug}
            </Typography>
          )}
        </Box>
        <Tooltip title="清空当前会话">
          <IconButton onClick={handleClear} size="small" disabled={messages.length === 0}>
            <DeleteOutlinedIcon />
          </IconButton>
        </Tooltip>
      </Box>

      {/* 消息区 */}
      <Box
        ref={scrollRef}
        sx={{
          flex: 1,
          overflowY: "auto",
          px: { xs: 1, sm: 3 },
          py: 2,
        }}
      >
        {!historyLoaded ? (
          <Box sx={{ display: "flex", justifyContent: "center", mt: 4 }}>
            <CircularProgress size={24} />
          </Box>
        ) : messages.length === 0 ? (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: "text.secondary",
            }}
          >
            <Typography variant="body1" gutterBottom>
              开始与 {agentName} 对话吧
            </Typography>
            <Typography variant="body2">输入消息后按 Enter 发送</Typography>
          </Box>
        ) : (
          messages.map((msg, idx) => <MessageBubble key={idx} message={msg} />)
        )}

        {loading && (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, ml: 6, mb: 2 }}>
            <CircularProgress size={16} />
            <Typography variant="caption" color="text.secondary">
              正在思考...
            </Typography>
          </Box>
        )}
      </Box>

      {/* 错误提示 */}
      {error && (
        <Alert
          severity="error"
          action={
            <Button color="inherit" size="small" onClick={() => setError(null)}>
              关闭
            </Button>
          }
          sx={{ mx: 2, mb: 1 }}
        >
          {error}
        </Alert>
      )}

      {/* 输入区 */}
      <ChatInput onSend={handleSend} disabled={loading} />
    </Box>
  );
}
