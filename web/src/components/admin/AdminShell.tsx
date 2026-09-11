"use client";

import { useEffect, useState, ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { AppBar, Box, Button, CircularProgress, Container, Toolbar, Typography } from "@mui/material";
import { hasValidAdminToken, setAdminToken } from "@/lib/adminApi";

const LOGIN_PATH = "/admin/login";

/**
 * 管理中心外壳：顶部导航 + 登录态守卫。
 * 未登录访问受保护页面时跳转到 /admin/login；已登录访问登录页时跳回 /admin。
 */
export default function AdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isLoginPage = pathname === LOGIN_PATH;

  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    // 用「未过期」判定：token 过期时直接回到登录页，避免打开后满屏 401
    const hasToken = hasValidAdminToken();
    setAuthed(hasToken);
    setReady(true);
    if (!isLoginPage && !hasToken) {
      router.replace(LOGIN_PATH);
    } else if (isLoginPage && hasToken) {
      router.replace("/admin");
    }
  }, [isLoginPage, pathname, router]);

  const handleLogout = () => {
    setAdminToken(null);
    router.replace(LOGIN_PATH);
  };

  if (isLoginPage) {
    return <>{children}</>;
  }

  if (!ready || !authed) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh" }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "grey.50", display: "flex", flexDirection: "column" }}>
      <AppBar position="static" color="default" elevation={1}>
        <Toolbar sx={{ gap: 1 }}>
          <Typography variant="h6" sx={{ flexGrow: 1, fontSize: 18 }} noWrap>
            A2A Gateway · 管理中心
          </Typography>
          <Button component={Link} href="/admin" size="small" color="primary">
            Agent 管理
          </Button>
          <Button component={Link} href="/" size="small">
            返回对话
          </Button>
          <Button size="small" color="error" onClick={handleLogout}>
            退出
          </Button>
        </Toolbar>
      </AppBar>
      <Container maxWidth="lg" sx={{ py: 3, flex: 1 }}>
        {children}
      </Container>
    </Box>
  );
}
