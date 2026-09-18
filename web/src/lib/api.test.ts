/**
 * SSE 解析回归测试。
 *
 * 背景：sse-starlette 使用 CRLF（\r\n）分隔事件，若只用 "\n\n" 切分，
 * 事件块永远切不出来 —— 表现为对话界面不显示流式内容、也不保存 thread_id。
 */

import { describe, expect, it } from "vitest";
import { parseSSEBlock, parseSSEEvent, splitSSEBlocks } from "./api";

describe("splitSSEBlocks", () => {
  it("能切分 CRLF 分隔的事件块（sse-starlette 的实际输出）", () => {
    const buffer =
      'event: token\r\ndata: {"content":"hi"}\r\n\r\nevent: done\r\ndata: {"thread_id":"t1"}\r\n\r\n';

    const [blocks, rest] = splitSSEBlocks(buffer);

    expect(blocks).toHaveLength(2);
    expect(rest).toBe("");
  });

  it("能切分 LF 分隔的事件块", () => {
    const buffer = 'event: token\ndata: {"content":"hi"}\n\nevent: done\ndata: {}\n\n';

    const [blocks, rest] = splitSSEBlocks(buffer);

    expect(blocks).toHaveLength(2);
    expect(rest).toBe("");
  });

  it("把未完成的事件块保留在 rest 中（等待下一个分片）", () => {
    const buffer = 'event: token\r\ndata: {"content":"hi"}\r\n\r\nevent: do';

    const [blocks, rest] = splitSSEBlocks(buffer);

    expect(blocks).toHaveLength(1);
    expect(rest).toBe("event: do");
  });

  it("回归：单纯用 \\n\\n 切分无法处理 CRLF（防止改回错误实现）", () => {
    const buffer = "event: token\r\ndata: 1\r\n\r\n";

    expect(buffer.split("\n\n")).toHaveLength(1); // 旧实现：切不出事件
    expect(splitSSEBlocks(buffer)[0]).toHaveLength(1); // 新实现：正常
  });
});

describe("parseSSEBlock", () => {
  it("提取 event 名与 data 内容（兼容 CRLF）", () => {
    expect(parseSSEBlock('event: token\r\ndata: {"content":"hi"}')).toEqual({
      event: "token",
      data: '{"content":"hi"}',
    });
  });

  it("未声明 event 时默认为 message", () => {
    expect(parseSSEBlock("data: 1")).toEqual({ event: "message", data: "1" });
  });

  it("无 data 的块返回 null", () => {
    expect(parseSSEBlock("event: ping")).toBeNull();
  });
});

describe("parseSSEEvent", () => {
  it("解析 interrupt 事件（等待用户补充信息）", () => {
    expect(parseSSEEvent("interrupt", '{"question":"请补充目的地"}')).toEqual({
      type: "interrupt",
      question: "请补充目的地",
    });
  });

  it("interrupt 缺少 question 字段时降级为空串", () => {
    expect(parseSSEEvent("interrupt", "{}")).toEqual({ type: "interrupt", question: "" });
  });

  it("解析 token 事件", () => {
    expect(parseSSEEvent("token", '{"content":"hi"}')).toEqual({ type: "token", content: "hi" });
  });

  it("未知事件返回 null", () => {
    expect(parseSSEEvent("ping", "{}")).toBeNull();
  });

  it("非法 JSON 返回 null", () => {
    expect(parseSSEEvent("token", "not-json")).toBeNull();
  });
});
