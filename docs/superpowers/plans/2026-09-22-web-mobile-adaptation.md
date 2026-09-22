# Web 移动端适配 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 `web/` 前端在手机（<600px）与平板竖屏（600-899px）获得完整体验，桌面（≥900px）零变化。

**架构：** 在现有 MUI 响应式骨架上补齐三档形态——新增断点 hook（`useIsMobile`/`useIsTablet`），admin 列表页窄屏切卡片、平板隐藏次要列，9 处 Dialog 移动端全屏化（共享 `DialogTitleBar`），全局修复 dvh/viewport/触屏 hover。触控目标 ≥44px 通过主题级 `MuiIconButton` styleOverride 在触屏指针设备上一处生效（对规格 §5.4 的实现优化：等价、DRY、且保证桌面 DOM 样式零变化）。

**技术栈：** Next.js 16 + React 19 + MUI 9 + Tailwind 4 + vitest。不新增任何依赖。

**规格：** `docs/superpowers/specs/2026-09-22-web-mobile-adaptation-design.md`（计划与规格一起读；本计划的论证依据全部来自规格）。

## 全局约束

- 不引入新依赖（含 devDependencies；规格 §9 / §10——项目无 React 测试工具，不为薄封装补组件测试）。
- 不修改主题断点定义、不改后端、不动 `next.config.ts`。
- ≥900px 桌面各页视觉与交互**零变化**（回归红线，规格 §10.4）。
- 断点：`xs`<600 / `sm` 600-899 / `md`≥900，沿用 MUI 默认。
- `100dvh` 必须用 `"@supports (height: 100dvh)"` 条件覆盖写法，禁止同一 JS 对象双写 `height`（ESLint `no-dupe-keys`）。
- 每个任务完成即 commit；所有命令在 `web/` 目录下执行（`cd d:\web\a2a-gateway\web`）。
- 验证命令三件套：`npm run lint`、`npx vitest run`（现有 5 个测试须保持全绿）、`npm run build`（预期 1-2 分钟，勿设过短超时）。

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `web/src/lib/breakpoints.ts` | 创建 | `useIsMobile()`（<600）/ `useIsTablet()`（600-899）两个共享 hook |
| `web/src/app/layout.tsx` | 修改 | 显式 `viewport` 导出（键盘行为） |
| `web/src/components/ThemeRegistry.tsx` | 修改 | 主题级触屏 `IconButton` 触控目标 override |
| `web/src/components/ConversationList.tsx` | 修改 | 触屏 hover 修复（删除按钮常显） |
| `web/src/app/[[...slug]]/page.tsx` | 修改 | loading / 404 两处 100dvh |
| `web/src/app/admin/login/page.tsx` | 修改 | 100dvh |
| `web/src/components/admin/AdminShell.tsx` | 修改 | 外层/侧栏/内容列 3 处 100dvh |
| `web/src/components/ChatPage.tsx` | 修改 | 100dvh + 侧栏断点放宽到 sm + 平板侧栏 240 |
| `web/src/components/ChatInput.tsx` | 修改 | 移动端 Enter=换行、发送仅按钮 + placeholder 文案 |
| `web/src/components/MessageBubble.tsx` | 修改 | 气泡 maxWidth 移动端 85% |
| `web/src/components/admin/DialogTitleBar.tsx` | 创建 | 移动端带关闭按钮的 DialogTitle（桌面不渲染按钮） |
| `web/src/components/admin/A2AEndpointDialog.tsx` | 修改 | fullScreen + DialogTitleBar |
| `web/src/components/admin/McpServerDialog.tsx` | 修改 | 同上 |
| `web/src/components/admin/SkillEditDialog.tsx` | 修改 | 同上 |
| `web/src/components/admin/SkillImportDialog.tsx` | 修改 | 同上 |
| `web/src/components/admin/SkillDetailDialog.tsx` | 修改 | 同上 |
| `web/src/components/admin/ManualA2ABinding.tsx` | 修改 | 内部 `ManualA2ADialog` 同上 |
| `web/src/components/admin/ManualMcpBinding.tsx` | 修改 | 内部 `ManualMcpDialog` 同上 |
| `web/src/components/admin/TestChatDialog.tsx` | 修改 | fullScreen + 移动端高度自适应（自带关闭按钮，不换标题栏） |
| `web/src/app/admin/page.tsx` | 修改 | Agent 卡片 + 平板隐藏列 + 页头换行 |
| `web/src/app/admin/a2a/page.tsx` | 修改 | A2A 卡片 + 隐藏列 + 页头换行 |
| `web/src/app/admin/mcp/page.tsx` | 修改 | MCP 卡片 + 隐藏列 + 页头换行 + 内联工具 Dialog |
| `web/src/app/admin/skills/page.tsx` | 修改 | Skill 卡片 + 隐藏列 + 页头换行 |
| `web/src/components/admin/AgentApiKeys.tsx` | 修改 | Key 卡片 + 新建表单窄屏纵排 |

---

### 任务 1：断点 hook（`lib/breakpoints.ts`）

**文件：**
- 创建：`web/src/lib/breakpoints.ts`

- [ ] **步骤 1：创建 hook 文件**

```ts
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
```

- [ ] **步骤 2：验证**

运行：`npm run lint && npx vitest run`
预期：lint 无新增错误；vitest 5 个测试文件全部 PASS。

- [ ] **步骤 3：Commit**

```bash
git add src/lib/breakpoints.ts
git commit -m "feat(web): 新增移动端/平板断点 hook"
```

---

### 任务 2：viewport 导出 + 触控目标 override + 全部 100dvh

**文件：**
- 修改：`web/src/app/layout.tsx`
- 修改：`web/src/components/ThemeRegistry.tsx:58-76`（createTheme）
- 修改：`web/src/components/ChatPage.tsx:338`
- 修改：`web/src/app/[[...slug]]/page.tsx:99,113`
- 修改：`web/src/app/admin/login/page.tsx:57`
- 修改：`web/src/components/admin/AdminShell.tsx:172,184,204`

- [ ] **步骤 1：layout.tsx 增加 viewport 导出**

在 `metadata` 导出之后添加（`Viewport` 类型从 `next` 导入）：

```ts
import type { Metadata, Viewport } from "next";

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  interactiveWidget: "resizes-content", // 键盘弹起收缩视口而非遮挡输入框
};
```

- [ ] **步骤 2：ThemeRegistry 触控目标 override**

`createTheme` 配置对象中，`typography` 字段之后追加 `components`：

```ts
components: {
  MuiIconButton: {
    styleOverrides: {
      // 触屏指针设备上小尺寸图标按钮达到 44px 触控目标（桌面不动）
      sizeSmall: {
        "@media (hover: none) and (pointer: coarse)": {
          padding: 12,
        },
      },
    },
  },
},
```

- [ ] **步骤 3：ChatPage.tsx 外层容器 dvh**

`ChatPage.tsx` 第 338 行：

```tsx
<Box sx={{ display: "flex", height: "100vh", bgcolor: "background.default" }}>
```

改为：

```tsx
<Box
  sx={{
    display: "flex",
    height: "100vh",
    "@supports (height: 100dvh)": { height: "100dvh" },
    bgcolor: "background.default",
  }}
>
```

- [ ] **步骤 4：[[...slug]]/page.tsx 两处 dvh**

第 99 行（loading 分支）：

```tsx
<Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100vh" }}>
```

改为：

```tsx
<Box
  sx={{
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
    height: "100vh",
    "@supports (height: 100dvh)": { height: "100dvh" },
  }}
>
```

第 113 行（404 分支）同样处理：`height: "100vh"` 后追加 `"@supports (height: 100dvh)": { height: "100dvh" }`。

- [ ] **步骤 5：login 页 dvh**

`admin/login/page.tsx` 第 57 行 `minHeight: "100vh"`：

```tsx
minHeight: "100vh",
"@supports (min-height: 100dvh)": { minHeight: "100dvh" },
```

- [ ] **步骤 6：AdminShell 三处 dvh**

第 172 行外层、第 184 行侧栏、第 204 行内容列的 `height: "100vh"`，均在原属性后追加：

```ts
"@supports (height: 100dvh)": { height: "100dvh" },
```

（注意保持各自的其余属性不变；第 172 行外层含 `overflow: "hidden"`、第 183 行侧栏含 `overflowY/overflowX/scrollbarGutter`、第 204 行含 `overflow: "hidden"`。）

- [ ] **步骤 7：验证**

运行：`npm run lint && npx vitest run`
预期：无新增错误，测试全绿。

- [ ] **步骤 8：Commit**

```bash
git add src/app/layout.tsx src/components/ThemeRegistry.tsx src/components/ChatPage.tsx "src/app/[[...slug]]/page.tsx" src/app/admin/login/page.tsx src/components/admin/AdminShell.tsx
git commit -m "fix(web): viewport 键盘行为、触屏触控目标与 100dvh 视口高度"
```

---

### 任务 3：触屏 hover 修复（`ConversationList.tsx`）

**文件：**
- 修改：`web/src/components/ConversationList.tsx:91-97`

- [ ] **步骤 1：删除按钮显隐改为 hover 能力媒体查询**

`ListItemButton` 的 sx 中：

```tsx
sx={{
  borderRadius: 1,
  mb: 0.5,
  pr: 0.5,
  "& .conv-delete": { opacity: 0 },
  "&:hover .conv-delete": { opacity: 1 },
}}
```

改为：

```tsx
sx={{
  borderRadius: 1,
  mb: 0.5,
  pr: 0.5,
  // 触屏无 hover：删除按钮常显；有鼠标的设备维持 hover 显隐
  "& .conv-delete": { opacity: 1 },
  "@media (hover: hover)": {
    "& .conv-delete": { opacity: 0 },
    "&:hover .conv-delete": { opacity: 1 },
  },
}}
```

- [ ] **步骤 2：验证**

运行：`npm run lint`
预期：无新增错误。

- [ ] **步骤 3：Commit**

```bash
git add src/components/ConversationList.tsx
git commit -m "fix(web): 会话删除按钮在触屏设备常显"
```

---

### 任务 4：聊天页侧栏断点与平板宽度（`ChatPage.tsx`）

**文件：**
- 修改：`web/src/components/ChatPage.tsx:65,340-353`

- [ ] **步骤 1：常驻侧栏条件放宽到 sm**

第 65 行：

```tsx
const isDesktop = useMediaQuery(theme.breakpoints.up("md"));
```

改为（≥600px 双栏；<600px 走抽屉）：

```tsx
const isDesktop = useMediaQuery(theme.breakpoints.up("sm"));
```

- [ ] **步骤 2：侧栏宽度分档**

桌面侧栏 Box（第 341-350 行）：

```tsx
<Box
  component="aside"
  sx={{
    width: SIDEBAR_WIDTH,
    flexShrink: 0,
    bgcolor: "background.paper",
    borderRight: 1,
    borderColor: "divider",
  }}
>
```

`width` 改为分档（`SIDEBAR_WIDTH` 常量仍被 Drawer 使用，保留不动）：

```tsx
<Box
  component="aside"
  sx={{
    width: { sm: 240, md: 288 },
    flexShrink: 0,
    bgcolor: "background.paper",
    borderRight: 1,
    borderColor: "divider",
  }}
>
```

- [ ] **步骤 3：验证**

运行：`npm run lint && npm run build`
预期：通过；≥900px 侧栏仍为 288px（`md: 288` 与原常量一致，回归零变化）。

- [ ] **步骤 4：Commit**

```bash
git add src/components/ChatPage.tsx
git commit -m "feat(web): 聊天页平板竖屏双栏布局"
```

---

### 任务 5：移动端 Enter 策略（`ChatInput.tsx`）

**文件：**
- 修改：`web/src/components/ChatInput.tsx:1-41,125-140`

- [ ] **步骤 1：引入 hook**

第 4 行 MUI 导入保持不变，新增：

```tsx
import { useIsMobile } from "@/lib/breakpoints";
```

组件内（`const [value, setValue] = useState("");` 之前）：

```tsx
const isMobile = useIsMobile();
```

- [ ] **步骤 2：Enter 仅在桌面发送**

```tsx
const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
};
```

改为：

```tsx
const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
  // 移动端软键盘"换行"只换行，发送仅通过发送按钮；桌面保持 Enter 发送
  if (isMobile) return;
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
};
```

- [ ] **步骤 3：placeholder 分端文案**

```tsx
placeholder={
  recording
    ? "正在聆听，请说话...（再次点击麦克风结束）"
    : isMobile
      ? "输入消息，点击右侧按钮发送"
      : "输入消息...（Enter 发送，Shift+Enter 换行）"
}
```

- [ ] **步骤 4：验证**

运行：`npm run lint && npm run build`
预期：通过。

- [ ] **步骤 5：Commit**

```bash
git add src/components/ChatInput.tsx
git commit -m "feat(web): 移动端聊天输入改为换行键不发送"
```

---

### 任务 6：消息气泡宽度（`MessageBubble.tsx`）

**文件：**
- 修改：`web/src/components/MessageBubble.tsx:125`

- [ ] **步骤 1：maxWidth 分档**

气泡容器：

```tsx
<Box sx={{ maxWidth: "75%", minWidth: 0 }}>
```

改为：

```tsx
<Box sx={{ maxWidth: { xs: "85%", sm: "75%" }, minWidth: 0 }}>
```

（工具卡片分支的 `maxWidth: "90%"` 不动。）

- [ ] **步骤 2：验证**

运行：`npm run lint`
预期：无新增错误。

- [ ] **步骤 3：Commit**

```bash
git add src/components/MessageBubble.tsx
git commit -m "feat(web): 消息气泡移动端加宽至 85%"
```

---

### 任务 7：DialogTitleBar 组件 + 9 处 Dialog 移动端全屏化

**文件：**
- 创建：`web/src/components/admin/DialogTitleBar.tsx`
- 修改：`web/src/components/admin/A2AEndpointDialog.tsx:117-118`
- 修改：`web/src/components/admin/McpServerDialog.tsx:159-160`
- 修改：`web/src/components/admin/SkillEditDialog.tsx:143-144`
- 修改：`web/src/components/admin/SkillImportDialog.tsx:193-194`
- 修改：`web/src/components/admin/SkillDetailDialog.tsx:30-37`
- 修改：`web/src/components/admin/ManualA2ABinding.tsx:184-185`
- 修改：`web/src/components/admin/ManualMcpBinding.tsx:219-220`
- 修改：`web/src/components/admin/TestChatDialog.tsx:124,133-136`
- 修改：`web/src/app/admin/mcp/page.tsx:299-305`（内联"可用工具" Dialog）

- [ ] **步骤 1：创建 DialogTitleBar**

```tsx
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
```

- [ ] **步骤 2：接入 7 个"简单标题"Dialog（统一模式，逐文件替换）**

每个文件：新增 `import DialogTitleBar from "./DialogTitleBar";` 与 `import { useIsMobile } from "@/lib/breakpoints";`，组件体内加 `const isMobile = useIsMobile();`，然后按下表替换 `<Dialog ...>` 行与 `<DialogTitle>...</DialogTitle>` 行：

| 文件 | Dialog 行改为 | 原 DialogTitle 行改为 |
|---|---|---|
| `A2AEndpointDialog.tsx` | `<Dialog open onClose={onClose} maxWidth="sm" fullWidth fullScreen={isMobile}>` | `<DialogTitleBar title={isEdit ? "编辑 A2A 目标" : "新建 A2A 目标"} onClose={onClose} />` |
| `McpServerDialog.tsx` | `<Dialog open onClose={onClose} maxWidth="sm" fullWidth fullScreen={isMobile}>` | `<DialogTitleBar title={isEdit ? "编辑 MCP 服务" : "新建 MCP 服务"} onClose={onClose} />` |
| `SkillEditDialog.tsx` | `<Dialog open={open} onClose={onClose} fullWidth maxWidth="md" fullScreen={isMobile}>` | `<DialogTitleBar title={`编辑 Skill：${skill.name}`} onClose={onClose} />` |
| `SkillImportDialog.tsx` | `<Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" fullScreen={isMobile}>` | `<DialogTitleBar title="导入 Skill" onClose={onClose} />` |
| `ManualA2ABinding.tsx`（ManualA2ADialog） | `<Dialog open onClose={onClose} maxWidth="sm" fullWidth fullScreen={isMobile}>` | `<DialogTitleBar title={initial ? "编辑手动绑定的 A2A 目标" : "手动绑定 A2A 目标"} onClose={onClose} />` |
| `ManualMcpBinding.tsx`（ManualMcpDialog） | `<Dialog open onClose={onClose} maxWidth="sm" fullWidth fullScreen={isMobile}>` | `<DialogTitleBar title={initial ? "编辑手动绑定的 MCP 服务" : "手动绑定 MCP 服务"} onClose={onClose} />` |
| `mcp/page.tsx`（内联 Dialog） | `<Dialog open={!!toolsTarget} onClose={() => setToolsTarget(null)} maxWidth="sm" fullWidth fullScreen={isMobile}>` | `<DialogTitleBar title={`可用工具 · ${toolsTarget?.name ?? ""}`} onClose={() => setToolsTarget(null)} />` |

注意：`mcp/page.tsx` 的 import 路径为 `@/components/admin/DialogTitleBar`（页面在 app 目录）；`useIsMobile` 也在该页引入。原文件中被替换掉的 `DialogTitle` 导入若不再使用，从 MUI 导入列表中移除（lint 会提示）。

- [ ] **步骤 3：SkillDetailDialog 接入（标题含元信息节点）**

```tsx
<Dialog open={open} onClose={onClose} fullWidth maxWidth="md" fullScreen={isMobile}>
  <DialogTitleBar
    title={
      <>
        {skill.name}
        <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
          {skill.load_mode === "always" ? "常驻" : "按需"} · {formatBytes(skill.size_bytes)} ·{" "}
          {skill.file_count} 附件 · 来源 {skill.source}
        </Typography>
      </>
    }
    onClose={onClose}
  />
```

- [ ] **步骤 4：TestChatDialog 接入（保留自带关闭按钮，改高度策略）**

```tsx
const isMobile = useIsMobile();
```

```tsx
<Dialog open={open} onClose={onClose} maxWidth="md" fullWidth fullScreen={isMobile}>
```

DialogContent（原 `height: "65vh"`）改为移动端自适应撑满：

```tsx
<DialogContent
  dividers
  sx={{
    p: 0,
    display: "flex",
    flexDirection: "column",
    bgcolor: "background.default",
    ...(isMobile ? { minHeight: 0, flex: 1 } : { height: "65vh" }),
  }}
>
```

- [ ] **步骤 5：验证**

运行：`npm run lint && npm run build && npx vitest run`
预期：全部通过（重点看 SkillEditDialog/SkillImportDialog 是否有残留未用导入）。

- [ ] **步骤 6：Commit**

```bash
git add src/components/admin/DialogTitleBar.tsx src/components/admin/A2AEndpointDialog.tsx src/components/admin/McpServerDialog.tsx src/components/admin/SkillEditDialog.tsx src/components/admin/SkillImportDialog.tsx src/components/admin/SkillDetailDialog.tsx src/components/admin/ManualA2ABinding.tsx src/components/admin/ManualMcpBinding.tsx src/components/admin/TestChatDialog.tsx src/app/admin/mcp/page.tsx
git commit -m "feat(web): 弹窗移动端全屏化并补充关闭按钮"
```

---

### 任务 8：Agent 管理页卡片化（`admin/page.tsx`）

**文件：**
- 修改：`web/src/app/admin/page.tsx`

- [ ] **步骤 1：引入断点 hook 与 Paper**

MUI 导入列表加入 `Paper`（`Table` 系导入保留），并新增：

```tsx
import { useIsMobile, useIsTablet } from "@/lib/breakpoints";
```

`AdminAgentsPage` 组件体内：

```tsx
const isMobile = useIsMobile();
const isTablet = useIsTablet();
const showOptionalCols = !isTablet; // 平板档隐藏「A2A 目标」「更新时间」两列
```

- [ ] **步骤 2：页头允许换行**

```tsx
<Box sx={{ display: "flex", alignItems: "center", mb: 2 }}>
```

改为：

```tsx
<Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap", mb: 2 }}>
```

- [ ] **步骤 3：表头条件渲染隐藏列**

TableHead 的 7 个 TableCell 改为：

```tsx
<TableRow>
  <TableCell>名称</TableCell>
  <TableCell>路由</TableCell>
  <TableCell>A2A 地址</TableCell>
  <TableCell>状态</TableCell>
  {showOptionalCols && <TableCell>A2A 目标</TableCell>}
  {showOptionalCols && <TableCell>更新时间</TableCell>}
  <TableCell align="right">操作</TableCell>
</TableRow>
```

- [ ] **步骤 4：loading / 空态 colSpan 适配**

两处 `colSpan={7}` 改为 `colSpan={showOptionalCols ? 7 : 5}`。

- [ ] **步骤 5：数据行隐藏对应单元格**

`agents.map` 内，`A2A 目标` 与 `更新时间` 两个 `<TableCell>...</TableCell>` 分别用 `{showOptionalCols && (...)}` 包裹。

- [ ] **步骤 6：移动端卡片分支**

`return` 中把现有 `<TableContainer component={Paper} variant="outlined"> ... </TableContainer>` 整块包进 `!isMobile && (...)`，并在其上方添加卡片分支：

```tsx
{isMobile ? (
  <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
    {loading ? (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <CircularProgress size={24} />
      </Box>
    ) : agents.length === 0 ? (
      <Typography variant="body2" color="text.secondary" align="center" sx={{ py: 4 }}>
        暂无 Agent，点击右上角「新建 Agent」开始
      </Typography>
    ) : (
      agents.map((agent) => {
        const isDefault = agent.slug === "/";
        const busy = busyId === agent.id;
        return (
          <Paper key={agent.id} variant="outlined" sx={{ p: 2 }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography variant="body1" sx={{ flex: 1, minWidth: 0, fontWeight: 600 }} noWrap>
                {agent.name}
              </Typography>
              <Chip
                size="small"
                label={agent.status === "published" ? "已发布" : "草稿"}
                color={agent.status === "published" ? "success" : "default"}
              />
            </Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
              {isDefault ? "/（默认）" : `/${agent.slug}`}
              {agent.updated_at ? ` · ${new Date(agent.updated_at).toLocaleString()}` : ""}
            </Typography>
            {agent.description && (
              <Typography variant="body2" color="text.secondary" noWrap sx={{ mt: 0.5 }}>
                {agent.description}
              </Typography>
            )}
            {agent.status === "published" && (
              <Typography
                variant="caption"
                sx={{ display: "block", mt: 0.5, fontFamily: "monospace", wordBreak: "break-all" }}
              >
                {a2aPathForAgent(agent)}
              </Typography>
            )}
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
              A2A 目标：{targetSummary(agent)}
            </Typography>
            <Box sx={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 0.5, mt: 1.5, minHeight: 44 }}>
              {busy && <CircularProgress size={16} sx={{ mr: 0.5 }} />}
              <Tooltip title="编辑">
                <IconButton size="small" component={Link} href={`/admin/agents/${agent.id}/edit`} disabled={busy}>
                  <EditOutlinedIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title={agent.status === "published" ? "前往对话" : "草稿未发布，发布后可对话"}>
                <span>
                  <IconButton
                    size="small"
                    component={Link}
                    href={isDefault ? "/" : `/${agent.slug}`}
                    disabled={busy || agent.status !== "published"}
                  >
                    <ChatOutlinedIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
              <Tooltip title={agent.status === "published" ? "下线" : "发布"}>
                <span>
                  <IconButton
                    size="small"
                    onClick={() => handleTogglePublish(agent)}
                    disabled={busy || (isDefault && agent.status === "published")}
                  >
                    {agent.status === "published" ? <UnpublishedIcon fontSize="small" /> : <PublishIcon fontSize="small" />}
                  </IconButton>
                </span>
              </Tooltip>
              <Tooltip title="测试对话">
                <IconButton size="small" onClick={() => setTestAgent(agent)} disabled={busy}>
                  <ForumOutlinedIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title={isDefault ? "默认 Agent 不可删除" : "删除"}>
                <span>
                  <IconButton size="small" color="error" onClick={() => handleDelete(agent)} disabled={busy || isDefault}>
                    <DeleteOutlinedIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
            </Box>
          </Paper>
        );
      })
    )}
  </Box>
) : (
  <TableContainer component={Paper} variant="outlined">
    {/* 原表格整体保留，仅按步骤 3-5 改造 */}
  </TableContainer>
)}
```

- [ ] **步骤 7：验证**

运行：`npm run lint && npm run build`
预期：通过。桌面列数 7 不变；平板 5 列无横向滚动；375px 宽为卡片。

- [ ] **步骤 8：Commit**

```bash
git add src/app/admin/page.tsx
git commit -m "feat(web): Agent 管理页移动端卡片布局"
```

---

### 任务 9：A2A 管理页卡片化（`admin/a2a/page.tsx`）

**文件：**
- 修改：`web/src/app/admin/a2a/page.tsx`

- [ ] **步骤 1：引入 hook**

```tsx
import { useIsMobile, useIsTablet } from "@/lib/breakpoints";
```

组件体内：

```tsx
const isMobile = useIsMobile();
const showUpdatedAt = !useIsTablet(); // 平板档仅隐藏「更新时间」列
```

- [ ] **步骤 2：页头换行**

```tsx
<Box sx={{ display: "flex", alignItems: "flex-start", gap: 2, mb: 2 }}>
```

改为：

```tsx
<Box sx={{ display: "flex", alignItems: "flex-start", gap: 1, flexWrap: "wrap", mb: 2 }}>
```

- [ ] **步骤 3：表头与 colSpan**

表头：`<TableCell>更新时间</TableCell>` 改为 `{showUpdatedAt && <TableCell>更新时间</TableCell>}`；
两处 `colSpan={6}` 改为 `colSpan={showUpdatedAt ? 6 : 5}`；
数据行的更新时间 `<TableCell>` 用 `{showUpdatedAt && (...)}` 包裹。

- [ ] **步骤 4：移动端卡片分支**

现有 `<TableContainer>...</TableContainer>` 包进 `!isMobile && (...)`，上方添加：

```tsx
{isMobile ? (
  <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
    {loading ? (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <CircularProgress size={24} />
      </Box>
    ) : items.length === 0 ? (
      <Typography variant="body2" color="text.secondary" align="center" sx={{ py: 4 }}>
        暂无 A2A 目标，点击右上角「新建目标」添加
      </Typography>
    ) : (
      items.map((item) => (
        <Paper key={item.id} variant="outlined" sx={{ p: 2 }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Typography variant="body1" sx={{ flex: 1, minWidth: 0, fontWeight: 600 }} noWrap>
              {item.name}
            </Typography>
            <Chip size="small" label={item.enabled ? "启用" : "停用"} color={item.enabled ? "success" : "default"} />
          </Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
            {AUTH_TYPE_LABELS[item.auth_type] ?? item.auth_type}
            {item.updated_at ? ` · ${new Date(item.updated_at).toLocaleString()}` : ""}
          </Typography>
          {item.description && (
            <Typography variant="body2" color="text.secondary" noWrap sx={{ mt: 0.5 }}>
              {item.description}
            </Typography>
          )}
          <Typography
            variant="caption"
            sx={{ display: "block", mt: 0.5, fontFamily: "monospace", wordBreak: "break-all" }}
          >
            {item.url}
          </Typography>
          <Box sx={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 0.5, mt: 1.5, minHeight: 44 }}>
            {busyId === item.id && <CircularProgress size={16} sx={{ mr: 0.5 }} />}
            <Tooltip title="测试连接">
              <IconButton size="small" onClick={() => handleTest(item)} disabled={busyId === item.id}>
                <BoltIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="编辑">
              <IconButton size="small" onClick={() => openEdit(item)} disabled={busyId === item.id}>
                <EditOutlinedIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="删除">
              <IconButton size="small" color="error" onClick={() => handleDelete(item)} disabled={busyId === item.id}>
                <DeleteOutlinedIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Box>
        </Paper>
      ))
    )}
  </Box>
) : (
  <TableContainer component={Paper} variant="outlined">
    {/* 原表格整体保留，仅按步骤 3 改造 */}
  </TableContainer>
)}
```

- [ ] **步骤 5：验证**

运行：`npm run lint && npm run build`
预期：通过。

- [ ] **步骤 6：Commit**

```bash
git add src/app/admin/a2a/page.tsx
git commit -m "feat(web): A2A 管理页移动端卡片布局"
```

---

### 任务 10：MCP 管理页卡片化（`admin/mcp/page.tsx`）

**文件：**
- 修改：`web/src/app/admin/mcp/page.tsx`（仅列表区与页头；内联 Dialog 已在任务 7 处理）

- [ ] **步骤 1：引入 hook**

```tsx
import { useIsMobile, useIsTablet } from "@/lib/breakpoints";
```

组件体内：

```tsx
const isMobile = useIsMobile();
const showOptionalCols = !useIsTablet(); // 平板档隐藏「鉴权」「更新时间」
```

- [ ] **步骤 2：页头换行**

头部 Box（第 155 行）`gap: 2` 改为 `gap: 1, flexWrap: "wrap"`（`alignItems: "flex-start"` 保留）。

- [ ] **步骤 3：表头与 colSpan**

表头：`<TableCell>鉴权</TableCell>` 与 `<TableCell>更新时间</TableCell>` 分别用 `{showOptionalCols && ...}` 包裹；
两处 `colSpan={7}` 改为 `colSpan={showOptionalCols ? 7 : 5}`；
数据行对应的两个 `<TableCell>` 同样用 `{showOptionalCols && (...)}` 包裹。

- [ ] **步骤 4：移动端卡片分支**

现有 `<TableContainer>...</TableContainer>` 包进 `!isMobile && (...)`，上方添加：

```tsx
{isMobile ? (
  <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
    {loading ? (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <CircularProgress size={24} />
      </Box>
    ) : items.length === 0 ? (
      <Typography variant="body2" color="text.secondary" align="center" sx={{ py: 4 }}>
        暂无 MCP 服务，点击右上角「新建服务」添加
      </Typography>
    ) : (
      items.map((item) => (
        <Paper key={item.id} variant="outlined" sx={{ p: 2 }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Typography variant="body1" sx={{ flex: 1, minWidth: 0, fontWeight: 600 }} noWrap>
              {item.name}
            </Typography>
            <Chip size="small" label={item.enabled ? "启用" : "停用"} color={item.enabled ? "success" : "default"} />
          </Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
            {MCP_TRANSPORT_LABELS[item.transport] ?? item.transport} ·{" "}
            {AUTH_TYPE_LABELS[item.auth_type] ?? item.auth_type}
            {item.updated_at ? ` · ${new Date(item.updated_at).toLocaleString()}` : ""}
          </Typography>
          {item.description && (
            <Typography variant="body2" color="text.secondary" noWrap sx={{ mt: 0.5 }}>
              {item.description}
            </Typography>
          )}
          <MonoText variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
            {connectionText(item)}
          </MonoText>
          <Box sx={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 0.5, mt: 1.5, minHeight: 44 }}>
            {busyId === item.id && <CircularProgress size={16} sx={{ mr: 0.5 }} />}
            <Tooltip title="测试连接">
              <IconButton size="small" onClick={() => handleTest(item)} disabled={busyId === item.id}>
                <BoltIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="查看可用工具">
              <IconButton size="small" onClick={() => handleOpenTools(item)} disabled={busyId === item.id}>
                <ListAltIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="编辑">
              <IconButton size="small" onClick={() => openEdit(item)} disabled={busyId === item.id}>
                <EditOutlinedIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="删除">
              <IconButton size="small" color="error" onClick={() => handleDelete(item)} disabled={busyId === item.id}>
                <DeleteOutlinedIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Box>
        </Paper>
      ))
    )}
  </Box>
) : (
  <TableContainer component={Paper} variant="outlined">
    {/* 原表格整体保留，仅按步骤 3 改造 */}
  </TableContainer>
)}
```

- [ ] **步骤 5：验证**

运行：`npm run lint && npm run build`
预期：通过。

- [ ] **步骤 6：Commit**

```bash
git add src/app/admin/mcp/page.tsx
git commit -m "feat(web): MCP 管理页移动端卡片布局"
```

---

### 任务 11：Skill 管理页卡片化（`admin/skills/page.tsx`）

**文件：**
- 修改：`web/src/app/admin/skills/page.tsx`

- [ ] **步骤 1：引入 hook**

```tsx
import { useIsMobile, useIsTablet } from "@/lib/breakpoints";
```

组件体内：

```tsx
const isMobile = useIsMobile();
const showOptionalCols = !useIsTablet(); // 平板档隐藏「加载模式」「来源」
```

- [ ] **步骤 2：页头换行**

头部 Box（第 111 行）`gap: 2` 改为 `gap: 1, flexWrap: "wrap"`。

- [ ] **步骤 3：表头与 colSpan**

表头：`<TableCell>加载模式</TableCell>` 与 `<TableCell>来源</TableCell>` 分别用 `{showOptionalCols && ...}` 包裹；
两处 `colSpan={7}` 改为 `colSpan={showOptionalCols ? 7 : 5}`；
数据行对应的两个 `<TableCell>` 同样用 `{showOptionalCols && (...)}` 包裹。

- [ ] **步骤 4：移动端卡片分支**

现有 `<TableContainer>...</TableContainer>` 包进 `!isMobile && (...)`，上方添加：

```tsx
{isMobile ? (
  <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
    {loading ? (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <CircularProgress size={24} />
      </Box>
    ) : skills.length === 0 ? (
      <Typography variant="body2" color="text.secondary" align="center" sx={{ py: 4 }}>
        还没有导入任何 Skill，点击右上角「导入 Skill」添加
      </Typography>
    ) : (
      skills.map((skill) => (
        <Paper key={skill.id} variant="outlined" sx={{ p: 2 }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Typography variant="body1" sx={{ flex: 1, minWidth: 0, fontWeight: 600 }} noWrap>
              {skill.name}
            </Typography>
            <Chip size="small" {...STATUS_CHIP[skill.review_status]} />
          </Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
            {skill.load_mode === "always" ? "常驻" : "按需"} · {formatBytes(skill.size_bytes)} /{" "}
            {skill.file_count} 附件 · {skill.source}
          </Typography>
          {skill.description && (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ mt: 0.5, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}
            >
              {skill.description}
            </Typography>
          )}
          {!skill.enabled && <Chip size="small" label="已停用" sx={{ mt: 1 }} />}
          <Box sx={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 0.5, mt: 1.5, minHeight: 44, flexWrap: "wrap" }}>
            {busyId === skill.id && <CircularProgress size={16} sx={{ mr: 0.5 }} />}
            <Button size="small" startIcon={<VisibilityIcon />} onClick={() => setDetailSkill(skill)}>
              详情
            </Button>
            <Button size="small" startIcon={<EditIcon />} onClick={() => setEditSkill(skill)} disabled={busyId === skill.id}>
              编辑
            </Button>
            {skill.review_status !== "approved" && (
              <Button size="small" onClick={() => void review(skill, "approved")} disabled={busyId === skill.id}>
                通过
              </Button>
            )}
            {skill.review_status !== "rejected" && (
              <Button size="small" color="warning" onClick={() => void review(skill, "rejected")} disabled={busyId === skill.id}>
                拒绝
              </Button>
            )}
            <Button size="small" color="error" onClick={() => void remove(skill)} disabled={busyId === skill.id}>
              删除
            </Button>
          </Box>
        </Paper>
      ))
    )}
  </Box>
) : (
  <TableContainer component={Paper} variant="outlined">
    {/* 原表格整体保留，仅按步骤 3 改造 */}
  </TableContainer>
)}
```

- [ ] **步骤 5：验证**

运行：`npm run lint && npm run build`
预期：通过。

- [ ] **步骤 6：Commit**

```bash
git add src/app/admin/skills/page.tsx
git commit -m "feat(web): Skill 管理页移动端卡片布局"
```

---

### 任务 12：AgentApiKeys 窄屏适配（`AgentApiKeys.tsx`）

**文件：**
- 修改：`web/src/components/admin/AgentApiKeys.tsx`

- [ ] **步骤 1：引入 hook 与 Paper**

```tsx
import { Paper } from "@mui/material"; // 并入现有 MUI 导入列表
import { useIsMobile } from "@/lib/breakpoints";
```

组件体内：`const isMobile = useIsMobile();`

- [ ] **步骤 2：新建表单窄屏纵排**

第 118 行：

```tsx
<Stack direction="row" sx={{ mb: 2 }} spacing={1}>
```

改为：

```tsx
<Stack direction={{ xs: "column", sm: "row" }} sx={{ mb: 2 }} spacing={1}>
```

第 129 行 TextField `sx={{ width: 280 }}` 改为：

```tsx
sx={{ width: { xs: "100%", sm: 280 } }}
```

- [ ] **步骤 3：移动端卡片分支（平板保留 4 列表格）**

现有 `<Table size="small"> ... </Table>` 包进 `!isMobile && (...)`，上方添加：

```tsx
{isMobile ? (
  <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
    {loading ? (
      <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
        <CircularProgress size={22} />
      </Box>
    ) : items.length === 0 ? (
      <Typography variant="body2" color="text.secondary" align="center" sx={{ py: 3 }}>
        暂无 API Key，保存后会自动生成默认 Key
      </Typography>
    ) : (
      items.map((item) => (
        <Paper key={item.id} variant="outlined" sx={{ p: 2 }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Typography variant="body2" sx={{ flex: 1, minWidth: 0, fontWeight: 600 }} noWrap>
              {item.name}
            </Typography>
            <Chip size="small" label={item.is_default ? "默认" : "自定义"} color={item.is_default ? "primary" : "default"} />
          </Box>
          <Stack direction="row" spacing={0.5} sx={{ alignItems: "flex-start", mt: 0.5 }}>
            <Typography
              variant="caption"
              sx={{ fontFamily: "monospace", flex: 1, minWidth: 0, wordBreak: "break-all" }}
            >
              {item.key}
            </Typography>
            <IconButton size="small" onClick={() => void copyKey(item.key)} aria-label="复制 Key">
              <ContentCopyOutlinedIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </Stack>
          <Box sx={{ display: "flex", justifyContent: "flex-end", alignItems: "center", minHeight: 44 }}>
            {busyId === item.id && <CircularProgress size={16} sx={{ mr: 0.5 }} />}
            <Tooltip title={item.is_default ? "默认 Key 不可删除" : "删除"}>
              <span>
                <IconButton
                  size="small"
                  color="error"
                  onClick={() => handleDelete(item)}
                  disabled={busyId === item.id || item.is_default}
                >
                  <DeleteOutlinedIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Box>
        </Paper>
      ))
    )}
  </Box>
) : (
  <Table size="small">{/* 原表格整体保留 */}</Table>
)}
```

- [ ] **步骤 4：验证**

运行：`npm run lint && npm run build`
预期：通过。

- [ ] **步骤 5：Commit**

```bash
git add src/components/admin/AgentApiKeys.tsx
git commit -m "feat(web): API Key 列表移动端卡片布局"
```

---

### 任务 13：全量验证与收尾

**文件：** 无新改动（只验证；若发现问题回到对应任务修复后重跑本任务）

- [ ] **步骤 1：全量验证**

```bash
npm run lint
npx vitest run
npm run build
```

预期：lint 0 error；vitest 5 个测试文件全绿；build 成功。

- [ ] **步骤 2：桌面回归自查（代码层面）**

`git diff 964f86d..HEAD --stat` 确认改动文件仅限本计划文件结构表所列；对 `ChatPage.tsx`、`AdminShell.tsx`、4 个列表页的 diff 逐一检查 `md` 及以上断点的属性值未变（侧栏 288、表格全列、Dialog 非 fullScreen）。

- [ ] **步骤 3：手动验收**

启动 dev 服务（`npm run dev`），按规格附录 A 在 375×667 / 390×844 / 768×1024 / ≥900px 四档执行验收清单；真机项（标注 B）交由用户在真机确认。

- [ ] **步骤 4：收尾汇报**

向用户汇报：任务完成情况、每档形态截图或描述、遗留真机验证项。

---

## 执行交接

计划已保存到 `docs/superpowers/plans/2026-09-22-web-mobile-adaptation.md`。两种执行方式：

1. **子代理驱动（推荐）** — 每个任务调度一个新的子代理实现，任务间进行审查，快速迭代（subagent-driven-development）。
2. **内联执行** — 在当前会话中用 executing-plans 批量执行并设检查点。

执行前建议确认：是否在独立 git worktree / 分支上进行（当前在 `master`）。




