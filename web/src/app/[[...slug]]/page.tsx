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

export default function Page({ params }: PageProps) {
  const { slug } = use(params);
  const slugStr = slug && slug.length > 0 ? slug.join("/") : "/";
  const displaySlug = slugStr === "/" ? "" : slugStr;

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

  return <ChatPage slug={displaySlug} agentName={agent?.name || "Agent"} />;
}
