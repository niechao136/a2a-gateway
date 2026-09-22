"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
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
import {
  streamChat,
  retryChat,
  fetchHistory,
  IDENTITY_CHANGED_EVENT,
  ChatMessage,
  SSEEvent,
} from "@/lib/api";
import {
  Conversation,
  setActiveId,
  initIdentity,
  migrateLegacyList,
  loadConversations,
  createConversation,
  touchConversation,
  removeConversation,
} from "@/lib/conversations";

interface ChatPageProps {
  slug: string;
  agentName: string;
  /** 路由中的会话 id（/{slug}/c/{id}）；为空表示未指定 */
  initialConversationId?: string | null;
}

const SIDEBAR_WIDTH = 288;

/** 会话对应的页面路径；无会话时回到 Agent 根路由。 */
function chatPath(slug: string, conversationId: string | null): string {
  const base = slug && slug !== "/" ? `/${slug}` : "";
  return conversationId ? `${base}/c/${conversationId}` : base || "/";
}

export default function ChatPage({
  slug,
  agentName,
  initialConversationId = null,
}: ChatPageProps) {
  const theme = useTheme();
  const router = useRouter();
  const isDesktop = useMediaQuery(theme.breakpoints.up("md"));

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveIdState] = useState<string | null>(initialConversationId);
  const [drawerOpen, setDrawerOpen] = useState(false);
  // 下游任务要求补充信息（interrupt）时置 true；用户发送下一条消息时复位
  const [waiting, setWaiting] = useState(false);

  // activeId 的同步引用：供 effect 判断「路由变化是否指向当前已在看的会话」
  const activeIdRef = useRef<string | null>(initialConversationId);
  const mountedRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const updateActiveId = useCallback((id: string | null) => {
    activeIdRef.current = id;
    setActiveIdState(id);
  }, []);

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

  // 初始化：以路由中的会话 id 为准；裸路由（未带 id）= 全新空白对话
  useEffect(() => {
    // 首条消息发送后 router.replace 到 /c/{id}：会话已在当前对话框中，
    // 跳过重载，避免打断正在流式填充的消息
    if (mountedRef.current && initialConversationId && initialConversationId === activeIdRef.current) {
      return;
    }
    mountedRef.current = true;

    let cancelled = false;
    setError(null);
    setHistoryLoaded(false);

    void (async () => {
      // 1) 确保身份 cookie 存在（后端 httpOnly 签发）
      // 2) 把旧版本残留在 localStorage 的会话一次性导入服务端
      // 3) 以服务端列表为准
      await initIdentity();
      await migrateLegacyList(slug);
      let list = await loadConversations(slug);
      if (cancelled) return;
      setConversations(list);

      let active: string | null = null;
      if (initialConversationId) {
        // 路由指定的会话（分享链接 / 旧书签）：列表里没有则补登记一次
        if (!list.some((c) => c.id === initialConversationId)) {
          await touchConversation(slug, initialConversationId);
          list = await loadConversations(slug);
          if (cancelled) return;
          setConversations(list);
        }
        active = initialConversationId;
      }
      updateActiveId(active);
      if (active) {
        setActiveId(slug, active);
        void loadHistory(active, slug);
      } else {
        setMessages([]);
        setHistoryLoaded(true);
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, initialConversationId, loadHistory, updateActiveId]);

  // 自动滚动到底部
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const refreshConversations = useCallback(async () => {
    setConversations(await loadConversations(slug));
  }, [slug]);

  // 登录 / 退出后身份变化：匿名会话会被归并到账号，列表需要重新拉取。
  // 只刷列表不重载消息，避免打断正在流式输出的回复。
  useEffect(() => {
    const onChange = () => {
      void refreshConversations();
    };
    window.addEventListener(IDENTITY_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(IDENTITY_CHANGED_EVENT, onChange);
  }, [refreshConversations]);

  const handleSelect = useCallback(
    (id: string) => {
      setDrawerOpen(false);
      if (id === activeId) return;
      // 路由带会话 id，便于分享 / 前进后退
      router.push(chatPath(slug, id));
    },
    [activeId, slug, router],
  );

  const handleNew = useCallback(() => {
    setDrawerOpen(false);
    // 不再立即生成会话 id：清空对话框，等用户发出首条消息后再落地新路由。
    // 用 history.replaceState 仅改 URL，不触发 Next.js 导航/重渲染
    updateActiveId(null);
    setActiveId(slug, null);
    setMessages([]);
    setError(null);
    setHistoryLoaded(true);
    window.history.replaceState(null, "", chatPath(slug, null));
  }, [slug, updateActiveId]);

  const handleDelete = useCallback(
    async (id: string) => {
      await removeConversation(slug, id);
      const list = await loadConversations(slug);
      setConversations(list);
      if (activeId === id) {
        const next = list[0]?.id ?? null;
        router.push(chatPath(slug, next));
      }
    },
    [activeId, slug, router],
  );

  /** SSE 事件 → 消息状态更新（发送与重试共用）。 */
  const consumeChatEvents = useCallback(
    (e: SSEEvent) => {
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
      } else if (e.type === "interrupt") {
        setWaiting(true);
      } else if (e.type === "error") {
        setError(e.detail || "对话处理失败");
      }
    },
    [],
  );

  const handleSend = useCallback(
    async (text: string) => {
      setError(null);
      setLoading(true);

      // 确定会话：没有当前会话则新建；首条消息用作会话标题
      let conversationId = activeId;
      const isFirstMessage = !messages.some((m) => m.role === "user");
      if (!conversationId) {
        const conv = await createConversation(slug, text.slice(0, 24));
        conversationId = conv.id;
        updateActiveId(conv.id);
        // 会话落地后把 id 无感写进路由：
        // 必须用 history.replaceState 而非 router.replace —— 后者会触发
        // Next.js 导航并重渲染页面，打断正在流式输出的回复
        window.history.replaceState(null, "", chatPath(slug, conv.id));
      } else if (isFirstMessage) {
        await touchConversation(slug, conversationId, text.slice(0, 24));
      } else {
        await touchConversation(slug, conversationId);
      }
      void refreshConversations();

      setWaiting(false);
      // 追加用户消息 + 助手占位（流式填充）
      setMessages((prev) => [
        ...prev,
        { role: "user", content: text },
        { role: "assistant", content: "" },
      ]);

      try {
        await streamChat(slug, text, conversationId, (e: SSEEvent) => {
          if (e.type === "done") refreshConversations();
          consumeChatEvents(e);
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "网络错误，请稍后重试");
      } finally {
        setLoading(false);
        refreshConversations();
      }
    },
    [activeId, messages, slug, refreshConversations, consumeChatEvents, updateActiveId],
  );

  /** 重试最后一次回复：后端 time travel 到最后一次人类消息的检查点重放。 */
  const handleRetry = useCallback(async () => {
    if (!activeId || loading) return;
    setError(null);
    setLoading(true);

    // 移除最后一次回复（含尾部工具卡片），保留到人类消息为止
    setMessages((prev) => {
      const next = [...prev];
      while (next.length && next[next.length - 1].role !== "user") next.pop();
      return next;
    });

    try {
      await retryChat(slug, activeId, (e: SSEEvent) => consumeChatEvents(e));
    } catch (err) {
      setError(err instanceof Error ? err.message : "网络错误，请稍后重试");
    } finally {
      setLoading(false);
    }
  }, [activeId, loading, slug, consumeChatEvents]);

  // 最后一条 assistant 消息的索引（重试按钮只挂在它上面）
  const lastAssistantIdx = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") return i;
    }
    return -1;
  })();

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
    <Box
      sx={{
        display: "flex",
        height: "100vh",
        "@supports (height: 100dvh)": { height: "100dvh" },
        bgcolor: "background.default",
      }}
    >
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
            scrollbarGutter: "stable",
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
            messages.map((msg, idx) => (
              <MessageBubble
                key={idx}
                message={msg}
                onRetry={
                  idx === lastAssistantIdx && !loading ? handleRetry : undefined
                }
              />
            ))
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

        {/* 等待补充提示（下游任务追问用户） */}
        {waiting && (
          <Alert severity="info" sx={{ mx: 2, mb: 1 }}>
            等待你补充信息，请直接回复
          </Alert>
        )}

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
