"use client";

import { FormEvent, useState } from "react";
import NextLink from "next/link";
import { useRouter } from "next/navigation";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Link as MuiLink,
  Paper,
  TextField,
  Typography,
} from "@mui/material";
import { adminApi, setAdminToken } from "@/lib/adminApi";
import { notifyIdentityChanged } from "@/lib/api";
import ThemeToggleButton from "@/components/ThemeToggleButton";

export default function AdminLoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!username.trim() || !password) {
      setError("请输入用户名和密码");
      return;
    }

    setLoading(true);
    try {
      const token = await adminApi.login(username.trim(), password);
      setAdminToken(token.access_token);
      // 登录会把匿名会话归并到账号，通知对话页重新拉取列表
      notifyIdentityChanged();
      router.replace("/admin");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box
      sx={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        bgcolor: "background.default",
        px: 2,
      }}
    >
      <Box sx={{ position: "absolute", top: 16, right: 16 }}>
        <ThemeToggleButton />
      </Box>

      <Paper elevation={3} sx={{ p: 4, width: "100%", maxWidth: 400 }}>
        <Typography variant="h6" gutterBottom>
          A2A Gateway 管理中心
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
          请使用管理员账号登录
        </Typography>

        <Box component="form" onSubmit={handleSubmit} noValidate>
          <TextField
            label="用户名"
            fullWidth
            size="small"
            margin="normal"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
          <TextField
            label="密码"
            type="password"
            fullWidth
            size="small"
            margin="normal"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />

          {error && (
            <Alert severity="error" sx={{ mt: 2 }}>
              {error}
            </Alert>
          )}

          <Button
            type="submit"
            variant="contained"
            fullWidth
            sx={{ mt: 3 }}
            disabled={loading}
            startIcon={loading ? <CircularProgress size={16} color="inherit" /> : null}
          >
            {loading ? "登录中..." : "登录"}
          </Button>
        </Box>

        <Box sx={{ mt: 2, textAlign: "center" }}>
          <MuiLink
            component={NextLink}
            href="/"
            variant="caption"
            color="text.secondary"
            underline="hover"
          >
            返回对话页
          </MuiLink>
        </Box>
      </Paper>
    </Box>
  );
}
