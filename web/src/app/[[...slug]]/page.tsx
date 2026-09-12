"use client";

import { useState, useEffect } from "react";
import { use } from "react";
import { Box, Typography, CircularProgress, Button } from "@mui/material";
import Link from "next/link";
import ChatPage from "@/components/ChatPage";

interface AgentInfo {
  id: number;
  slug: string;
  name: string;
  description: string;
  status: string;
}

interface PageProps {
  params: Promise<{ slug?: string[] }>;
}

/**
 * 从 catch-all 路由段解析 Agent 与会话：
 *   /                     → 默认 Agent，未指定会话
 *   /c/{threadId}         → 默认 Agent + 会话
 *   /{agentSlug}          → 指定 Agent，未指定会话
 *   /{agentSlug}/c/{threadId} → 指定 Agent + 会话
 */
export function parseRoute(
  segments: string[] | undefined,
): { agentSlug: string; conversationId: string | null } {
  if (!segments || segments.length === 0) return { agentSlug: "/", conversationId: null };
  if (segments[0] === "c") {
    return { agentSlug: "/", conversationId: segments[1] ?? null };
  }
  const agentSlug = segments[0];
  if (segments.length >= 2 && segments[1] === "c") {
    return { agentSlug, conversationId: segments[2] ?? null };
  }
  return { agentSlug, conversationId: null };
}

export default function Page({ params }: PageProps) {
  const { slug } = use(params);
  const { agentSlug, conversationId } = parseRoute(slug);
  const slugStr = agentSlug === "/" ? "/" : agentSlug;

  const [agent, setAgent] = useState<AgentInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    // 公开路由只暴露已发布的 Agent；通过对话端点的 404 来判断是否存在
    // 这里用一个轻量探测：请求一次历史接口（不会产生副作用）
    const url =
      slugStr === "/"
        ? "/api/chat/history?thread_id=probe"
        : `/api/chat/${slugStr}/history?thread_id=probe`;
    fetch(url)
      .then((resp) => {
        if (cancelled) return;
        if (resp.status === 404) {
          setNotFound(true);
          setAgent(null);
        } else {
          // Agent 存在（无论是否有历史）
          setAgent({
            id: 0,
            slug: slugStr,
            name: slugStr === "/" ? "默认 Agent" : `Agent: ${slugStr}`,
            description: "",
            status: "published",
          });
          setNotFound(false);
        }
      })
      .catch(() => {
        if (cancelled) return;
        // 网络错误时也展示对话界面，让用户在对话时看到具体错误
        setAgent({
          id: 0,
          slug: slugStr,
          name: slugStr === "/" ? "默认 Agent" : `Agent: ${slugStr}`,
          description: "",
          status: "published",
        });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slugStr]);

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100vh" }}>
        <CircularProgress />
      </Box>
    );
  }

  if (notFound) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
          gap: 2,
        }}
      >
        <Typography variant="h3" color="text.secondary">
          404
        </Typography>
        <Typography variant="body1" color="text.secondary">
          对话 Agent 不存在或未发布
        </Typography>
        <Button component={Link} href="/" variant="contained">
          返回首页
        </Button>
      </Box>
    );
  }

  return (
    <ChatPage
      slug={agentSlug === "/" ? "" : agentSlug}
      agentName={agent?.name || "Agent"}
      initialConversationId={conversationId}
    />
  );
}
