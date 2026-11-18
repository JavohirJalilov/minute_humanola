"""The bot's connection to the backend: one small method per call of the backend's /internal API."""

import httpx


class BackendError(Exception):
    """The backend refused something. `message` is English and is the key of its translation."""

    def __init__(self, code: str, message: str, params: dict | None = None, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = params or {}
        self.status = status


class BackendUnavailable(Exception):
    """The backend can't be reached or failed."""


class Backend:
    def __init__(self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers={"X-Bot-Key": api_key},
                                      timeout=httpx.Timeout(15, read=40), transport=transport)

    async def close(self) -> None:
        await self.http.aclose()

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            r = await self.http.request(method, "/internal" + path, **kwargs)
        except httpx.HTTPError as e:
            raise BackendUnavailable(type(e).__name__) from e
        if r.status_code >= 500:
            raise BackendUnavailable(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail") or {}
            except ValueError:
                detail = {}
            if not isinstance(detail, dict):
                detail = {"message": str(detail)}
            raise BackendError(detail.get("code", "error"), detail.get("message", "Something went wrong"),
                               detail.get("params"), r.status_code)
        return r

    async def _json(self, method: str, path: str, **kwargs) -> dict:
        return (await self._call(method, path, **kwargs)).json()

    # ---- startup

    async def hello(self, name: str, username: str) -> dict:
        return await self._json("POST", "/hello", json={"name": name, "username": username})

    # ---- registration

    async def lookup(self, telegram_id: int, username: str | None) -> dict:
        return await self._json("POST", "/users/lookup", json={"telegram_id": telegram_id, "username": username})

    async def check_name(self, text: str) -> str:
        return (await self._json("POST", "/registration/name", json={"text": text}))["name"]

    async def check_phone(self, text: str) -> str:
        return (await self._json("POST", "/registration/phone", json={"text": text}))["phone"]

    async def register(self, **fields) -> dict:
        return await self._json("POST", "/registration", json=fields)

    async def set_language(self, telegram_id: int, language: str) -> dict:
        return await self._json("POST", "/users/language", json={"telegram_id": telegram_id, "language": language})

    # ---- transfers

    async def transfer(self, transfer_id: int, telegram_id: int) -> dict:
        return await self._json("GET", f"/transfers/{transfer_id}", params={"telegram_id": telegram_id})

    async def answer(self, transfer_id: int, telegram_id: int, accept: bool, reason: str = "") -> dict:
        return await self._json("POST", f"/transfers/{transfer_id}/answer",
                                json={"telegram_id": telegram_id, "accept": accept, "reason": reason[:500]})

    # ---- messages to deliver

    async def claim(self, limit: int = 25, wait: float = 0) -> list[dict]:
        return (await self._json("POST", "/outbox/claim", json={"limit": limit, "wait": wait}))["messages"]

    async def report(self, results: list[dict]) -> None:
        await self._json("POST", "/outbox/report", json={"results": results})

    async def document(self, outbox_id: int) -> tuple[bytes, str]:
        r = await self._call("GET", f"/outbox/{outbox_id}/document")
        return r.content, r.headers.get("X-Filename", "file")
