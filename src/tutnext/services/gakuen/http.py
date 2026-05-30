# tutnext/services/gakuen/http.py
# Low-level HTTP transport layer used by GakuenAPI.
import json
import urllib.parse
from typing import Literal, Optional, Union

import aiohttp
from bs4 import BeautifulSoup

from tutnext.services.gakuen.errors import GakuenAPIError, GakuenNetworkError


class _HttpClient:
    """HTTP 通信層"""

    def __init__(
        self,
        session: Optional[aiohttp.ClientSession],
        timeout: int,
        http_proxy: Optional[str],
    ) -> None:
        self._owns_session = session is None
        self.session = session or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        )
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
        _error = False
        try:
            async with self.session.request(
                method, url, data=data, json=_json, params=params, proxy=self.http_proxy
            ) as response:
                if response.status != 200:
                    # 代理或目标返回 5xx → 上报看门狗（由 TCP 探针辨别真伪）
                    if self.http_proxy and response.status >= 500:
                        self._report_proxy_failure(
                            f"HTTP {response.status} via proxy"
                        )
                    if response_type == "json":
                        _error = True
                    else:
                        raise GakuenNetworkError(
                            f"HTTPエラー: {response.reason}",
                            error_code="HTTP_ERROR",
                            status_code=response.status,
                        )
                html = await response.text()
                if response_type == "json":
                    if "innerInfo" in html:
                        soup = BeautifulSoup(html, "html.parser")
                        if error_msg := soup.find("p", class_="innerInfo"):
                            raise GakuenAPIError(
                                f"APIエラー: {error_msg.text}",
                                error_code="API_ERROR",
                            )
                    try:
                        out_json = json.loads(
                            urllib.parse.unquote(html)
                            .replace("\u3000", " ")
                            .replace("+", " ")
                        )
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
