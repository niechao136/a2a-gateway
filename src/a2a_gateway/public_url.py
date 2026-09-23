"""对外可达地址推导（入口自适应）。

直连（``Host`` 自带端口）与域名反代（``X-Forwarded-*``）都能得到正确地址，
供 Agent Card 回连地址与连接器 Webhook 地址共用。
"""

from fastapi import Request

_DEFAULT_PORTS = {"http": "80", "https": "443"}


def first_header_value(value: str | None) -> str:
    """取请求头首个值（多级代理会把 X-Forwarded-* 追加成逗号列表）。"""
    return value.split(",")[0].strip() if value else ""


def split_host_port(host: str) -> tuple[str, str]:
    """拆分 ``host[:port]``，兼容 IPv6 字面量 ``[::1]:8000``；无端口返回空串。"""
    if host.startswith("["):
        end = host.find("]")
        if end != -1:
            rest = host[end + 1 :]
            return host[: end + 1], rest[1:] if rest.startswith(":") else ""
        return host, ""
    name, sep, port = host.partition(":")
    return (name, port) if sep else (host, "")


def public_base_url(request: Request) -> str:
    """从请求头推导对外可达的网关基础地址（支持经 nginx 反代）。

    - 协议：``X-Forwarded-Proto`` 优先（多级代理取首个值），否则用请求实际协议；
    - 主机：``X-Forwarded-Host`` 优先，否则用 ``Host``；同样取首个值；
    - 端口：主机自带端口时原样保留；主机不带端口且 ``X-Forwarded-Port``
      不是该协议的默认端口（http/80、https/443）时补上。
    """
    proto = first_header_value(request.headers.get("x-forwarded-proto")) or request.url.scheme
    host = first_header_value(request.headers.get("x-forwarded-host")) or first_header_value(
        request.headers.get("host")
    )
    hostname, port = split_host_port(host)
    if not port:
        forwarded_port = first_header_value(request.headers.get("x-forwarded-port"))
        if forwarded_port and forwarded_port != _DEFAULT_PORTS.get(proto):
            port = forwarded_port
    authority = f"{hostname}:{port}" if port else hostname
    return f"{proto}://{authority}".rstrip("/")
