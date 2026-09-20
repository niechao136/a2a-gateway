import { describe, expect, it } from "vitest";
import {
  estimateResidentBytes,
  estimateResidentChars,
  formatBytes,
  totalSkillBytes,
} from "./skillUtils";

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

describe("estimateResidentBytes", () => {
  it("中文正文按 UTF-8 字节计（字符数的 3 倍）", () => {
    // 后端按 len(content.encode("utf-8")) 计量，字符口径会低估到 1/3
    expect(estimateResidentChars([always])).toBe(4);
    expect(estimateResidentBytes([always])).toBe(12);
  });

  it("ASCII 正文字节数等于字符数", () => {
    const ascii = { name: "c", load_mode: "always", content: "abcd" };
    expect(estimateResidentBytes([ascii])).toBe(4);
  });

  it("仅统计 always 技能", () => {
    expect(estimateResidentBytes([always, onDemand])).toBe(12);
    expect(estimateResidentBytes([onDemand])).toBe(0);
  });

  it("正文缺失与空数组均为 0", () => {
    const empty = { name: "d", load_mode: "always" };
    expect(estimateResidentBytes([empty])).toBe(0);
    expect(estimateResidentBytes([])).toBe(0);
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
