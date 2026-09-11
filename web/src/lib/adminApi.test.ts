/**
 * JWT payload 解析与登录态判定测试（纯函数，不依赖 localStorage / 网络）。
 *
 * 背景：对话页需要根据本地 token 决定显示「登录」还是「管理中心」入口，
 * 因此必须能在不请求后端的前提下解析 payload 并判断是否过期。
 */

import { describe, expect, it } from "vitest";
import { decodeJwtPayload, isJwtExpired } from "./adminApi";

/** 按 UTF-8 编码为 base64url（与 JWT 规范一致）。 */
function toBase64Url(input: string): string {
  const bytes = new TextEncoder().encode(input);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** 拼一个结构合法的 JWT（签名段随意 —— 解析 payload 不校验签名）。 */
function makeToken(payload: Record<string, unknown>): string {
  return `${toBase64Url('{"alg":"HS256","typ":"JWT"}')}.${toBase64Url(JSON.stringify(payload))}.sig`;
}

describe("decodeJwtPayload", () => {
  it("解析出 sub 与 exp", () => {
    const payload = decodeJwtPayload(makeToken({ sub: "admin", exp: 1893456000 }));

    expect(payload).toEqual({ sub: "admin", exp: 1893456000 });
  });

  it("正确解码 UTF-8 用户名（中文）", () => {
    expect(decodeJwtPayload(makeToken({ sub: "管理员" }))?.sub).toBe("管理员");
  });

  it("兼容未补齐 padding 的 base64url", () => {
    // "a" 的 base64url 为 "YQ"（无 padding），JWT 实际就是这种形式
    const token = makeToken({ sub: "a" });
    expect(token.split(".")[1]).not.toContain("=");
    expect(decodeJwtPayload(token)?.sub).toBe("a");
  });

  it("段数不为 3 时返回 null", () => {
    expect(decodeJwtPayload("only-one-part")).toBeNull();
    expect(decodeJwtPayload("a.b")).toBeNull();
    expect(decodeJwtPayload("a.b.c.d")).toBeNull();
  });

  it("payload 非法（非 JSON / 非对象）时返回 null", () => {
    expect(decodeJwtPayload(`x.${toBase64Url("not-json")}.sig`)).toBeNull();
    expect(decodeJwtPayload(`x.${toBase64Url("[1,2]")}.sig`)).toBeNull();
    expect(decodeJwtPayload(`x.${toBase64Url('"str"')}.sig`)).toBeNull();
  });

  it("空串返回 null，不抛异常", () => {
    expect(decodeJwtPayload("")).toBeNull();
  });
});

describe("isJwtExpired", () => {
  const now = 1_800_000_000_000; // 固定时间点，避免测试受当前时间影响

  it("未过期返回 false", () => {
    expect(isJwtExpired({ exp: now / 1000 + 60 }, now)).toBe(false);
  });

  it("已过期返回 true", () => {
    expect(isJwtExpired({ exp: now / 1000 - 1 }, now)).toBe(true);
  });

  it("恰好到期视为过期（边界）", () => {
    expect(isJwtExpired({ exp: now / 1000 }, now)).toBe(true);
  });

  it("无 exp 时不判定为过期（交给后端兜底 401）", () => {
    expect(isJwtExpired({ sub: "admin" }, now)).toBe(false);
    expect(isJwtExpired({ exp: "not-a-number" }, now)).toBe(false);
  });

  it("payload 无法解析时视为失效", () => {
    expect(isJwtExpired(null, now)).toBe(true);
  });
});
