"use client";

import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  FormControl,
  FormHelperText,
  InputLabel,
  MenuItem,
  Select,
  TextField,
} from "@mui/material";
import { Connector, ConnectorConversation, adminApi } from "@/lib/adminApi";
import DialogTitleBar from "./DialogTitleBar";
import { useIsMobile } from "@/lib/breakpoints";

interface SendTestDialogProps {
  connector: Connector;
  onClose: () => void;
}

/** 最近活跃会话的展示文案：chat_id（类型 · 发言人）。 */
function conversationLabel(item: ConnectorConversation): string {
  const type = item.chat_type === "group" ? "群聊" : "私聊";
  const name = item.last_user_ref?.display_name;
  return `${item.chat_id}（${type}${typeof name === "string" && name ? ` · ${name}` : ""}）`;
}

/** 发送测试消息：下拉选最近活跃会话，或手填 chat_id。 */
export default function SendTestDialog({ connector, onClose }: SendTestDialogProps) {
  const isMobile = useIsMobile();
  const [conversations, setConversations] = useState<ConnectorConversation[]>([]);
  const [selectedChatId, setSelectedChatId] = useState<string>("");
  const [manualChatId, setManualChatId] = useState<string>("");
  const [text, setText] = useState("连接器测试消息");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    adminApi
      .listConnectorConversations(connector.id)
      .then((rows) => {
        if (alive) setConversations(rows);
      })
      .catch(() => {
        /* 下拉留空，可手填 chat_id */
      });
    return () => {
      alive = false;
    };
  }, [connector.id]);

  const submit = async () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setSending(true);
    setError(null);
    setToast(null);
    try {
      const res = await adminApi.sendConnectorMessage(connector.id, {
        chat_id: manualChatId.trim() || selectedChatId || null,
        text: trimmed,
      });
      setToast(`已推送到会话 ${res.chat_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "推送失败");
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth fullScreen={isMobile}>
      <DialogTitleBar title={`发送测试消息 · ${connector.name}`} onClose={onClose} />
      <DialogContent dividers>
        <FormControl fullWidth size="small" sx={{ mb: 2 }}>
          <InputLabel id="connector-chat-label">目标会话</InputLabel>
          <Select
            labelId="connector-chat-label"
            label="目标会话"
            value={selectedChatId}
            onChange={(e) => setSelectedChatId(e.target.value)}
          >
            <MenuItem value="">最近活跃会话（自动）</MenuItem>
            {conversations.map((item) => (
              <MenuItem key={item.chat_id} value={item.chat_id}>
                {conversationLabel(item)}
              </MenuItem>
            ))}
          </Select>
          <FormHelperText>
            {conversations.length === 0
              ? "该连接器还没有会话，请先在聊天应用中发起一次对话，或手填 chat_id"
              : "按最近活跃时间排序"}
          </FormHelperText>
        </FormControl>
        <TextField
          label="或手填 chat_id"
          fullWidth
          size="small"
          value={manualChatId}
          onChange={(e) => setManualChatId(e.target.value)}
          helperText="填写后优先于上方选择"
        />
        <TextField
          label="消息文本"
          fullWidth
          size="small"
          multiline
          minRows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          sx={{ mt: 2 }}
        />
        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
        {toast && (
          <Alert severity="success" sx={{ mt: 2 }}>
            {toast}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>关闭</Button>
        <Button
          variant="contained"
          onClick={() => void submit()}
          disabled={sending || !text.trim()}
          startIcon={sending ? <CircularProgress size={16} /> : undefined}
        >
          发送
        </Button>
      </DialogActions>
    </Dialog>
  );
}
