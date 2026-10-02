# tutnext/services/gakuen/http.py
# Low-level HTTP transport layer used by GakuenAPI.
#
# Two interchangeable implementations share the same interface:
#   _AiohttpClient — server mode (aiohttp session, cookie jar, optional proxy)
#   _FetchClient   — Cloudflare Workers mode (fetch + a manual cookie jar and
#                    manual redirect handling, because Workers fetch keeps no
#                    cookies between requests)
import json
import urllib.parse
from typing import Literal, Optional, Union
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from tutnext import runtime
from tutnext.services.gakuen.errors import GakuenAPIError, GakuenNetworkError

# Mirrors aiohttp's default User-Agent so the school system sees the same client as before.
_USER_AGENT = "Python/3.12 aiohttp/3.10.0"
_MAX_REDIRECTS = 10


def _process_response(
    status: int,
    reason: str,
    html: str,
    response_type: Literal["json", "soup"],
    features: Optional[str],
    *,
    on_server_error=None,
) -> Optional[Union[BeautifulSoup, dict]]:
    """Shared post-processing of a school-system response (status + body)."""
    _error = False
    if status != 200:
        if on_server_error is not None and status >= 500:
            on_server_error(f"HTTP {status}")
        if response_type == "json":
            _error = True
        else:
            raise GakuenNetworkError(
                f"HTTPエラー: {reason}",
                error_code="HTTP_ERROR",
                status_code=status,
            )
    if response_type == "json":
        if "innerInfo" in html:
            soup = BeautifulSoup(html, "html.parser")
            if error_msg := soup.find("p", class_="innerInfo"):
                raise GakuenAPIError(
                    f"APIエラー: {error_msg.text}",
                    error_code="API_ERROR",
                )
        try:
            out_json = json.loads(urllib.parse.unquote(html).replace("　", " ").replace("+", " "))
            if _error:
                raise GakuenAPIError(
                    f"APIレスポンスが不正です: {''.join(out_json['statusDto']['messageList'])}",
                    error_code="INVALID_API_RESPONSE",
                )
        except json.JSONDecodeError as e:
            raise GakuenAPIError(
                f"JSONデコードエラー: {str(e)}",
                error_code="JSON_DECODE_ERROR",
            )
        return out_json
    return BeautifulSoup(html, features)


class _AiohttpClient:
    """HTTP 通信層 (server mode, aiohttp)"""

    def __init__(
        self,
        session,
        timeout: int,
        http_proxy: Optional[str],
    ) -> None:
        import aiohttp

        self._owns_session = session is None
        self.session = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout))
        self.http_proxy = http_proxy

    async def fetch(
        self,
        url: str,
        method: Literal["GET", "POST"] = "POST",
        data: Optional[dict] = None,
        _json: Optional[dict] = None,
        params: Optional[dict] = None,
        response_type: Literal["json", "soup"] = "soup",
        features: Optional[str] = "html.parser",
    ) -> Optional[Union[BeautifulSoup, dict]]:
        """指定されたURLからデータを取得し、BeautifulSoup と Json オブジェクトを返す"""
        import aiohttp

        try:
            async with self.session.request(
                method, url, data=data, json=_json, params=params, proxy=self.http_proxy
            ) as response:
                html = await response.text()
                return _process_response(
                    response.status,
                    response.reason or "",
                    html,
                    response_type,
                    features,
                    on_server_error=self._report_proxy_failure if self.http_proxy else None,
                )
        except aiohttp.ClientError as e:
            # 代理不可达 / 连接被拒等典型代理故障 → 上报看门狗
            if self.http_proxy:
                self._report_proxy_failure(str(e))
            raise GakuenNetworkError(
                f"ネットワークエラー: {str(e)}",
                error_code="NETWORK_ERROR",
            ) from e

    @staticmethod
    def _report_proxy_failure(reason: str) -> None:
        """通知代理看门狗一次网络失败（永不抛异常，永不阻塞调用方）。"""
        try:
            from tutnext.services.watchdog import get_watchdog

            wd = get_watchdog()
            if wd is not None:
                wd.report_failure(reason)
        except Exception:  # noqa: BLE001
            # 看门狗任何故障都不得影响 HTTP 请求路径
            pass

    async def close(self) -> None:
        """セッションを閉じる"""
        if self._owns_session and not self.session.closed:
            await self.session.close()


class _FetchClient:
    """HTTP 通信層 (Cloudflare Workers mode, fetch).

    Keeps a per-instance cookie jar (JSESSIONID etc.) and follows redirects by
    hand so cookies set during a redirect chain are carried along.
    """

    def __init__(self, session, timeout: int, http_proxy: Optional[str]) -> None:
        self._owns_session = True
        self.session = None  # backward-compatible attribute (no aiohttp session here)
        self.http_proxy = None  # Workers cannot use a LAN proxy
        self.timeout = timeout
        self._cookies: dict[str, str] = {}
        self._closed = False

    # -- cookie jar -------------------------------------------------------

    def _store_cookies(self, set_cookie_headers: list[str]) -> None:
        for header in set_cookie_headers:
            parts = [p.strip() for p in header.split(";")]
            if not parts or "=" not in parts[0]:
                continue  # e.g. the odd "HttpOnly;Secure" header the school server emits
            name, _, value = parts[0].partition("=")
            name = name.strip()
            if not name:
                continue
            expired = False
            for attr in parts[1:]:
                key, _, val = attr.partition("=")
                key = key.strip().lower()
                if key == "max-age":
                    try:
                        expired = int(val.strip()) <= 0
                    except ValueError:
                        pass
                elif key == "expires" and "1970" in val:
                    expired = True
            if expired or value == "":
                self._cookies.pop(name, None)
            else:
                self._cookies[name] = value

    def _cookie_header(self) -> Optional[str]:
        if not self._cookies:
            return None
        return "; ".join(f"{k}={v}" for k, v in self._cookies.items())

    # -- transport --------------------------------------------------------

    async def _request(self, method: str, url: str, headers: dict[str, str], body: Optional[str]):
        from tutnext.core import http as core_http

        for _ in range(_MAX_REDIRECTS + 1):
            cookie = self._cookie_header()
            if cookie:
                headers["Cookie"] = cookie
            else:
                headers.pop("Cookie", None)
            resp = await core_http.request(
                method, url, headers=headers, data=body, timeout=self.timeout, follow_redirects=False
            )
            self._store_cookies(resp.set_cookies)
            location = resp.headers.get("location")
            if resp.status in (301, 302, 303, 307, 308) and location:
                url = urljoin(url, location)
                if resp.status in (301, 302, 303):
                    method = "GET"
                    body = None
                    headers.pop("Content-Type", None)
                continue
            return resp
        raise GakuenNetworkError("リダイレクトが多すぎます", error_code="TOO_MANY_REDIRECTS")

    async def fetch(
        self,
        url: str,
        method: Literal["GET", "POST"] = "POST",
        data: Optional[dict] = None,
        _json: Optional[dict] = None,
        params: Optional[dict] = None,
        response_type: Literal["json", "soup"] = "soup",
        features: Optional[str] = "html.parser",
    ) -> Optional[Union[BeautifulSoup, dict]]:
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        headers: dict[str, str] = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
        body: Optional[str] = None
        if _json is not None:
            body = json.dumps(_json, ensure_ascii=False)
            headers["Content-Type"] = "application/json"
        elif data is not None:
            # aiohttp rejects None form values with TypeError; fail the same way instead of
            # silently posting the string "None" (e.g. a missing javax.faces.ViewState).
            missing = [k for k, v in data.items() if v is None]
            if missing:
                raise TypeError(f"form field(s) {missing} are None")
            body = urllib.parse.urlencode(data)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            resp = await self._request(method, url, headers, body)
        except GakuenAPIError:
            raise
        except Exception as e:  # noqa: BLE001 - network layer errors (abort, DNS, TLS...)
            raise GakuenNetworkError(
                f"ネットワークエラー: {str(e)}",
                error_code="NETWORK_ERROR",
            ) from e
        return _process_response(resp.status, resp.reason, resp.text(), response_type, features)

    async def close(self) -> None:
        self._closed = True
        self._cookies.clear()


def _HttpClient(session, timeout: int, http_proxy: Optional[str]):  # noqa: N802 - keeps the historical name
    """Factory returning the transport for the current runtime."""
    if runtime.IS_WORKERS:
        return _FetchClient(session, timeout, http_proxy)
    return _AiohttpClient(session, timeout, http_proxy)
