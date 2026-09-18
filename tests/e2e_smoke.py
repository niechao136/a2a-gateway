"""端到端冒烟测试：对「已部署实例」验证 管理中心 → 自定义 Agent → A2A 全链路。

与 tests/ 下的单元测试不同，本脚本需要真实运行的实例（真实 LLM + 真实 A2A 目标），
因此文件名不以 `test_` 开头，不会被 pytest 收集。

流程：
  1. 管理员登录
  2. 读取默认 Agent 的 A2A 目标（复用，保证验证的是真实 A2A 链路）
  3. 创建（或复用）测试 Agent
  4. 发布该 Agent
  5. 通过 /api/chat/{slug} 对话，强制模型调用 a2a_call，校验 SSE 事件
  6. 清理：下线并删除测试 Agent（--keep 可保留）

用法（推荐在 backend 容器内执行，管理员账号从容器环境变量读取）：
    python e2e_smoke.py
    python e2e_smoke.py --base-url http://localhost:10099 --slug e2e-hermes --keep

退出码：0 = 成功；1 = 失败（可用于发版后校验 / CI）
"""

import argparse
import json
import os
import re
import sys

import httpx

DEFAULT_SLUG = "e2e-hermes"
READ_TIMEOUT = 180.0


def sse_events(text: str) -> list[tuple[str, dict]]:
    """把 SSE 文本解析为 [(event_name, data_dict)]。

    注意：sse-starlette 使用 CRLF（\\r\\n）分隔，必须兼容 \\n 与 \\r\\n。
    """
    events: list[tuple[str, dict]] = []
    for block in re.split(r"\r?\n\r?\n", text):
        name = None
        data_lines: list[str] = []
        for line in re.split(r"\r?\n", block):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not name or not data_lines:
            continue
        payload = "\n".join(data_lines)
        try:
            events.append((name, json.loads(payload)))
        except json.JSONDecodeError:
            events.append((name, {"raw": payload}))
    return events


def _require(condition: bool, message: str) -> None:
    if not condition:
        print(f"❌ {message}")
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="a2a-gateway 端到端冒烟测试")
    parser.add_argument("--base-url", default=os.getenv("E2E_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--username", default=os.getenv("ADMIN_USERNAME", "admin"))
    parser.add_argument("--password", default=os.getenv("ADMIN_PASSWORD", ""))
    parser.add_argument("--slug", default=DEFAULT_SLUG)
    parser.add_argument("--keep", action="store_true", help="保留测试 Agent，不做清理")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    timeout = httpx.Timeout(30.0, read=READ_TIMEOUT)

    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        # 1) 登录
        resp = client.post(
            "/api/admin/login",
            json={"username": args.username, "password": args.password},
        )
        _require(resp.status_code == 200, f"管理员登录失败（HTTP {resp.status_code}）：{resp.text[:200]}")
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        print(f"[1/6] 登录成功：{base_url}")

        # 2) 复用默认 Agent 的 A2A 目标
        resp = client.get("/api/admin/agents", headers=headers)
        _require(resp.status_code == 200, f"获取 Agent 列表失败（HTTP {resp.status_code}）")
        agents = resp.json()
        default_agent = next((a for a in agents if a["slug"] == "/"), None)
        _require(default_agent is not None, "未找到默认 Agent（slug=/）")
        assert default_agent is not None  # 收窄类型（_require 不具备类型守卫语义）
        targets = default_agent.get("a2a_targets") or []
        _require(bool(targets), "默认 Agent 未绑定 A2A 目标，无法验证 A2A 链路")
        print(f"[2/6] 复用默认 Agent 的 A2A 目标：{targets[0].get('url')}")

        # 3) 创建或复用测试 Agent
        existing = next((a for a in agents if a["slug"] == args.slug), None)
        if existing:
            agent_id = existing["id"]
            print(f"[3/6] 复用已存在的测试 Agent：{args.slug} (id={agent_id})")
        else:
            resp = client.post(
                "/api/admin/agents",
                headers=headers,
                json={
                    "slug": args.slug,
                    "name": "E2E Hermes 测试 Agent",
                    "description": "由 e2e_smoke.py 自动创建",
                    "a2a_targets": targets,
                    "enabled_tools": [],
                },
            )
            _require(
                resp.status_code == 201,
                f"创建测试 Agent 失败（HTTP {resp.status_code}）：{resp.text[:200]}",
            )
            agent_id = resp.json()["id"]
            print(f"[3/6] 创建测试 Agent：{args.slug} (id={agent_id})")

        # 4) 发布
        resp = client.post(f"/api/admin/agents/{agent_id}/publish", headers=headers)
        _require(resp.status_code == 200, f"发布失败（HTTP {resp.status_code}）：{resp.text[:200]}")
        print("[4/6] 已发布")

        # 5) 通过自定义路由对话（强制调用 a2a_call；模型偶发不调工具，最多重试 2 次）
        message = (
            "You must call the a2a_call tool right now, passing exactly this text: hello. "
            "Then report the raw tool output verbatim."
        )
        events: list[tuple[str, dict]] = []
        for attempt in range(1, 3):
            resp = client.post(
                f"/api/chat/{args.slug}",
                json={"message": message, "thread_id": f"e2e-{os.urandom(4).hex()}"},
            )
            _require(resp.status_code == 200, f"对话失败（HTTP {resp.status_code}）：{resp.text[:200]}")

            events = sse_events(resp.text)
            kinds = [name for name, _ in events]
            print(f"[5/6] 第 {attempt} 次 SSE 事件集合：{sorted(set(kinds))}")
            if "error" not in kinds and "tool_start" in kinds:
                break
            if attempt == 1:
                print("[5/6] 未触发 a2a_call，重试一次...")

        kinds = [name for name, _ in events]
        _require("error" not in kinds, f"对话返回 error 事件：{events}")
        _require("tool_start" in kinds, "未触发 a2a_call（模型未调用工具）")
        _require("tool_end" in kinds, "缺少 tool_end 事件")

        tool_output = str(next(data for name, data in events if name == "tool_end").get("output", ""))
        _require("A2A 调用失败" not in tool_output, f"A2A 调用失败：{tool_output[:300]}")
        _require("token" in kinds, "未收到 token 事件（流式输出缺失）")
        _require("done" in kinds, "未收到 done 事件")
        print(f"[5/6] a2a_call 返回：{tool_output[:160]}")

        # 6) 清理
        if args.keep:
            print("[6/6] --keep 指定，保留测试 Agent")
        else:
            client.post(f"/api/admin/agents/{agent_id}/unpublish", headers=headers)
            resp = client.delete(f"/api/admin/agents/{agent_id}", headers=headers)
            _require(resp.status_code in (200, 204), f"清理失败（HTTP {resp.status_code}）")
            print("[6/6] 已清理测试 Agent")

    print("\n✅ E2E 冒烟通过：管理中心 → 自定义 Agent → 发布 → 自定义路由 → A2A")
    return 0


if __name__ == "__main__":
    sys.exit(main())
