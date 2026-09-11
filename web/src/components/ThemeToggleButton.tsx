"use client";

import { IconButton, Tooltip } from "@mui/material";
import LightModeOutlinedIcon from "@mui/icons-material/LightModeOutlined";
import DarkModeOutlinedIcon from "@mui/icons-material/DarkModeOutlined";
import { useColorMode } from "./ThemeRegistry";

/** 光暗主题切换按钮：根据当前模式显示对应图标 */
export default function ThemeToggleButton() {
  const { mode, toggle } = useColorMode();
  const isDark = mode === "dark";
  return (
    <Tooltip title={isDark ? "切换到浅色" : "切换到深色"}>
      <IconButton size="small" onClick={toggle} aria-label="切换主题" color="inherit">
        {isDark ? (
          <LightModeOutlinedIcon fontSize="small" />
        ) : (
          <DarkModeOutlinedIcon fontSize="small" />
        )}
      </IconButton>
    </Tooltip>
  );
}
