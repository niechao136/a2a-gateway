# Web 移动端适配设计规格

- 日期：2026-09-22
- 状态：已批准（设计对话中确认）
- 范围：`web/` 前端（Next.js 16 + MUI 9 + Tailwind 4，无其他 UI 依赖）

## 1. 背景与目标

`web/` 前端的布局骨架（`ChatPage`、`AdminShell`）已有桌面/移动双形态（`md` 断点 + Drawer），但存在以下缺口：

1. admin 4 个列表页与 `AgentApiKeys` 均为宽表格，窄屏横向溢出；
2. 9 处 Dialog 未做窄屏处理；
3. `ConversationList` 删除按钮仅 `:hover` 显示，触屏上不可见（功能缺陷）；
4. 4 处 `100vh` 在移动浏览器（地址栏收展）下高度失真，聊天输入框可能被遮；
5. 无显式 `viewport` 导出，未声明键盘弹起行为；
6. 平板竖屏区间（600-899px）直接套用手机形态，体验粗糙。

目标：手机（<600px）与平板竖屏（600-899px）获得完整、可用的体验；桌面（≥900px）现有体验零变化。

## 2. 已确认的决策

| 决策点 | 结论 |
|---|---|
| 覆盖范围 | 全部：聊天页 + admin 4 列表页 + 全部 Dialog + 全局基础修复 |
| admin 表格窄屏形态 | <600px 切卡片布局，操作按钮常显 |
| 目标设备 | 手机 + 平板竖屏（600-899px 做中间态优化）；不含刘海屏 safe-area |
| 验证方式 | 现有 vitest 保持全绿 + 手动验收清单（见附录） |
| 平板竖屏聊天页 | 双栏，侧栏收窄至 240（而非抽屉） |
| Dialog 手机档 | 直接全屏（fullScreen） |

## 3. 断点与三档形态

沿用 MUI 默认断点（xs<600 / sm 600-899 / md≥900），**不修改主题断点定义**。

| 区间 | 聊天页 | admin 列表页 | Dialog | 表单（AgentForm 等） |
|---|---|---|---|---|
| <600 手机 | 单栏 + 抽屉（现状） | 卡片布局 | fullScreen + 内部滚动 | 单列（现状即如此） |
| 600-899 平板竖屏 | 双栏，侧栏 240px | 精简表格（隐藏次要列） | maxWidth="sm" + fullWidth | 单列（现状） |
| ≥900 桌面 | 现状（侧栏 288） | 现状 | 现状 | 现状 |

## 4. 新增共享原语

新建 `web/src/lib/breakpoints.ts`：

```ts
useIsMobile()   // theme.breakpoints.down("sm")   → <600px
useIsTablet()   // theme.breakpoints.between("sm","md") → 600-899px
```

- 与现有 `useMediaQuery(theme.breakpoints.up("md"))` 用法保持一致（`useTheme` + `useMediaQuery` 封装）。
- **只抽 hook，不抽共享卡片组件**：各实体字段差异大，动态抽象（columns → card 通用映射器）属过度设计。各页卡片遵循第 7 节的结构约定独立实现。
- 新增文件：`web/src/lib/breakpoints.ts`。

## 5. 全局基础修复

### 5.1 显式 viewport 导出（`web/src/app/layout.tsx`）

```ts
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  interactiveWidget: "resizes-content", // 键盘弹起收缩视口而非遮挡输入框
};
```

### 5.2 100vh → 100dvh（4 处）

`ChatPage.tsx`、`AdminShell.tsx`、`admin/login/page.tsx`、`[[...slug]]/page.tsx`：

```
height: "100vh",
"@supports (height: 100dvh)": { height: "100dvh" },
```

注意：不能在同一个 JS 对象里双写 `height`（ESLint no-dupe-keys），必须通过 `@supports` 条件覆盖；旧浏览器自动回退 `100vh`。

### 5.3 触屏 hover 缺失修复（`ConversationList.tsx`）

删除按钮的显隐改用 CSS 媒体查询控制：

```
"@media (hover: hover)": { "& .conv-delete": { opacity: 0 }, "&:hover .conv-delete": { opacity: 1 } }
```

触屏设备（无 hover 能力）删除按钮常显。

### 5.4 触控目标

移动端（`useIsMobile` 为 true 时）通过 sx 给小尺寸 `IconButton` 增加 padding，使可点区域 ≥44×44。范围：聊天顶栏、消息气泡操作行、卡片操作行、抽屉内列表项。

### 5.5 消息气泡

`MessageBubble.tsx` 气泡容器 `maxWidth` 由固定 75% 改为 `{ xs: "85%", sm: "75%" }`。

## 6. 聊天页适配（`ChatPage.tsx`）

- 常驻侧栏条件从 `breakpoints.up("md")` 放宽到 `up("sm")`：≥600px 双栏，<600px 抽屉（现状）。
- 侧栏宽度：`{ sm: 240, md: 288 }`（Drawer 宽度维持 288）。
- 移动端 Enter 行为：现状 `Enter` 发送在软键盘上表现为"换行键=发送"，用户无法换行。改为**移动端（`useIsMobile`）Enter 仅换行，发送只通过发送按钮**；桌面 Enter 发送保持不变。
- 其余（顶栏、消息区、输入区、Alert）已响应式，不动；仅按 5.4 微调触控目标。

## 7. admin 列表页卡片化

### 7.1 卡片结构约定（统一）

```
┌───────────────────────────────┐
│ 实体名称（主标题）      [状态Chip] │
│ 次要元信息（caption，1 行）        │
│ 描述 / 地址（monospace 截断，≤2 行）│
│ ─────────────────────────────│
│ 操作按钮行（常显，右对齐）          │
└───────────────────────────────┘
```

- 容器：`Paper variant="outlined"`，`mb: 1.5`，纵向排列。
- 操作按钮与表格版共用同一批 handler，不复制业务逻辑。
- 空态 / loading 复用页面现有分支逻辑。

### 7.2 各页映射与平板隐藏列

| 页面 | 卡片标题 | 元信息 | 内容行 | 操作 | 平板隐藏列 |
|---|---|---|---|---|---|
| Agent 管理 `/admin` | name + 发布状态 Chip | `/{slug}` · 更新时间 | 描述；A2A 地址（mono）；A2A 目标摘要 | 编辑/对话/发布下线/测试对话/删除 | A2A 目标、更新时间 |
| A2A 管理 `/admin/a2a` | name + 启停 Chip | 鉴权 · 更新时间 | 服务地址（mono）；描述 | 测试连接/编辑/删除 | 更新时间 |
| MCP 管理 `/admin/mcp` | name + 启停 Chip | 传输方式 · 鉴权 · 更新时间 | 连接信息（mono，stdio 为命令）；描述 | 测试连接/查看工具/编辑/删除 | 鉴权、更新时间 |
| Skill 管理 `/admin/skills` | name + 审核 Chip（+停用 Chip） | 加载模式 · 大小/附件 · 来源 | 说明（≤2 行） | 详情/编辑/通过/拒绝/删除 | 加载模式、来源 |
| `AgentApiKeys`（AgentForm 内嵌） | key 名称 + 状态 Chip | — | Key（mono 截断 + 复制） | 复制/删除 | 平板保留表格（仅 4 列） |

- 实现位置：每页一个 `XxxCard` 内联组件（放在对应 `page.tsx` 内），`AgentApiKeys` 卡片放其组件文件内。
- 渲染分支：`useIsMobile() ? <XxxCard/> : <Table/>`；平板档表格通过条件渲染隐藏指定列。

## 8. Dialog 适配

9 处 Dialog（8 个组件 + `mcp/page.tsx` 内联"可用工具"）统一模式：

```tsx
const isMobile = useIsMobile();
<Dialog fullScreen={isMobile} maxWidth="sm" fullWidth ...>
```

- fullScreen 档：`DialogTitle` 加关闭按钮（`fullScreen` 无默认关闭交互），`DialogContent` 内部滚动。
- 组件清单：`A2AEndpointDialog`、`McpServerDialog`、`SkillDetailDialog`、`SkillEditDialog`、`SkillImportDialog`、`TestChatDialog`、`ManualA2ABinding`、`ManualMcpBinding`、mcp 页内联 Dialog。

### 表单

`AgentForm` 已是单列 Stack + fullWidth 布局，**无需布局改造**，仅纳入验收清单验证。

## 9. 明确不做的事（YAGNI）

- 不引入新 UI 依赖（DataGrid /别的组件库/手势库）；
- 不做刘海屏 safe-area（`viewportFit`/`env(safe-area-inset)`）；
- 不做平板横屏专门优化（≥900 走桌面形态）；
- 不改主题断点、不改后端、不动 `next.config.ts`；
- 不做 PWA / 离线。

## 10. 验证策略

1. `npm run lint` 与 `npm run build` 通过（在 `web/` 目录）；
2. 现有 5 个 vitest 测试（`src/lib/*.test.ts`）保持全绿。`useIsMobile/useIsTablet` 为 `useMediaQuery` 薄封装，不强制单测（项目未装 React 测试工具，不为薄封装引入新 dev 依赖）；若实现中产生纯逻辑单元（如各页列隐藏配置），为其补测；
3. 手动验收清单（附录 A）：375×667（iPhone SE）、390×844（iPhone 15）、768×1024（iPad 竖屏）+ 900px 回归桌面形态；
4. 回归红线：≥900px 桌面各页视觉与交互零变化（diff 审查 + 桌面手动过一遍）。

---

## 附录 A：手动验收清单

设备档位：**A** = 375×667（iPhone SE）、**B** = 390×844（iPhone 15）、**C** = 768×1024（iPad 竖屏）、**D** = ≥900px 桌面（回归）。
每项在指定档位下逐条勾选；标注（B）的项目需在真机额外验证（键盘/麦克风/剪贴板等模拟器不可靠）。

### A1 聊天页（A B C D）

- [ ] A：单栏 + 汉堡菜单，抽屉可开合、选会话后自动关闭
- [ ] A（B）：地址栏收/展时页面不跳动，输入框始终可见（100dvh）
- [ ] A（B）：点击输入框弹起键盘后，正在输入的行可见（interactive-widget）
- [ ] B：消息气泡占宽约 85%，长词/URL 换行正常
- [ ] B（A）：复制/播放/重试按钮可点，触控区域不局促
- [ ] C：双栏布局，侧栏 240px，对话区无横向滚动
- [ ] D：侧栏 288px，与改动前一致（回归）
- [ ] A：会话列表项删除按钮**无需长按即可见**（触屏修复）
- [ ] B（A）：流式回复滚动到底部正常；SSE 中途锁屏/回前台不丢消息

### A2 聊天输入（A B）

- [ ] 语音按钮、发送按钮触控目标 ≥44px
- [ ] （B）语音输入：授权弹窗、录音、停止后文本回填正常
- [ ] A（B）：软键盘"换行"键仅换行不发送；发送只通过发送按钮
- [ ] D：桌面 Enter 发送、Shift+Enter 换行行为不变（回归）

### A3 admin 列表页 ×4（A C D）

- [ ] A：Agent/A2A/MCP/Skill 每行渲染为卡片，操作按钮常显可点
- [ ] A：卡片信息完整（名称、状态、路由/地址、更新时间不丢失）
- [ ] C：精简表格无横向滚动，隐藏列不产生布局裂缝
- [ ] D：完整表格列数与现状一致（回归）
- [ ] A：页头按钮（新建/导入）不与标题挤压换行错乱

### A4 Dialog ×9（A B C）

- [ ] A：全部 Dialog 全屏展示，右上角有关闭按钮，内容可滚动
- [ ] A（B）：McpServerDialog / SkillEditDialog 长表单滚动到底部不裁切
- [ ] A（B）：TestChatDialog 全屏下可正常对话（键盘弹起输入可见）
- [ ] C：Dialog 居中 maxWidth=sm（600px）而非全屏，内容不裁切

### A5 登录页与 Agent 编辑页（A C）

- [ ] A：登录卡片无溢出，键盘弹起不遮挡输入框
- [ ] A：AgentForm 表单单列可完整操作；AgentApiKeys 卡片化，Key 可复制
- [ ] C：AgentApiKeys 保持表格，无横向滚动

### A6 主题与回归（A B C D）

- [ ] 深色/浅色切换后各档位无白色闪块、无溢出
- [ ] D：桌面端逐页过一遍（聊天 + 4 列表 + 1 Dialog + 登录 + 编辑），与改动前一致
