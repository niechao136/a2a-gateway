"""连接器数据模型注册与迁移文件存在性。"""

from pathlib import Path

from a2a_gateway.models import Base, ConnectorPlatform


def test_connector_tables_registered():
    assert "chat_connectors" in Base.metadata.tables
    assert "chat_connector_conversations" in Base.metadata.tables


def test_platform_enum_uses_member_values():
    assert [p.value for p in ConnectorPlatform] == ["feishu", "telegram", "slack"]


def test_migration_file_exists():
    path = Path("alembic/versions/0012_chat_connectors.py")
    assert path.exists()
