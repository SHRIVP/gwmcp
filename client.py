from __future__ import annotations

import time
import httpx


class AuthError(Exception):
    pass


class GuidewireError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        super().__init__(f"HTTP {status}: {body[:500]}")


class GuidewireClient:
    def __init__(
        self,
        base_url: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self._token: str | None = None
        self._token_expiry: float = 0

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        payload: dict = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.scope:
            payload["scope"] = self.scope

        async with httpx.AsyncClient() as http:
            resp = await http.post(
                self.token_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )

        if resp.status_code != 200:
            raise AuthError(f"Token request failed ({resp.status_code}): {resp.text[:300]}")

        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 3600)
        return self._token

    async def search(self, path: str, params: dict) -> dict:
        token = await self._get_token()
        clean = {k: v for k, v in params.items() if v is not None}

        async with httpx.AsyncClient() as http:
            resp = await http.get(
                f"{self.base_url}/v1{path}",
                params=clean,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                timeout=30,
            )

        if resp.status_code == 401:
            self._token = None  # force re-auth on next call
            raise AuthError("Unauthorized — check credentials and token URL")
        if resp.status_code != 200:
            raise GuidewireError(resp.status_code, resp.text)

        return resp.json()
