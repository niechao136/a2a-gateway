import { describe, expect, it } from "vitest";
import {
  attachmentFromBytes,
  validateAttachment,
  validateSkillEditForm,
  type SkillFilePayload,
} from "./skillForm";

describe("attachmentFromBytes", () => {
  it("脚本后缀 → entry_type=script + base64 编码", () => {
    const payload = attachmentFromBytes("scripts/gen.py", new Uint8Array([104, 105]));
    expect(payload.entry_type).toBe("script");
    expect(payload.encoding).toBe("base64");
    expect(payload.content).toBe("aGk="); // "hi"
    expect(payload.path).toBe("scripts/gen.py");
  });
  it("非脚本 → entry_type=text + utf-8 原文", () => {
    const payload = attachmentFromBytes("refs/a.md", new TextEncoder().encode("正文"));
    expect(payload.entry_type).toBe("text");
    expect(payload.encoding).toBe("utf-8");
    expect(payload.content).toBe("正文");
  });
});

describe("validateAttachment", () => {
  const base: SkillFilePayload = { path: "a.md", content: "x" };

  it("拒绝空路径与路径穿越", () => {
    expect(validateAttachment({ ...base, path: "" })).toContain("路径");
    expect(validateAttachment({ ...base, path: "../x.md" })).toContain("路径");
  });
  it("拒绝脚本 entry_type 但后缀不在白名单", () => {
    const message = validateAttachment({
      path: "bin/run.exe",
      content: "aGk=",
      entry_type: "script",
      encoding: "base64",
    });
    expect(message).toContain("后缀");
  });
  it("拒绝超限脚本（256KB）", () => {
    const message = validateAttachment({
      path: "big.py",
      content: "aGk=",
      entry_type: "script",
      encoding: "base64",
    });
    expect(message).toBeNull(); // "aGk=" 解码后 2 字节，合法
    const huge = attachmentFromBytes("big.py", new Uint8Array(256 * 1024 + 1));
    expect(validateAttachment(huge)).toContain("256KB");
  });
  it("合法条目返回 null", () => {
    expect(validateAttachment(base)).toBeNull();
    expect(
      validateAttachment({ path: "s.py", content: "aGk=", entry_type: "script", encoding: "base64" }),
    ).toBeNull();
  });
});

describe("validateSkillEditForm", () => {
  it("description 必填", () => {
    const errors = validateSkillEditForm({ description: "   " });
    expect(errors.description).toBeTruthy();
    expect(validateSkillEditForm({ description: "说明" }).description).toBeUndefined();
  });
});
