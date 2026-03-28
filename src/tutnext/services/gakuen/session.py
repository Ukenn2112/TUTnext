# tutnext/services/gakuen/session.py
# Pure-data container that tracks Web/API session state between requests.
from typing import Optional

from bs4 import BeautifulSoup, Tag


class _SessionState:
    """Web セッション状態の純粋データコンテナ"""

    def __init__(self) -> None:
        self.rx_tokens: dict = {}
        self.view_state: Optional[str] = None
        self.class_list: dict = {}
        self.web_is_logged_in: bool = False
        self.api_is_logged_in: bool = False
        self.first_setting: Optional[dict] = None

    def update_from_soup(self, soup: BeautifulSoup) -> None:
        """BeautifulSoup オブジェクトからセッショントークンを抽出"""
        token_mapping = {
            "rx-token": "token",
            "rx-loginKey": "loginKey",
            "rx-deviceKbn": "deviceKbn",
            "rx-loginType": "loginType",
        }
        for input_tag in soup.find_all("input"):
            if not isinstance(input_tag, Tag):
                continue
            name = input_tag.get("name")
            value = input_tag.get("value")
            if isinstance(name, str) and name in token_mapping:
                self.rx_tokens[token_mapping[name]] = value
            elif name == "javax.faces.ViewState" and isinstance(value, str):
                self.view_state = value
