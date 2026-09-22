import type { Metadata, Viewport } from "next";
import "./globals.css";
import ThemeRegistry from "@/components/ThemeRegistry";

export const metadata: Metadata = {
  title: "A2A Gateway",
  description: "基于 LangGraph 的多 Agent 平台",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  interactiveWidget: "resizes-content", // 键盘弹起收缩视口而非遮挡输入框
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full flex flex-col m-0 p-0">
        <ThemeRegistry>{children}</ThemeRegistry>
      </body>
    </html>
  );
}
