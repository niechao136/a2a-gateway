import { describe, expect, it } from "vitest";
import { base64ToUtf8, bytesToBase64, isSkillMdFileName, utf8ToBase64 } from "./skillUtils";

describe("isSkillMdFileName", () => {
  it("接受 .md / .markdown（大小写不敏感）", () => {
    expect(isSkillMdFileName("SKILL.md")).toBe(true);
    expect(isSkillMdFileName("skill.Markdown")).toBe(true);
  });
  it("拒绝其它扩展名与无扩展名", () => {
    expect(isSkillMdFileName("SKILL.txt")).toBe(false);
    expect(isSkillMdFileName("SKILL")).toBe(false);
    expect(isSkillMdFileName("")).toBe(false);
  });
});

describe("base64 ↔ utf-8", () => {
  it("utf8ToBase64 与 bytesToBase64 对 UTF-8 内容等价", () => {
    expect(utf8ToBase64("hi")).toBe("aGk=");
    expect(utf8ToBase64("中文注释")).toBe(bytesToBase64(new TextEncoder().encode("中文注释")));
  });
  it("base64ToUtf8 正确解码（含中文）", () => {
    expect(base64ToUtf8("aGk=")).toBe("hi");
    const text = "# -*- coding: utf-8 -*- 中文注释";
    expect(base64ToUtf8(utf8ToBase64(text))).toBe(text);
  });
  it("超过分片阈值（8192 字节）的脚本 round-trip 无损", () => {
    const text = "x".repeat(20000) + "中文尾部";
    expect(base64ToUtf8(utf8ToBase64(text))).toBe(text);
  });
});
