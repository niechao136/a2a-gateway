"use client";

import { useMediaQuery } from "@mui/material";
import { useTheme } from "@mui/material/styles";

/** 手机档：<600px。移动端专属布局（卡片、全屏 Dialog、Enter 换行策略）以此为开关。 */
export function useIsMobile(): boolean {
  const theme = useTheme();
  return useMediaQuery(theme.breakpoints.down("sm"));
}

/** 平板竖屏档：600-899px。用于精简表格（隐藏次要列）等中间态布局。 */
export function useIsTablet(): boolean {
  const theme = useTheme();
  return useMediaQuery(theme.breakpoints.between("sm", "md"));
}
