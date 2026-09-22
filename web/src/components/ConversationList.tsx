"use client";

import { ReactNode } from "react";
import {
  Box,
  Button,
  Divider,
  IconButton,
  List,
  ListItemButton,
  ListItemText,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutlined";
import ForumOutlinedIcon from "@mui/icons-material/ForumOutlined";
import { Conversation } from "@/lib/conversations";

interface ConversationListProps {
  agentName: string;
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  /** 底部自定义区域（如「登录 / 管理中心」入口） */
  footer?: ReactNode;
}

/** 把时间戳格式化为「今天显示时分，否则显示月/日」。 */
function formatTime(ts: number): string {
  const d = new Date(ts);
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  if (d.toDateString() === now.toDateString()) {
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export default function ConversationList({
  agentName,
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  footer,
}: ConversationListProps) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minWidth: 0 }}>
      <Box sx={{ p: 1.5 }}>
        <Button
          fullWidth
          variant="outlined"
          startIcon={<AddIcon />}
          onClick={onNew}
          sx={{ justifyContent: "flex-start", textTransform: "none" }}
        >
          新建对话
        </Button>
      </Box>
      <Divider />

      <Box sx={{ px: 2, pt: 1.5, pb: 0.5 }}>
        <Typography variant="caption" color="text.secondary">
          对话历史
        </Typography>
      </Box>

      <Box sx={{ flex: 1, overflowY: "auto", scrollbarGutter: "stable", px: 1, pb: 1 }}>
        {conversations.length === 0 ? (
          <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center", mt: 4, px: 2, gap: 1 }}>
            <ForumOutlinedIcon sx={{ color: "text.disabled" }} />
            <Typography variant="caption" color="text.secondary" align="center">
              暂无历史对话
              <br />
              发送消息后会自动保存在这里
            </Typography>
          </Box>
        ) : (
          <List disablePadding>
            {conversations.map((conv) => {
              const selected = conv.id === activeId;
              return (
                <ListItemButton
                  key={conv.id}
                  selected={selected}
                  onClick={() => onSelect(conv.id)}
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
                >
                  <ListItemText
                    disableTypography
                    primary={
                      <Typography variant="body2" noWrap sx={{ fontSize: 14 }}>
                        {conv.title || "新对话"}
                      </Typography>
                    }
                  />
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ ml: 0.5, flexShrink: 0, fontSize: 11 }}
                  >
                    {formatTime(conv.updatedAt)}
                  </Typography>
                  <Tooltip title="删除会话">
                    <IconButton
                      className="conv-delete"
                      size="small"
                      edge="end"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(conv.id);
                      }}
                      sx={{ ml: 0.5 }}
                    >
                      <DeleteOutlineIcon fontSize="inherit" />
                    </IconButton>
                  </Tooltip>
                </ListItemButton>
              );
            })}
          </List>
        )}
      </Box>

      <Divider />
      <Box sx={{ p: 1.5, display: "flex", flexDirection: "column", gap: 1 }}>
        {footer}
        <Typography variant="caption" color="text.secondary" noWrap sx={{ display: "block" }}>
          当前 Agent：{agentName}
        </Typography>
      </Box>
    </Box>
  );
}
