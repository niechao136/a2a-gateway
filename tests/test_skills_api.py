"""Skill 管理 API 测试（repository / 导入模块 monkeypatch，不连库不发网）。"""

import base64
import io
import types
import zipfile
from datetime import datetime, timezone

from a2a_gateway.routes import registry as registry_mod

_NOW = datetime.now(timezone.utc)

SKILL_MD = "---\nname: demo-skill\ndescription: 演示\n---\n正文"
OTHER_MD = "---\nname: other-skill\ndescription: 另一个\n---\n正文"


def _skill(**kw):
    base = {
        "id": 1, "name": "demo-skill", "description": "演示", "content": "正文",
        "frontmatter": {}, "files": [], "load_mode": "on_demand",
        "size_bytes": 6, "file_count": 0, "source": "text", "source_ref": "",
        "review_status": "pending", "review_note": "", "reviewed_at": None,
        "enabled": True, "created_at": _NOW, "updated_at": _NOW,
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def _zip_b64(entries: dict[str, str]) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, text in entries.items():
            zf.writestr(path, text)
    return base64.b64encode(buf.getvalue()).decode()


def _skill_zip_b64(skill_md: str = SKILL_MD) -> str:
    return _zip_b64({"demo-skill/SKILL.md": skill_md})


def _agent(agent_id: int = 7) -> types.SimpleNamespace:
    return types.SimpleNamespace(id=agent_id, name="引用方 Agent")


async def test_skills_require_auth(anon_client):
    assert (await anon_client.get("/api/admin/skills")).status_code == 401


async def test_list_skills(auth_client, monkeypatch):
    async def fake_list(session):
        return [_skill()]

    monkeypatch.setattr(registry_mod.repo, "list_skills", fake_list)
    resp = await auth_client.get("/api/admin/skills")
    assert resp.status_code == 200
    assert resp.json()[0]["name"] == "demo-skill"


async def test_create_skill_rejects_bad_name(auth_client):
    resp = await auth_client.post(
        "/api/admin/skills",
        json={"name": "中文名", "description": "d", "content": "c"},
    )
    assert resp.status_code == 400


async def test_create_skill_rejects_duplicate(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return _skill()

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    resp = await auth_client.post(
        "/api/admin/skills", json={"name": "demo-skill", "description": "d", "content": "c"}
    )
    assert resp.status_code == 409


async def test_create_skill_returns_201(auth_client, monkeypatch):
    captured = {}

    async def fake_by_name(session, name):
        return None

    async def fake_create(session, **kwargs):
        captured.update(kwargs)
        return _skill(name=kwargs["name"])

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    monkeypatch.setattr(registry_mod.repo, "create_skill", fake_create)
    resp = await auth_client.post(
        "/api/admin/skills",
        json={"name": " new-skill ", "description": "d", "content": "c"},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "new-skill"
    assert captured["name"] == "new-skill"  # 落库前 strip
    assert captured["source"] == "manual"


async def test_update_skill_rejects_empty_description(auth_client, monkeypatch):
    async def fake_get(session, skill_id):
        return _skill()

    monkeypatch.setattr(registry_mod.repo, "get_skill", fake_get)
    resp = await auth_client.put("/api/admin/skills/1", json={"description": "   "})
    assert resp.status_code == 400


async def test_update_skill_returns_404_when_missing(auth_client, monkeypatch):
    async def fake_get(session, skill_id):
        return None

    monkeypatch.setattr(registry_mod.repo, "get_skill", fake_get)
    resp = await auth_client.put("/api/admin/skills/9", json={"description": "新说明"})
    assert resp.status_code == 404


async def test_update_skill_refreshes_snapshot_and_invalidates_graph(auth_client, monkeypatch):
    refresh_calls = []
    invalidated = []

    async def fake_get(session, skill_id):
        return _skill()

    async def fake_using(session, skill_id):
        return [_agent()]

    async def fake_update(session, skill, data):
        return _skill(description=data.description or skill.description)

    async def fake_refresh(session, ids):
        refresh_calls.append(list(ids))
        return 1

    async def fake_invalidate(agent_id):
        invalidated.append(agent_id)

    monkeypatch.setattr(registry_mod.repo, "get_skill", fake_get)
    monkeypatch.setattr(registry_mod.repo, "agents_using_skill", fake_using)
    monkeypatch.setattr(registry_mod.repo, "update_skill", fake_update)
    monkeypatch.setattr(registry_mod.repo, "refresh_agents_for_skills", fake_refresh)
    monkeypatch.setattr(registry_mod, "invalidate_agent", fake_invalidate)
    resp = await auth_client.put("/api/admin/skills/1", json={"description": "新说明"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "新说明"
    assert refresh_calls == [[1]]
    assert invalidated == [7]


async def test_import_preview_from_text(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return None

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    resp = await auth_client.post(
        "/api/admin/skills/import/preview", json={"source": "text", "skill_md": SKILL_MD}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["name"] == "demo-skill"
    assert body["items"][0]["conflict"] is False


async def test_import_preview_flags_conflict(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return _skill()

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    resp = await auth_client.post(
        "/api/admin/skills/import/preview", json={"source": "text", "skill_md": SKILL_MD}
    )
    assert resp.json()["items"][0]["conflict"] is True


async def test_import_preview_from_zip(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return None

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    resp = await auth_client.post(
        "/api/admin/skills/import/preview",
        json={"source": "zip", "zip_b64": _skill_zip_b64()},
    )
    assert resp.status_code == 200
    assert resp.json()["items"][0]["name"] == "demo-skill"


async def test_import_preview_surfaces_scripts(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return None

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    resp = await auth_client.post(
        "/api/admin/skills/import/preview",
        json={
            "source": "zip",
            "zip_b64": _zip_b64(
                {
                    "demo-skill/SKILL.md": SKILL_MD,
                    "demo-skill/scripts/gen.py": "print('hi')",
                    "demo-skill/notes.md": "文本",
                }
            ),
        },
    )
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["scripts"] == ["scripts/gen.py"]
    assert item["files"] == ["notes.md"]
    assert item["file_count"] == 2


async def test_import_preview_from_dir(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return None

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    resp = await auth_client.post(
        "/api/admin/skills/import/preview",
        json={
            "source": "dir",
            "dir_files": [{"path": "demo-skill/SKILL.md", "content": SKILL_MD}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["name"] == "demo-skill"
    assert body["items"][0]["conflict"] is False


async def test_import_preview_over_limit_flag(auth_client, monkeypatch):
    """over_limit = 是否存在解析失败条目（来源级 errors 或条目级 error）。"""

    async def fake_by_name(session, name):
        return None

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    ok = await auth_client.post(
        "/api/admin/skills/import/preview", json={"source": "text", "skill_md": SKILL_MD}
    )
    assert ok.json()["over_limit"] is False
    bad = await auth_client.post(
        "/api/admin/skills/import/preview",
        json={"source": "text", "skill_md": "缺少 frontmatter 的正文"},
    )
    assert bad.json()["over_limit"] is True


async def test_import_preview_reports_parse_error_not_500(auth_client, monkeypatch):
    """zip 内 SKILL.md 不合法：SkillParseError 必须被收口成来源级 error，不能逃逸成 500。"""
    resp = await auth_client.post(
        "/api/admin/skills/import/preview",
        json={"source": "zip", "zip_b64": _skill_zip_b64("缺少 frontmatter 的正文")},
    )
    assert resp.status_code == 200
    assert "frontmatter" in resp.json()["errors"][0]


async def test_import_commit_resets_review_via_overwrite(auth_client, monkeypatch):
    captured = {}
    refresh_calls = []
    invalidated = []

    async def fake_by_name(session, name):
        return _skill()  # 重名

    async def fake_upsert(session, **kwargs):
        captured.update(kwargs)
        return _skill(), False

    async def fake_refresh(session, ids):
        refresh_calls.append(list(ids))
        return 0

    async def fake_using(session, skill_id):
        return [_agent()]

    async def fake_invalidate(agent_id):
        invalidated.append(agent_id)

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    monkeypatch.setattr(registry_mod.repo, "upsert_imported_skill", fake_upsert)
    monkeypatch.setattr(registry_mod.repo, "refresh_agents_for_skills", fake_refresh)
    monkeypatch.setattr(registry_mod.repo, "agents_using_skill", fake_using)
    monkeypatch.setattr(registry_mod, "invalidate_agent", fake_invalidate)
    resp = await auth_client.post(
        "/api/admin/skills/import/commit",
        json={"source": "text", "skill_md": SKILL_MD, "overwrite": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 1
    assert body["skipped"] == 0
    assert captured["overwrite"] is True
    # 覆盖写库 → 必须刷新引用方快照并失效图缓存（把 registry.py 的 refresh/invalidate 删掉即红）
    assert refresh_calls == [[1]]
    assert invalidated == [7]


async def test_import_commit_without_overwrite_skips_conflict(auth_client, monkeypatch):
    """overwrite=false + 重名：repository 是「跳过」，不能报 updated，更不能打掉图缓存。"""
    upsert_calls = []
    refresh_calls = []
    invalidated = []

    async def fake_by_name(session, name):
        return _skill()  # 重名 → 预览 conflict=True

    async def fake_upsert(session, **kwargs):
        upsert_calls.append(kwargs)
        return _skill(), False  # repository 未写库

    async def fake_refresh(session, ids):
        refresh_calls.append(list(ids))
        return 0

    async def fake_using(session, skill_id):
        return [_agent()]

    async def fake_invalidate(agent_id):
        invalidated.append(agent_id)

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    monkeypatch.setattr(registry_mod.repo, "upsert_imported_skill", fake_upsert)
    monkeypatch.setattr(registry_mod.repo, "refresh_agents_for_skills", fake_refresh)
    monkeypatch.setattr(registry_mod.repo, "agents_using_skill", fake_using)
    monkeypatch.setattr(registry_mod, "invalidate_agent", fake_invalidate)
    resp = await auth_client.post(
        "/api/admin/skills/import/commit",
        json={"source": "text", "skill_md": SKILL_MD},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 0
    assert body["updated"] == 0
    assert body["skipped"] == 1
    assert len(upsert_calls) == 1  # upsert 仍照常调用，repository 内部是 no-op
    assert upsert_calls[0]["overwrite"] is False
    assert refresh_calls == []
    assert invalidated == []


async def test_import_commit_honours_names_filter(auth_client, monkeypatch):
    """多 skill 包只落库勾选的那一条。"""
    upserted = []

    async def fake_by_name(session, name):
        return None

    async def fake_upsert(session, **kwargs):
        upserted.append(str(kwargs["parsed"]["name"]))
        return _skill(name=str(kwargs["parsed"]["name"])), True

    async def fake_refresh(session, ids):
        return 0

    async def fake_using(session, skill_id):
        return []

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    monkeypatch.setattr(registry_mod.repo, "upsert_imported_skill", fake_upsert)
    monkeypatch.setattr(registry_mod.repo, "refresh_agents_for_skills", fake_refresh)
    monkeypatch.setattr(registry_mod.repo, "agents_using_skill", fake_using)
    resp = await auth_client.post(
        "/api/admin/skills/import/commit",
        json={
            "source": "zip",
            "zip_b64": _zip_b64(
                {"demo-skill/SKILL.md": SKILL_MD, "other-skill/SKILL.md": OTHER_MD}
            ),
            "names": ["other-skill"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 1
    assert body["updated"] == 0
    assert upserted == ["other-skill"]


async def test_import_commit_skips_names_outside_preview(auth_client, monkeypatch):
    """名单里的名字预览里没有（含解析失败条目）：计 skipped，不能静默丢弃。"""
    upsert_calls = []

    async def fake_by_name(session, name):
        raise ValueError(f"查重失败：{name}")

    async def fake_upsert(session, **kwargs):
        upsert_calls.append(kwargs)
        return _skill(), True

    async def fake_refresh(session, ids):
        return 0

    async def fake_using(session, skill_id):
        return []

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    monkeypatch.setattr(registry_mod.repo, "upsert_imported_skill", fake_upsert)
    monkeypatch.setattr(registry_mod.repo, "refresh_agents_for_skills", fake_refresh)
    monkeypatch.setattr(registry_mod.repo, "agents_using_skill", fake_using)
    resp = await auth_client.post(
        "/api/admin/skills/import/commit",
        json={"source": "text", "skill_md": SKILL_MD, "names": ["demo-skill"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    item = body["items"][0]
    assert item["error"] is not None
    assert item["name"] == "demo-skill"  # 失败条目要带上名字，前端才能对账
    assert body["created"] == 0
    assert body["updated"] == 0
    assert body["skipped"] == 1
    assert upsert_calls == []


async def test_import_commit_fetches_url_once(auth_client, monkeypatch):
    """URL 来源：预览与落库共用同一次抓取（不能抓两遍，也不能中途换内容）。"""
    fetched = []

    async def fake_fetch(url, *, transport=None):
        fetched.append(url)
        return SKILL_MD.encode("utf-8")

    async def fake_by_name(session, name):
        return None

    async def fake_upsert(session, **kwargs):
        return _skill(), True

    async def fake_refresh(session, ids):
        return 0

    async def fake_using(session, skill_id):
        return []

    monkeypatch.setattr(registry_mod.si, "fetch_url", fake_fetch)
    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    monkeypatch.setattr(registry_mod.repo, "upsert_imported_skill", fake_upsert)
    monkeypatch.setattr(registry_mod.repo, "refresh_agents_for_skills", fake_refresh)
    monkeypatch.setattr(registry_mod.repo, "agents_using_skill", fake_using)
    resp = await auth_client.post(
        "/api/admin/skills/import/commit",
        json={"source": "url", "url": "http://x/SKILL.md"},
    )
    assert resp.status_code == 200
    assert fetched == ["http://x/SKILL.md"]
    assert resp.json()["created"] == 1


async def test_import_url_failure_reported_per_item(auth_client, monkeypatch):
    from a2a_gateway.skill_import import SkillImportError

    async def fake_fetch(url, *, transport=None):
        raise SkillImportError("URL 抓取失败：ConnectError")

    monkeypatch.setattr(registry_mod.si, "fetch_url", fake_fetch)
    resp = await auth_client.post(
        "/api/admin/skills/import/preview", json={"source": "url", "url": "http://x/SKILL.md"}
    )
    assert resp.status_code == 200
    assert "URL 抓取失败" in resp.json()["errors"][0]


async def test_review_endpoint_updates_status(auth_client, monkeypatch):
    refresh_calls = []
    invalidated = []

    async def fake_get(session, skill_id):
        return _skill(review_status="pending")

    async def fake_set_review(session, skill, review_status, note):
        return _skill(review_status=review_status, review_note=note, reviewed_at=_NOW)

    async def fake_refresh(session, ids):
        refresh_calls.append(list(ids))
        return 1

    async def fake_using(session, skill_id):
        return [_agent()]

    async def fake_invalidate(agent_id):
        invalidated.append(agent_id)

    monkeypatch.setattr(registry_mod.repo, "get_skill", fake_get)
    monkeypatch.setattr(registry_mod.repo, "set_skill_review", fake_set_review)
    monkeypatch.setattr(registry_mod.repo, "refresh_agents_for_skills", fake_refresh)
    monkeypatch.setattr(registry_mod.repo, "agents_using_skill", fake_using)
    monkeypatch.setattr(registry_mod, "invalidate_agent", fake_invalidate)
    resp = await auth_client.post(
        "/api/admin/skills/1/review", json={"status": "approved", "note": "ok"}
    )
    assert resp.status_code == 200
    assert resp.json()["review_status"] == "approved"
    # 审核态变了 → 引用方快照必须刷新、已编译图必须失效
    assert refresh_calls == [[1]]
    assert invalidated == [7]


async def test_delete_skill_requires_force_when_referenced(auth_client, monkeypatch):
    async def fake_get(session, skill_id):
        return _skill()

    async def fake_using(session, skill_id):
        return [types.SimpleNamespace(id=7, name="测试 Agent")]

    async def fake_detach(session, skill_id):
        return 1

    async def fake_delete(session, skill):
        return None

    monkeypatch.setattr(registry_mod.repo, "get_skill", fake_get)
    monkeypatch.setattr(registry_mod.repo, "agents_using_skill", fake_using)
    monkeypatch.setattr(registry_mod.repo, "detach_skill_from_agents", fake_detach)
    monkeypatch.setattr(registry_mod.repo, "delete_skill_record", fake_delete)
    assert (await auth_client.delete("/api/admin/skills/1")).status_code == 409
    assert (await auth_client.delete("/api/admin/skills/1?force=true")).status_code == 204
