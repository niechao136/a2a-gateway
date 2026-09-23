import { describe, expect, it } from "vitest";
import {
  attachmentFromBytes,
  collectDirCandidates,
  normalizeAttachmentPath,
  stripTopDir,
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

describe("normalizeAttachmentPath", () => {
  it("折叠重复斜杠 / 去 ./ 前缀 / 反斜杠转正斜杠 / trim / 去尾斜杠", () => {
    expect(normalizeAttachmentPath("references//a.md")).toBe("references/a.md");
    expect(normalizeAttachmentPath("./a.md")).toBe("a.md");
    expect(normalizeAttachmentPath("././a.md")).toBe("a.md");
    expect(normalizeAttachmentPath("references\\a.md")).toBe("references/a.md");
    expect(normalizeAttachmentPath("  references/a.md  ")).toBe("references/a.md");
    expect(normalizeAttachmentPath("references/a.md/")).toBe("references/a.md");
    expect(normalizeAttachmentPath("")).toBe("");
    expect(normalizeAttachmentPath(".")).toBe("");
  });

  it("不清理会掩盖非法性的前导斜杠与 ..（留给 validateAttachment 拒绝）", () => {
    expect(normalizeAttachmentPath("/abs/a.md")).toBe("/abs/a.md");
    expect(normalizeAttachmentPath("../x.md")).toBe("../x.md");
    expect(normalizeAttachmentPath("a/../b.md")).toBe("a/../b.md");
  });
});

describe("validateAttachment 路径形态", () => {
  it("拒绝以 / 结尾的路径（后端 normpath 会吃掉尾斜杠，产生无后缀条目）", () => {
    expect(validateAttachment({ path: "references/", content: "x" })).toContain("路径");
  });
  it("拒绝 . 作为路径", () => {
    expect(validateAttachment({ path: ".", content: "x" })).toContain("路径");
  });
  it("接受嵌套路径", () => {
    expect(validateAttachment({ path: "references/a.md", content: "x" })).toBeNull();
  });
});

describe("stripTopDir", () => {
  it("剥掉选中的顶层目录名（该目录即技能包根）", () => {
    expect(stripTopDir("my-skill/references/a.md")).toBe("references/a.md");
    expect(stripTopDir("my-skill/a.md")).toBe("a.md");
    expect(stripTopDir("my-skill/a/b/c.md")).toBe("a/b/c.md");
  });
  it("没有目录层级时原样返回", () => {
    expect(stripTopDir("a.md")).toBe("a.md");
    expect(stripTopDir("./a.md")).toBe("a.md");
  });
});

describe("collectDirCandidates", () => {
  const bytes = (text: string) => new TextEncoder().encode(text);
  const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x01]);

  it("文本 → text/utf-8；脚本后缀 → script/base64", () => {
    const [md, py] = collectDirCandidates([
      { path: "references/a.md", bytes: bytes("正文") },
      { path: "scripts/run.py", bytes: bytes("print(1)") },
    ]);
    expect(md.skipReason).toBeNull();
    expect(md.payload).toEqual({
      path: "references/a.md",
      content: "正文",
      entry_type: "text",
      encoding: "utf-8",
    });
    expect(py.skipReason).toBeNull();
    expect(py.payload?.entry_type).toBe("script");
    expect(py.payload?.encoding).toBe("base64");
    expect(py.payload?.path).toBe("scripts/run.py");
  });

  it("SKILL.md 跳过（正文由上方字段单独维护）", () => {
    const [item] = collectDirCandidates([{ path: "SKILL.md", bytes: bytes("# 标题") }]);
    expect(item.skipReason).toContain("SKILL.md");
    expect(item.payload).toBeNull();
  });

  it("二进制跳过，不阻断同批其余文件", () => {
    const items = collectDirCandidates([
      { path: "assets/logo.png", bytes: png },
      { path: "references/a.md", bytes: bytes("正文") },
    ]);
    expect(items[0].skipReason).toContain("二进制");
    expect(items[1].skipReason).toBeNull();
    expect(items[1].payload).not.toBeNull();
  });

  it("超限：文本 > 1MB、脚本 > 256KB 各自跳过并给出原因", () => {
    const [bigText, bigScript] = collectDirCandidates([
      { path: "big.md", bytes: new Uint8Array(1024 * 1024 + 1) },
      { path: "big.py", bytes: new Uint8Array(256 * 1024 + 1) },
    ]);
    expect(bigText.skipReason).toContain("1MB");
    expect(bigScript.skipReason).toContain("256KB");
  });

  it("路径穿越跳过", () => {
    const [item] = collectDirCandidates([{ path: "../escape.md", bytes: bytes("x") }]);
    expect(item.skipReason).toContain("路径");
  });

  it("与现有附件重复时标记 duplicate（供 UI 默认不勾选），但仍给出 payload", () => {
    const items = collectDirCandidates(
      [
        { path: "references/a.md", bytes: bytes("新") },
        { path: "references/b.md", bytes: bytes("新") },
      ],
      ["references/a.md"],
    );
    expect(items[0].duplicate).toBe(true);
    expect(items[0].payload).not.toBeNull();
    expect(items[1].duplicate).toBe(false);
  });

  it("size 按原始字节计（不是解码后字符数）", () => {
    const [item] = collectDirCandidates([{ path: "a.md", bytes: bytes("中文") }]);
    expect(item.size).toBe(6);
  });

  it("超限文件不必读进内存（只给 size）也能给出正确原因", () => {
    const big = 2 * 1024 * 1024;
    const [md, py] = collectDirCandidates([
      { path: "big.md", size: big },
      { path: "big.py", size: big },
    ]);
    expect(md.skipReason).toContain("1MB");
    expect(py.skipReason).toContain("256KB");
  });
});
