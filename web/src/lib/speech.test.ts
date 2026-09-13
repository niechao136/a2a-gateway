import { describe, expect, it } from "vitest";
import { splitTtsText } from "./speech";

describe("splitTtsText", () => {
  it("短句合并为一段", () => {
    expect(splitTtsText("你好。今天天气不错！我们去公园散步吧。")).toEqual([
      "你好。今天天气不错！我们去公园散步吧。",
    ]);
  });

  it("超过上限时按句切分", () => {
    const parts = splitTtsText("第一句。".repeat(30), 30);
    expect(parts.length).toBeGreaterThan(1);
    for (const p of parts) expect(p.length).toBeLessThanOrEqual(30);
    expect(parts.join("")).toBe("第一句。".repeat(30));
  });

  it("无标点长文本按长度切分", () => {
    const text = "这是一段没有任何标点符号的很长文本需要按照长度上限切分成多个片段".repeat(3);
    const parts = splitTtsText(text, 30);
    expect(parts.length).toBeGreaterThan(1);
    for (const p of parts) expect(p.length).toBeLessThanOrEqual(30);
    expect(parts.join("")).toBe(text);
  });

  it("超长单句按句内标点二次切分", () => {
    const sentence =
      "第三句话超级无敌长，里面包含了很多逗号，以及顿号、冒号：还有破折号——所以需要二次切分处理一下！";
    const parts = splitTtsText(sentence, 20);
    expect(parts.length).toBeGreaterThan(1);
    for (const p of parts) expect(p.length).toBeLessThanOrEqual(20);
    expect(parts.join("")).toBe(sentence);
  });

  it("空文本返回空数组", () => {
    expect(splitTtsText("")).toEqual([]);
    expect(splitTtsText("  \n ")).toEqual([]);
  });
});
