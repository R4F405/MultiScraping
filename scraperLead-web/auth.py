import ipaddress

import bcrypt
from starlette.requests import Request


def parse_users(env_val: str) -> dict[str, str]:
    users: dict[str, str] = {}
    for entry in env_val.split(","):
        entry = entry.strip()
        if ":" in entry:
            username, _, password_hash = entry.partition(":")
            users[username.strip()] = password_hash.strip()
    return users


def parse_ip_whitelist(env_val: str) -> list:
    networks = []
    for entry in env_val.split(","):
        entry = entry.strip()
        if entry:
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                pass
    return networks


def is_ip_allowed(ip: str, whitelist: list) -> bool:
    if not whitelist:
        return True
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in whitelist)
    except ValueError:
        return False


def verify_password(plain: str, stored: str) -> bool:
    if stored.startswith("$2b$") or stored.startswith("$2a$"):
        return bcrypt.checkpw(plain.encode(), stored.encode())
    return plain == stored


def get_current_user(request: Request) -> str | None:
    return request.session.get("user")
