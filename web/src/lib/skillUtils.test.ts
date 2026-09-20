import { describe, expect, it } from "vitest";
import { estimateResidentChars, formatBytes, totalSkillBytes } from "./skillUtils";

const always = { name: "a", load_mode: "always", content: "常驻正文", size_bytes: 12 };
const onDemand = { name: "b", load_mode: "on_demand", content: "按需正文", size_bytes: 12 };

describe("estimateResidentChars", () => {
  it("仅统计 always 技能正文", () => {
    expect(estimateResidentChars([always, onDemand])).toBeGreaterThan(0);
    expect(estimateResidentChars([onDemand])).toBe(0);
  });

  it("空数组为 0", () => {
    expect(estimateResidentChars([])).toBe(0);
  });
});

describe("totalSkillBytes", () => {
  it("正文 + 附件求和", () => {
    const skill = { ...always, files: [{ size: 10 }, { size: 5 }] };
    expect(totalSkillBytes(skill)).toBe(12 + 15);
  });
});

describe("formatBytes", () => {
  it("分级格式化", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1024 * 1024)).toBe("1.0 MB");
  });
});
