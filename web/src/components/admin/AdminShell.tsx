"use client";

import { useEffect, useState, ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import {
  AppBar,
  Box,
  Button,
  CircularProgress,
  Container,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Toolbar,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import MenuIcon from "@mui/icons-material/Menu";
import SmartToyOutlinedIcon from "@mui/icons-material/SmartToyOutlined";
import HubOutlinedIcon from "@mui/icons-material/HubOutlined";
import ExtensionOutlinedIcon from "@mui/icons-material/ExtensionOutlined";
import AutoStoriesOutlinedIcon from "@mui/icons-material/AutoStoriesOutlined";
import AdminPanelSettingsOutlinedIcon from "@mui/icons-material/AdminPanelSettingsOutlined";
import { adminApi, hasValidAdminToken, readAdminAuth, setAdminToken } from "@/lib/adminApi";
import { notifyIdentityChanged } from "@/lib/api";
import { useColorMode } from "@/components/ThemeRegistry";
import ThemeToggleButton from "@/components/ThemeToggleButton";

const LOGIN_PATH = "/admin/login";
const SIDEBAR_WIDTH = 240;

/** 侧边栏导航：Agent / A2A / MCP / Skill 四类资源 */
const NAV_ITEMS = [
  {
    label: "Agent 管理",
    href: "/admin",
    icon: <SmartToyOutlinedIcon fontSize="small" />,
    // /admin 与 /admin/agents/* 都算在「Agent 管理」下
    isActive: (pathname: string) =>
      pathname === "/admin" || pathname.startsWith("/admin/agents"),
  },
  {
    label: "A2A 管理",
    href: "/admin/a2a",
    icon: <HubOutlinedIcon fontSize="small" />,
    isActive: (pathname: string) => pathname.startsWith("/admin/a2a"),
  },
  {
    label: "MCP 管理",
    href: "/admin/mcp",
    icon: <ExtensionOutlinedIcon fontSize="small" />,
    isActive: (pathname: string) => pathname.startsWith("/admin/mcp"),
  },
  {
    label: "Skill 管理",
    href: "/admin/skills",
    icon: <AutoStoriesOutlinedIcon fontSize="small" />,
    isActive: (pathname: string) => pathname.startsWith("/admin/skills"),
  },
];

/**
 * 管理中心外壳：左侧导航 + 登录态守卫。
 * 未登录访问受保护页面时跳转到 /admin/login；已登录访问登录页时跳回 /admin。
 */
export default function AdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up("md"));
  const isLoginPage = pathname === LOGIN_PATH;

  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [username, setUsername] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    // 用「未过期」判定：token 过期时直接回到登录页，避免打开后满屏 401
    const hasToken = hasValidAdminToken();
    setAuthed(hasToken);
    setUsername(hasToken ? readAdminAuth().username : null);
    setReady(true);
    if (!isLoginPage && !hasToken) {
      router.replace(LOGIN_PATH);
    } else if (isLoginPage && hasToken) {
      router.replace("/admin");
    }
  }, [isLoginPage, pathname, router]);

  const handleLogout = async () => {
    // 后端会把身份 cookie 换成全新的匿名身份（会话随账号走，退出后不再可见）
    try {
      await adminApi.logout();
    } catch {
      /* 退出以本地为准，接口失败不阻断 */
    }
    setAdminToken(null);
    notifyIdentityChanged();
    router.replace(LOGIN_PATH);
  };

  if (isLoginPage) {
    return <>{children}</>;
  }

  if (!ready || !authed) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
          "@supports (height: 100dvh)": { height: "100dvh" },
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  const sidebar = (
    <Box
      sx={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        minWidth: 0,
      }}
    >
      <Box sx={{ p: 2, display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}>
        <AdminPanelSettingsOutlinedIcon color="primary" />
        <Typography variant="subtitle1" noWrap sx={{ fontWeight: 600 }}>
          A2A Gateway
        </Typography>
      </Box>
      <Divider />

      <List sx={{ flex: 1, px: 1, py: 1.5 }}>
        {NAV_ITEMS.map((item) => (
          <ListItemButton
            key={item.href}
            component={Link}
            href={item.href}
            selected={item.isActive(pathname)}
            onClick={() => setDrawerOpen(false)}
            sx={{ borderRadius: 1, mb: 0.5 }}
          >
            <ListItemIcon sx={{ minWidth: 34 }}>{item.icon}</ListItemIcon>
            {/* MUI v9 的 ListItemText 已移除 primaryTypographyProps，直接传节点 */}
            <ListItemText
              primary={
                <Typography variant="body2" noWrap>
                  {item.label}
                </Typography>
              }
            />
          </ListItemButton>
        ))}
      </List>

      <Divider />
      <Box sx={{ p: 1.5 }}>
        <Typography variant="caption" color="text.secondary" noWrap sx={{ display: "block" }}>
          已登录：{username || "管理员"}
        </Typography>
      </Box>
    </Box>
  );

  return (
    <Box
      sx={{
        display: "flex",
        height: "100vh",
        "@supports (height: 100dvh)": { height: "100dvh" },
        overflow: "hidden",
        bgcolor: "background.default",
      }}
    >
      {/* 桌面端常驻侧边栏（固定不随内容滚动） */}
      {isDesktop && (
        <Box
          component="aside"
          sx={{
            width: SIDEBAR_WIDTH,
            flexShrink: 0,
            bgcolor: "background.paper",
            borderRight: 1,
            borderColor: "divider",
            height: "100vh",
            "@supports (height: 100dvh)": { height: "100dvh" },
            overflowY: "auto",
            overflowX: "hidden",
            scrollbarGutter: "stable",
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

      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          minWidth: 0,
          height: "100vh",
          "@supports (height: 100dvh)": { height: "100dvh" },
          overflow: "hidden",
        }}
      >
        <AppBar
          position="static"
          color="default"
          elevation={0}
          sx={{ borderBottom: 1, borderColor: "divider", flexShrink: 0 }}
        >
          <Toolbar sx={{ gap: 1 }}>
            {!isDesktop && (
              <IconButton
                size="small"
                edge="start"
                onClick={() => setDrawerOpen(true)}
                aria-label="打开菜单"
              >
                <MenuIcon />
              </IconButton>
            )}
            <Typography variant="h6" sx={{ flexGrow: 1, fontSize: 17 }} noWrap>
              管理中心
            </Typography>
            <ThemeToggleButton />
            <Button component={Link} href="/" size="small">
              返回对话
            </Button>
            <Button size="small" color="error" onClick={handleLogout}>
              退出
            </Button>
          </Toolbar>
        </AppBar>

        <Container
          maxWidth="lg"
          sx={{ py: 3, flex: 1, overflowY: "auto", overflowX: "hidden", scrollbarGutter: "stable" }}
        >
          {children}
        </Container>
      </Box>
    </Box>
  );
}
