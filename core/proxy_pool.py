"""HTTP CONNECT proxy configuration for the standalone registrar."""

from pathlib import Path
from urllib.parse import unquote, urlsplit


def normalize_proxy(raw: str) -> str:
    raw = raw.strip()
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port:
            raise ValueError
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError
    except ValueError:
        raise ValueError("Invalid proxy: use http[s]://[user:password@]host:port") from None
    return raw.rstrip("/")


def playwright_proxy(proxy: str) -> dict[str, str]:
    parsed = urlsplit(normalize_proxy(proxy))
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    result = {"server": f"{parsed.scheme}://{host}:{parsed.port}"}
    if parsed.username is not None:
        result["username"] = unquote(parsed.username)
    if parsed.password is not None:
        result["password"] = unquote(parsed.password)
    return result


def redact_proxy(proxy: str) -> str:
    return playwright_proxy(proxy)["server"]


def load_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    proxies = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            proxies.append(normalize_proxy(line))
        except ValueError:
            raise ValueError(f"Invalid proxy on line {number} of {path.name}") from None
    return list(dict.fromkeys(proxies))
