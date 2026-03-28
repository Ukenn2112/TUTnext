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
            raise GakuenNetworkError(
                f"ネットワークエラー: {str(e)}",
                error_code="NETWORK_ERROR",
            ) from e

    async def close(self) -> None:
        """セッションを閉じる"""
        if self._owns_session and not self.session.closed:
            await self.session.close()
