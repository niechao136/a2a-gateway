import { describe, expect, it } from "vitest";
import { isSkillMdFileName } from "./skillUtils";

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
