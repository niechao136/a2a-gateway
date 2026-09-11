import type { ReactNode } from "react";
import AdminShell from "@/components/admin/AdminShell";

// 注意：/admin 下的路由优先于根级可选 catch-all [[...slug]]，不会落到公开对话页。
export default function AdminLayout({ children }: { children: ReactNode }) {
  return <AdminShell>{children}</AdminShell>;
}
