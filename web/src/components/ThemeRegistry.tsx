"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  ReactNode,
} from "react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";

export type ColorMode = "light" | "dark";

interface ColorModeContextValue {
  mode: ColorMode;
  toggle: () => void;
  setMode: (mode: ColorMode) => void;
}

const ColorModeContext = createContext<ColorModeContextValue>({
  mode: "light",
  toggle: () => {},
  setMode: () => {},
});

export function useColorMode(): ColorModeContextValue {
  return useContext(ColorModeContext);
}

const STORAGE_KEY = "a2a-color-mode";

function getInitialMode(): ColorMode {
  if (typeof window === "undefined") return "light";
  const saved = window.localStorage.getItem(STORAGE_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export default function ThemeRegistry({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ColorMode>("light");

  // 挂载后再读取用户偏好或系统设置，避免 SSR 首屏水合不一致
  useEffect(() => {
    setMode(getInitialMode());
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute("data-color-scheme", mode);
    try {
      window.localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // 忽略隐私模式下的写入异常
    }
  }, [mode]);

  const theme = useMemo(
    () =>
      createTheme({
        palette: {
          mode,
          primary: { main: "#1976d2" },
          secondary: { main: "#9c27b0" },
          background:
            mode === "dark"
              ? { default: "#0f1115", paper: "#171a21" }
              : { default: "#f5f6f8", paper: "#ffffff" },
        },
        typography: {
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif",
        },
        components: {
          MuiIconButton: {
            styleOverrides: {
              // 触屏指针设备上小尺寸图标按钮达到 44px 触控目标（桌面样式零变化）
              sizeSmall: {
                "@media (hover: none) and (pointer: coarse)": {
                  padding: 12,
                },
              },
            },
          },
        },
      }),
    [mode]
  );

  const value = useMemo<ColorModeContextValue>(
    () => ({
      mode,
      toggle: () => setMode((m) => (m === "light" ? "dark" : "light")),
      setMode,
    }),
    [mode]
  );

  return (
    <ColorModeContext.Provider value={value}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </ColorModeContext.Provider>
  );
}
