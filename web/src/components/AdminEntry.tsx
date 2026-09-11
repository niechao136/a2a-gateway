"use client";

import { useSyncExternalStore } from "react";
import Link from "next/link";
import { Button, IconButton, Tooltip, Typography } from "@mui/material";
import LoginIcon from "@mui/icons-material/Login";
import AdminPanelSettingsOutlinedIcon from "@mui/icons-material/AdminPanelSettingsOutlined";
import { readAdminAuth } from "@/lib/adminApi";

const BUTTON_SX = { justifyContent: "flex-start", textTransform: "none" } as const;

const ADMIN_HREF = "/admin";
const LOGIN_HREF = "/admin/login";

interface AdminAuthState {
  /** 是否已读到本地登录态（服务端渲染与首次水合时为 false） */
  ready: boolean;
  loggedIn: boolean;
  username: string | null;
}

/** 服务端/水合阶段的快照：读不到 localStorage，一律视为未登录 */
const SERVER_STATE: AdminAuthState = { ready: false, loggedIn: false, username: null };

/** getSnapshot 必须返回稳定引用，否则 useSyncExternalStore 会无限重渲染 */
let cachedState: AdminAuthState | null = null;

function subscribe(onStoreChange: () => void): () => void {
  // 从管理中心返回本页、或在其它标签页登录/退出后再回到本页时同步状态
  window.addEventListener("focus", onStoreChange);
  window.addEventListener("storage", onStoreChange);
  return () => {
    window.removeEventListener("focus", onStoreChange);
    window.removeEventListener("storage", onStoreChange);
  };
}

function getSnapshot(): AdminAuthState {
  const { loggedIn, username } = readAdminAuth();
  // 值未变化时复用同一对象，保持引用稳定
  if (!cachedState || cachedState.loggedIn !== loggedIn || cachedState.username !== username) {
    cachedState = { ready: true, loggedIn, username };
  }
  return cachedState;
}

function getServerSnapshot(): AdminAuthState {
  return SERVER_STATE;
}

/**
 * 订阅本地登录态（localStorage 中的管理中心 token）。
 *
 * localStorage 属于 React 之外的可变数据源，用 useSyncExternalStore 读取：
 * 服务端渲染返回未登录快照，水合后再切到真实状态，避免水合不一致。
 */
function useAdminAuth(): AdminAuthState {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

interface AdminEntryProps {
  /** icon：顶部栏紧凑图标按钮；button：整宽文字按钮（侧边栏 / 抽屉） */
  variant?: "icon" | "button";
  /** 在按钮下方展示当前登录用户名（侧边栏用） */
  showUsername?: boolean;
}

/**
 * 对话页的「登录 / 管理中心」入口。
 *
 * - 未登录（无 token 或已过期）：「登录」→ /admin/login
 * - 已登录：「管理中心」→ /admin，并展示当前登录用户名
 *
 * 登录态仅用于界面展示，受保护路由与接口仍由 AdminShell 和后端各自校验。
 */
export default function AdminEntry({ variant = "button", showUsername = false }: AdminEntryProps) {
  const { ready, loggedIn, username } = useAdminAuth();

  if (!ready) {
    // 占位：与就绪后尺寸一致，避免布局跳动
    return variant === "icon" ? (
      <IconButton size="small" disabled aria-label="管理中心">
        <AdminPanelSettingsOutlinedIcon fontSize="small" />
      </IconButton>
    ) : (
      <Button fullWidth variant="outlined" disabled startIcon={<LoginIcon />} sx={BUTTON_SX}>
        登录
      </Button>
    );
  }

  const href = loggedIn ? ADMIN_HREF : LOGIN_HREF;
  const label = loggedIn ? "管理中心" : "登录";
  const tip = loggedIn
    ? username
      ? `管理中心（已登录：${username}）`
      : "管理中心"
    : "登录管理中心";

  if (variant === "icon") {
    return (
      <Tooltip title={tip}>
        <IconButton size="small" component={Link} href={href} aria-label={label}>
          {loggedIn ? (
            <AdminPanelSettingsOutlinedIcon fontSize="small" />
          ) : (
            <LoginIcon fontSize="small" />
          )}
        </IconButton>
      </Tooltip>
    );
  }

  return (
    <>
      <Button
        fullWidth
        variant="outlined"
        component={Link}
        href={href}
        startIcon={loggedIn ? <AdminPanelSettingsOutlinedIcon /> : <LoginIcon />}
        sx={BUTTON_SX}
      >
        {label}
      </Button>
      {showUsername && loggedIn && (
        <Typography
          variant="caption"
          color="text.secondary"
          noWrap
          sx={{ display: "block", mt: 0.5, fontSize: 11 }}
        >
          已登录：{username || "管理员"}
        </Typography>
      )}
    </>
  );
}
