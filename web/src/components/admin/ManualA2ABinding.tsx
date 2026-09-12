"use client";

import {
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import { A2ATargetInput } from "@/lib/adminApi";

interface ManualA2ABindingProps {
  value: A2ATargetInput[];
  onChange: (value: A2ATargetInput[]) => void;
}

/** 手动绑定的 A2A 目标（无需在「A2A 管理」注册，可与勾选并存）。 */
export default function ManualA2ABinding({ value, onChange }: ManualA2ABindingProps) {
  const addRow = () => onChange([...value, { url: "", token: "", description: "" }]);

  const updateRow = (index: number, patch: Partial<A2ATargetInput>) => {
    onChange(value.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  };

  const removeRow = (index: number) => onChange(value.filter((_, i) => i !== index));

  return (
    <Box sx={{ mt: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Typography variant="body2">手动绑定（未注册目标）</Typography>
        <Button size="small" startIcon={<AddIcon />} onClick={addRow}>
          添加
        </Button>
      </Box>
      <Typography variant="caption" color="text.secondary">
        直接填写地址即可，无需先在「A2A 管理」登记；说明会帮助模型判断何时调用该目标。
      </Typography>

      {value.length === 0 ? (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
          暂无手动绑定的目标
        </Typography>
      ) : (
        <Stack spacing={1} sx={{ mt: 1 }}>
          {value.map((item, index) => (
            <Stack key={index} direction="row" spacing={1} sx={{ alignItems: "flex-start" }}>
              <TextField
                size="small"
                required
                label="地址"
                placeholder="http://host:port/"
                value={item.url}
                onChange={(e) => updateRow(index, { url: e.target.value })}
                sx={{ flex: 2, minWidth: 0, fontFamily: "monospace" }}
              />
              <TextField
                size="small"
                label="密钥（可选）"
                value={item.token}
                onChange={(e) => updateRow(index, { token: e.target.value })}
                sx={{ flex: 1, minWidth: 0 }}
              />
              <TextField
                size="small"
                label="说明（可选）"
                value={item.description ?? ""}
                onChange={(e) => updateRow(index, { description: e.target.value })}
                sx={{ flex: 2, minWidth: 0 }}
              />
              <Tooltip title="移除">
                <IconButton size="small" color="error" onClick={() => removeRow(index)}>
                  <DeleteOutlinedIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Stack>
          ))}
        </Stack>
      )}
    </Box>
  );
}
