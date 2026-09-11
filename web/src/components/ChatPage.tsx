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
  Drawer,
  useMediaQuery,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import MenuIcon from "@mui/icons-material/Menu";
import AddCommentOutlinedIcon from "@mui/icons-material/AddCommentOutlined";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";
import ConversationList from "./ConversationList";
import AdminEntry from "./AdminEntry";
import ThemeToggleButton from "./ThemeToggleButton";
import { streamChat, fetchHistory, ChatMessage, SSEEvent } from "@/lib/api";
import {
  Conversation,
  listConversations,
  getActiveId,
  setActiveId,
  migrateLegacy,
  createConversation,
  upsertConversation,
  deleteConversation,
} from "@/lib/conversations";

interface ChatPageProps {
  slug: string;
  agentName: string;
}

const SIDEBAR_WIDTH = 288;

export default function ChatPage({ slug, agentName }: ChatPageProps) {
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up("md"));

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveIdState] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);

  const loadHistory = useCallback(async (conversationId: string, targetSlug: string) => {
    setHistoryLoaded(false);
    setMessages([]);
    try {
      const hist = await fetchHistory(targetSlug, conversationId);
      setMessages(hist);
    } catch {
      setMessages([]);
    } finally {
      setHistoryLoaded(true);
    }
  }, []);

  // 初始化：迁移旧 session，恢复会话列表与当前会话
  useEffect(() => {
    migrateLegacy(slug);
    const list = listConversations(slug);
    setConversations(list);
    setError(null);

    const stored = getActiveId(slug);
    const active = stored && list.some((c) => c.id === stored) ? stored : list[0]?.id ?? null;
    setActiveIdState(active);
    if (active) {
      setActiveId(slug, active);
      void loadHistory(active, slug);
    } else {
      setMessages([]);
      setHistoryLoaded(true);
    }
  }, [slug, loadHistory]);

  // 自动滚动到底部
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const refreshConversations = useCallback(() => {
    setConversations(listConversations(slug));
  }, [slug]);

  const handleSelect = useCallback(
    (id: string) => {
      setDrawerOpen(false);
      if (id === activeId) return;
      setActiveId(slug, id);
      setActiveIdState(id);
      setError(null);
      void loadHistory(id, slug);
    },
    [activeId, slug, loadHistory],
  );

  const handleNew = useCallback(() => {
    setDrawerOpen(false);
    setActiveId(slug, null);
    setActiveIdState(null);
    setMessages([]);
    setError(null);
    setHistoryLoaded(true);
  }, [slug]);

  const handleDelete = useCallback(
    (id: string) => {
      deleteConversation(slug, id);
      const list = listConversations(slug);
      setConversations(list);
      if (activeId === id) {
        const next = list[0]?.id ?? null;
        if (next) {
          setActiveId(slug, next);
          setActiveIdState(next);
          void loadHistory(next, slug);
        } else {
          setActiveId(slug, null);
          setActiveIdState(null);
          setMessages([]);
          setHistoryLoaded(true);
        }
      }
    },
    [activeId, slug, loadHistory],
  );

  const handleSend = useCallback(
    async (text: string) => {
      setError(null);
      setLoading(true);

      // 确定会话：没有当前会话则新建；首条消息用作会话标题
      let conversationId = activeId;
      const isFirstMessage = !messages.some((m) => m.role === "user");
      if (!conversationId) {
        const conv = createConversation(slug, text.slice(0, 24));
        conversationId = conv.id;
        setActiveIdState(conv.id);
      } else if (isFirstMessage) {
        upsertConversation(slug, conversationId, { title: text.slice(0, 24) });
      } else {
        upsertConversation(slug, conversationId);
      }
      refreshConversations();

      // 追加用户消息 + 助手占位（流式填充）
      setMessages((prev) => [
        ...prev,
        { role: "user", content: text },
        { role: "assistant", content: "" },
      ]);

      try {
        await streamChat(slug, text, conversationId, (e: SSEEvent) => {
          if (e.type === "token") {
            setMessages((prev) => {
              const next = [...prev];
              // 找到最后一条 assistant 消息填入 token（工具卡片可能插在中间）
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
              // 插到助手占位之前，保持助手消息在末尾以继续接收 token
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
          } else if (e.type === "done") {
            refreshConversations();
          } else if (e.type === "error") {
            setError(e.detail || "对话处理失败");
          }
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "网络错误，请稍后重试");
      } finally {
        setLoading(false);
        refreshConversations();
      }
    },
    [activeId, messages, slug, refreshConversations],
  );

  const sidebar = (
    <ConversationList
      agentName={agentName}
      conversations={conversations}
      activeId={activeId}
      onSelect={handleSelect}
      onNew={handleNew}
      onDelete={handleDelete}
      footer={<AdminEntry variant="button" showUsername />}
    />
  );

  return (
    <Box sx={{ display: "flex", height: "100vh", bgcolor: "grey.50" }}>
      {/* 桌面端常驻侧边栏 */}
      {isDesktop && (
        <Box
          component="aside"
          sx={{
            width: SIDEBAR_WIDTH,
            flexShrink: 0,
            bgcolor: "background.paper",
            borderRight: 1,
            borderColor: "divider",
          }}
        >
          {sidebar}
        </Box>
      )}

      {/* 移动端抽屉 */}
      {!isDesktop && (
        <Drawer
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          sx={{ "& .MuiDrawer-paper": { width: SIDEBAR_WIDTH, boxSizing: "border-box" } }}
        >
          {sidebar}
        </Drawer>
      )}

      {/* 主对话区 */}
      <Box sx={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {/* 顶部栏 */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            px: 2,
            py: 1.5,
            bgcolor: "background.paper",
            borderBottom: 1,
            borderColor: "divider",
          }}
        >
          {!isDesktop && (
            <IconButton size="small" edge="start" onClick={() => setDrawerOpen(true)}>
              <MenuIcon />
            </IconButton>
          )}
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="h6" sx={{ fontSize: 18 }} noWrap>
              {agentName}
            </Typography>
            {slug && slug !== "/" && (
              <Typography variant="caption" color="text.secondary">
                /{slug}
              </Typography>
            )}
          </Box>
          <Tooltip title="新建对话">
            <IconButton size="small" onClick={handleNew}>
              <AddCommentOutlinedIcon />
            </IconButton>
          </Tooltip>
          <ThemeToggleButton />
          <AdminEntry variant="icon" />
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
    </Box>
  );
}
