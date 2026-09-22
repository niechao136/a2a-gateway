"use client";

import { ReactNode } from "react";
import { Box, DialogTitle, IconButton } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { useIsMobile } from "@/lib/breakpoints";

interface DialogTitleBarProps {
  title: ReactNode;
  onClose: () => void;
}

/**
 * 弹窗标题栏：移动端（fullScreen 档）MUI 无默认关闭交互，补一个关闭按钮；
 * 桌面不渲染按钮，维持现状（ESC / 点击遮罩关闭）。
 */
export default function DialogTitleBar({ title, onClose }: DialogTitleBarProps) {
  const isMobile = useIsMobile();
  return (
    <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1, pr: 1.5 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>{title}</Box>
      {isMobile && (
        <IconButton size="small" edge="end" onClick={onClose} aria-label="关闭">
          <CloseIcon fontSize="small" />
        </IconButton>
      )}
    </DialogTitle>
  );
}
