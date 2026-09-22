"use client";

import { useEffect, useRef, useState, KeyboardEvent } from "react";
import { Box, TextField, IconButton, Tooltip, CircularProgress, Typography } from "@mui/material";
import SendIcon from "@mui/icons-material/Send";
import MicIcon from "@mui/icons-material/Mic";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import { AsrRecorder } from "@/lib/speech";
import { useIsMobile } from "@/lib/breakpoints";

interface ChatInputProps {
  onSend: (message: string) => void;
  disabled?: boolean;
}

export default function ChatInput({ onSend, disabled }: ChatInputProps) {
  const isMobile = useIsMobile();
  const [value, setValue] = useState("");
  const [recording, setRecording] = useState(false);
  const [speechError, setSpeechError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const recorderRef = useRef<AsrRecorder | null>(null);
  const finalTextRef = useRef("");

  useEffect(() => {
    return () => {
      recorderRef.current?.stop();
    };
  }, []);

  const handleSend = () => {
    const text = value.trim();
    if (!text || disabled) return;
    onSend(text);
    setValue("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    // 移动端软键盘「换行」只换行，发送仅通过发送按钮；桌面保持 Enter 发送
    if (isMobile) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleToggleRecording = async () => {
    if (recording) {
      recorderRef.current?.stop();
      recorderRef.current = null;
      setRecording(false);
      return;
    }

    setSpeechError(null);
    setStarting(true);
    // 录音期间保留输入框已有内容，识别文本追加其后
    const baseText = value.trim() ? value.trimEnd() + " " : "";
    finalTextRef.current = baseText;
    const recorder = new AsrRecorder({
      onResult: (text, isFinal) => {
        // 流式部分结果实时回显；本段结束时固化为最终文本
        const display = finalTextRef.current + (isFinal ? "" : text);
        setValue(display);
        if (isFinal) finalTextRef.current = finalTextRef.current + text;
      },
      onError: (message) => {
        setSpeechError(message);
        recorderRef.current = null;
        setRecording(false);
        recorder.stop();
      },
      onClose: () => {
        setRecording(false);
      },
    });
    try {
      await recorder.start();
      recorderRef.current = recorder;
      setRecording(true);
    } catch (err) {
      const message =
        err instanceof Error && /超时|连接/.test(err.message)
          ? err.message
          : "无法访问麦克风，请检查浏览器权限";
      setSpeechError(message);
    } finally {
      setStarting(false);
    }
  };

  const micDisabled = disabled || starting || (typeof navigator !== "undefined" && !navigator.mediaDevices);

  return (
    <Box sx={{ borderTop: 1, borderColor: "divider", bgcolor: "background.paper" }}>
      {speechError && (
        <Typography
          variant="caption"
          color="error"
          sx={{ display: "flex", alignItems: "center", px: 2, pt: 1, justifyContent: "space-between" }}
        >
          {speechError}
          <Box
            component="span"
            onClick={() => setSpeechError(null)}
            sx={{ cursor: "pointer", ml: 1, textDecoration: "underline" }}
          >
            关闭
          </Box>
        </Typography>
      )}
      <Box sx={{ display: "flex", gap: 1, p: 2 }}>
        <Tooltip title={recording ? "停止录音" : "语音输入（中英双语）"}>
          <IconButton
            color={recording ? "error" : "primary"}
            onClick={() => void handleToggleRecording()}
            disabled={micDisabled}
            sx={{ alignSelf: "flex-end" }}
          >
            {starting ? (
              <CircularProgress size={22} />
            ) : recording ? (
              <StopCircleIcon />
            ) : (
              <MicIcon />
            )}
          </IconButton>
        </Tooltip>
        <TextField
          fullWidth
          multiline
          maxRows={4}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            recording
              ? "正在聆听，请说话...（再次点击麦克风结束）"
              : isMobile
                ? "输入消息，点击右侧按钮发送"
                : "输入消息...（Enter 发送，Shift+Enter 换行）"
          }
          variant="outlined"
          size="small"
          disabled={disabled}
        />
        <IconButton
          color="primary"
          onClick={handleSend}
          disabled={disabled || !value.trim()}
          sx={{ alignSelf: "flex-end" }}
        >
          <SendIcon />
        </IconButton>
      </Box>
    </Box>
  );
}
