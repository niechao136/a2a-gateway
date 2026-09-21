"""沙箱服务测试公共夹具：临时 socket 路径（零外部依赖）。"""

import pytest


@pytest.fixture
def socket_path(tmp_path):
    return str(tmp_path / "sandbox.sock")
