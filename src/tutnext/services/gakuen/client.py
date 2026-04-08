"""
学园系统 API 客户端
===================
T-NEXT (多摩大学キャンパスシステム) 的异步爬虫客户端。

大学系统基于 PrimeFaces (JSF) 框架，使用以下会话机制:
- rx-token / rx-loginKey / rx-deviceKbn / rx-loginType: 每次请求都需要携带的会话令牌
- javax.faces.ViewState: JSF 视图状态，每次响应后更新
- PrimeFaces 组件 ID: 动态生成，需要从 JavaScript 中提取

认证流程:
1. api_login() → 获取 encryptedPassword（用于移动端登录）
2. _mobile_login() → 使用 encryptedPassword 登录移动端页面
3. 登录后可以访问: 课题列表(get_user_kadai)、次日日程(get_later_user_schedule)

注意: 会话令牌在每次请求后都会更新，必须保持 _SessionState 同步。

Helper classes live in their own modules:
  errors.py  – exception hierarchy
  session.py – _SessionState (token/view-state container)
  http.py    – _HttpClient (aiohttp transport)
  ids.py     – _MobilePageIds (PrimeFaces component ID registry)
"""
# tutnext/services/gakuen/client.py
import re
import logging
import json
import urllib.parse
from typing import Any, Optional, Union, Literal

from bs4 import BeautifulSoup, Tag
from datetime import date, datetime, timedelta

from tutnext.services.gakuen.errors import (
    GakuenAPIError,
    GakuenLoginError,
    GakuenNetworkError,
    GakuenDataError,
    GakuenPermissionError,
)
from tutnext.services.gakuen.session import _SessionState
from tutnext.services.gakuen.http import _HttpClient
from tutnext.services.gakuen.ids import _MobilePageIds

import aiohttp

_ROOM_CACHE_TTL = 604800  # 1 week


class GakuenAPI:
    """学園システムAPIクライアント"""

    def __init__(
        self,
        user_id: str,
        password: str,
        base_url: str,
        encrypted_login_password: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None,
        timeout: int = 20,
        http_proxy: Optional[str] = None,
    ) -> None:
        """GakuenAPIクライアントを初期化

        Args:
            user_id: ユーザーID（学籍番号）
            password: パスワード
            base_url: 大学のベースURL（例: https://you.university.url）
            encrypted_login_password: 暗号化済みログインパスワード
            session: 既存のaiohttpセッション（省略可）
            timeout: リクエストタイムアウト（秒）
            http_proxy: HTTPプロキシURL（例: http://127.0.0.1:8888）。利用しない場合は None。
        """
        self.user_id = user_id
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.encrypted_login_password = encrypted_login_password
        self.http_proxy = http_proxy

        self._http = _HttpClient(session, timeout, http_proxy)
        self._state = _SessionState()
        self._ids = _MobilePageIds()

        # Backward compatibility: expose session reference
        self.session = self._http.session
        self._owns_session = self._http._owns_session

    # --- Backward-compatible property accessors ---

    @property
    def j_idt(self) -> Optional[str]:
        return self._ids.schedule_component_id

    @j_idt.setter
    def j_idt(self, value: str) -> None:
        self._ids.schedule_component_id = value

    @property
    def j_idt_kadai(self) -> Optional[str]:
        return self._ids.kadai_tab_id

    @j_idt_kadai.setter
    def j_idt_kadai(self, value: str) -> None:
        self._ids.kadai_tab_id = value

    @property
    def rx(self) -> dict:
        return self._state.rx_tokens

    @property
    def view_state(self) -> Optional[str]:
        return self._state.view_state

    @view_state.setter
    def view_state(self, value: str) -> None:
        self._state.view_state = value

    @property
    def class_list(self) -> dict:
        return self._state.class_list

    async def close(self) -> None:
        """セッションを閉じる"""
        await self._http.close()

    async def __aenter__(self):
        """非同期コンテキストマネージャー（入口）"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """非同期コンテキストマネージャー（出口）"""
        await self.close()

    async def login(self) -> dict[str, dict[str, str]]:
        """Web システムにログインする

        Raises:
            GakuenLoginError: ログインに失敗した場合
            GakuenAPIError: その他のAPIエラー

        Returns:
            Optional[dict]: クラス一覧の辞書。ログイン成功時に返される。
        """
        login_url = f"{self.base_url}/uprx/up/pk/pky001/Pky00101.xhtml"
        data = {
            "loginForm": "loginForm",
            "loginForm:userId": self.user_id,
            "loginForm:password": self.password,
            "loginForm:loginButton": "",
            "javax.faces.ViewState": "stateless",
        }
        try:
            soup = await self._http.fetch(login_url, method="POST", data=data)
            if not isinstance(soup, BeautifulSoup):
                raise GakuenLoginError(
                    "ログインに失敗しました。サーバーからの応答がありません。",
                    error_code="LOGIN_FAILED",
                )
            if error_msg := soup.find("span", class_="ui-messages-error-detail"):
                raise GakuenLoginError(
                    f"ログインエラー: {error_msg.text}", error_code="LOGIN_FAILED"
                )
            self._state.update_from_soup(soup)
            if soup.find("dt", class_="msgArea"):  # 重要アンケートがある場合
                soup = await self._to_home_page()
                self._state.update_from_soup(soup)
            self._ids.extract_desktop_ids(soup)
            await self._fetch_class_list(soup)
            if not self._state.class_list:
                raise GakuenDataError(
                    "クラスデータが取得できませんでした", error_code="NO_CLASS_DATA"
                )
            self._state.web_is_logged_in = True
            return self._state.class_list
        except Exception as e:
            if isinstance(e, GakuenAPIError):
                raise
            raise GakuenAPIError(
                f"ログイン処理中に予想外エラーが発生しました: {str(e)}",
                error_code="UNEXPECTED_ERROR",
            )

    async def month_data(self, year: int, month: int) -> list[dict]:
        """指定月の授業スケジュールを取得

        Args:
            year: 年
            month: 月

        Raises:
            GakuenNetworkError: ネットワークエラー
            GakuenDataError: データ解析エラー

        Returns:
            list: 授業イベントデータ
            各イベントは辞書形式で、以下のキーを含む:
                - "title": 授業名
                - "start": 開始日時（datetimeオブジェクト）
                - "end": 終了日時（datetimeオブジェクト）
                - "teacher": 教員名
                - "room": 教室名
                - "allDay": 終日イベントかどうか（bool）
        """
        if not self._state.web_is_logged_in:
            raise GakuenPermissionError(
                "Web ログインが必要です", error_code="NOT_LOGGED_IN"
            )

        # 月初のタイムスタンプを生成（ミリ秒単位）
        month_timestamp = str(int(datetime(year, month, 1).timestamp())) + "000"
        month_url = f"{self.base_url}/uprx/up/bs/bsa001/Bsa00101.xhtml"
        j_idt = self._ids.schedule_component_id
        data = {
            "javax.faces.partial.ajax": "true",
            f"javax.faces.partial.render": f"funcForm:{j_idt}:content",
            f"funcForm:{j_idt}:content": f"funcForm:{j_idt}:content",
            f"funcForm:{j_idt}:content_start": month_timestamp,
            f"funcForm:{j_idt}:content_end": month_timestamp,
            "rx-token": self._state.rx_tokens["token"],
            "rx-loginKey": self._state.rx_tokens["loginKey"],
            "rx-deviceKbn": self._state.rx_tokens["deviceKbn"],
            "rx-loginType": self._state.rx_tokens["loginType"],
            "javax.faces.ViewState": self._state.view_state,
        }
        try:
            processed_events = []
            soup = await self._http.fetch(
                month_url, method="POST", data=data, features="xml"
            )
            if (
                isinstance(soup, BeautifulSoup)
                and isinstance(
                    update_tag := soup.find("update", text=re.compile("rx-token")), Tag
                )
                and isinstance(
                    course_soup := soup.find(
                        "update", {"id": f"funcForm:{j_idt}:content"}
                    ),
                    Tag,
                )
            ):
                course_dict = json.loads(course_soup.text.strip())
                inner_soup = BeautifulSoup(update_tag.text, "html.parser")
                self._state.update_from_soup(inner_soup)
                for event in course_dict["events"]:
                    event["title"] = event["title"].replace("\u3000", " ").strip()
                    if not event["title"]:
                        continue
                    if not event["allDay"]:  # 1時間半単位の授業
                        event["start"] = datetime.strptime(
                            event["start"], "%Y-%m-%dT%H:%M:%S%z"
                        )
                        event["end"] = event["start"] + timedelta(minutes=90)
                    else:  # 1日単位の授業
                        if event["className"] == "eventKeijiAd":
                            continue
                        t = datetime.strptime(event["start"], "%Y-%m-%dT%H:%M:%S%z")
                        t_e = datetime.strptime(event["end"], "%Y-%m-%dT%H:%M:%S%z")
                        event["start"] = date(t.year, t.month, t.day) + timedelta(
                            days=1
                        )
                        event["end"] = date(t_e.year, t_e.month, t_e.day)
                    if (
                        event["title"] in self._state.class_list
                    ):  # 授業名が一致するものがあれば
                        event["teacher"] = self._state.class_list[event["title"]][
                            "lessonTeachers"
                        ]
                        event["room"] = self._state.class_list[event["title"]]["lessonClass"]
                    processed_events.append(event)
            if not processed_events:
                raise GakuenDataError(
                    "指定された月の授業データが取得できませんでした",
                    error_code="NO_MONTH_DATA",
                )
            return processed_events
        except Exception as e:
            if isinstance(e, GakuenAPIError):
                raise
            raise GakuenDataError(
                f"月データ取得中に予想外エラーが発生しました: {str(e)}",
                error_code="MONTH_DATA_ERROR",
            )

    async def kadai_data(self) -> list[dict]:
        """課題データを取得 (Web)

        Raises:
            GakuenDataError: 課題データの取得に失敗した場合

        Returns:
            list[dict]: 課題データのリスト。各課題は辞書形式で、以下のキーを含む:
                - "task": 課題の種類（例: "課題", "レポート"）
                - "date": 課題の日付
                - "title": 課題のタイトル
                - "from": 課題の出所（例: 教員名）
                - "deadline": 課題の締切日時（datetimeオブジェクト）
        """
        if not self._state.web_is_logged_in:
            raise GakuenPermissionError(
                "Web ログインが必要です", error_code="NOT_LOGGED_IN"
            )

        try:
            soup = await self._to_home_page()
            self._state.update_from_soup(soup)
            kadai_tab = soup.find("div", id=f"funcForm:{self._ids.kadai_tab_id}")
            if not isinstance(kadai_tab, Tag):
                raise GakuenDataError(
                    "課題データの取得に失敗しました",
                    error_code="KADAI_DATA_ERROR",
                )
            kaitai_list = []
            for item in kadai_tab.find_all("li", class_="ui-datalist-item"):
                if isinstance(item, Tag) and (
                    task := item.find(class_="signPortal signPortalKadai")
                ):
                    deadline = datetime.strptime(
                        item.find_all(class_="textDate")[-1].text + "/23:59",
                        "%Y/%m/%d/%H:%M",
                    )
                    if isinstance(
                        _date := item.find(class_="textDate"), Tag
                    ) and isinstance(_title := item.find(class_="textTitle"), Tag):
                        kaitai_list.append(
                            {
                                "task": task.text,
                                "date": _date.text,
                                "title": _title.text.replace("\u3000", " "),
                                "from": item.find_all(class_="textFrom")[
                                    1
                                ].text.replace("\u3000", " "),
                                "deadline": deadline,
                            }
                        )
            return kaitai_list
        except Exception as e:
            if isinstance(e, GakuenAPIError):
                raise
            raise GakuenDataError(
                f"課題データ取得中に予想外エラーが発生しました: {str(e)}",
                error_code="KADAI_DATA_ERROR",
            )

    async def api_login(self) -> dict:
        """APIログインを行う

        Raises:
            GakuenLoginError: ログインに失敗した場合
            GakuenAPIError: その他のAPIエラー

        Returns:
            dict: ログイン成功時のトークン情報
        """
        login_url = f"{self.base_url}/uprx/webapi/up/pk/Pky001Resource/login"
        _json = {
            "data": {
                "loginUserId": self.user_id,
                "plainLoginPassword": self.password,
            }
        }
        try:
            data = await self._http.fetch(
                login_url, method="POST", _json=_json, response_type="json"
            )
            if not isinstance(data, dict):
                raise GakuenLoginError(
                    "APIログインに失敗しました。サーバーからの応答が予想外でいま。",
                    error_code="API_LOGIN_FAILED",
                )
            self._state.api_is_logged_in = True
            self.encrypted_login_password = data["data"]["encryptedPassword"]
            # Fix 4: call firstSetting after login (required by the app flow)
            self._state.first_setting = await self._call_first_setting()
            return data["data"]
        except Exception as e:
            if isinstance(e, GakuenAPIError):
                raise
            raise GakuenAPIError(
                f"APIログイン処理中に予想外エラーが発生しました: {str(e)}",
                error_code="UNEXPECTED_API_LOGIN_ERROR",
            )

    async def api_login_out(self) -> dict:
        """APIログアウトを行う

        Raises:
            GakuenAPIError: ログアウトに失敗した場合

        Returns:
            dict: ログアウト成功時のレスポンスデータ
        """
        if not self._state.api_is_logged_in:
            raise GakuenPermissionError(
                "Api ログインが必要です", error_code="NOT_LOGGED_IN"
            )

        logout_url = f"{self.base_url}/uprx/webapi/up/pk/Pky002Resource/logout"
        _json = {
            "subProductCd": "apa",
            "plainLoginPassword": "",
            "loginUserId": self.user_id,
            "langCd": "",
            "productCd": "ap",
            "encryptedLoginPassword": self.encrypted_login_password,
        }
        try:
            data = await self._http.fetch(
                logout_url, method="POST", _json=_json, response_type="json"
            )
            if not isinstance(data, dict):
                raise GakuenAPIError(
                    "APIログアウトに失敗しました。サーバーからの応答が予想外でいま。",
                    error_code="API_LOGOUT_FAILED",
                )
            return data
        except Exception as e:
            raise GakuenAPIError(
                f"APIログアウト処理中に予想外エラーが発生しました: {str(e)}",
                error_code="UNEXPECTED_API_LOGOUT_ERROR",
            )

    async def class_bulletin(
        self, year: int = 0, semester: Literal[0, 1, 2] = 0
    ) -> dict:
        """クラスデータ取得 (Api loginが必要) Student Only

        Args:
            year: 年（省略時は現在の年）
            semester: 学期 [全学期0,春学期1,秋学期2]（省略時は全学期）

        Raises:
            GakuenAPIError: クラスデータの取得に失敗した場合
            GakuenPermissionError: ログインが必要な場合
        Returns:
            dict: クラスデータの辞書。各授業はキーとして授業名を持ち、値は授業情報の辞書。
        """
        if not self._state.api_is_logged_in:
            raise GakuenPermissionError(
                "Api ログインが必要です", error_code="NOT_LOGGED_IN"
            )

        class_url = (
            f"{self.base_url}/uprx/webapi/up/ap/Apa004Resource/getJugyoKeijiMenuInfo"
        )
        _json = {
            "plainLoginPassword": "",
            "data": {
                "kaikoNendo": year,
                "gakkiNo": semester,
            },
            "langCd": "",
            "encryptedLoginPassword": self.encrypted_login_password,
            "loginUserId": self.user_id,
            "productCd": "ap",
            "subProductCd": "apa",
        }
        try:
            data = await self._http.fetch(
                class_url, method="POST", _json=_json, response_type="json"
            )
            if not isinstance(data, dict):
                raise GakuenDataError(
                    "クラスデータの取得に失敗しました",
                    error_code="CLASS_DATA_ERROR",
                )
            return data["data"]
        except Exception as e:
            raise GakuenAPIError(
                f"クラスデータ取得中に予想外エラーが発生しました: {str(e)}",
                error_code="UNEXPECTED_CLASS_DATA_ERROR",
            )

    async def class_data_info(self, class_data: dict) -> dict:
        """クラス掲示データ取得 (webapi loginが必要)

        Args:
            class_data: クラスデータの辞書。

        Raises:
            GakuenDataError: クラスデータの詳細情報の取得に失敗した場合

        Returns:
            dict: クラスデータ掲示データ
        """
        if not self._state.api_is_logged_in:
            raise GakuenPermissionError(
                "Api ログインが必要です", error_code="NOT_LOGGED_IN"
            )

        class_info_url = (
            f"{self.base_url}/uprx/webapi/up/ap/Apa004Resource/getJugyoDetailInfo"
        )
        _json = {
            "loginUserId": self.user_id,
            "langCd": "",
            "encryptedLoginPassword": self.encrypted_login_password,
            "productCd": "ap",
            "plainLoginPassword": "",
            "subProductCd": "apa",
            "data": class_data,
        }
        try:
            data = await self._http.fetch(
                class_info_url, method="POST", _json=_json, response_type="json"
            )
            if not isinstance(data, dict):
                raise GakuenDataError(
                    "クラス掲示データ",
                    error_code="CLASS_DATA_INFO_ERROR",
                )
            return data["data"]
        except Exception as e:
            raise GakuenAPIError(
                f"クラス掲示データ情報取得中に予想外エラーが発生しました: {str(e)}",
                error_code="UNEXPECTED_CLASS_DATA_INFO_ERROR",
            )

    async def get_later_user_schedule(
        self,
        user_id: Optional[str] = None,
        encrypted_login_password: Optional[str] = None,
        target_date: Optional[date] = None,
        skip_login: bool = False,
    ) -> dict:
        """後日ユーザースケジュール取得 (encrypted_login_passwordが必要) Student Only

        Args:
            user_id: ユーザーID（学籍番号）
            encrypted_login_password: 暗号化されたログインパスワード
            target_date: 取得する日付（省略時は翌日）

        Raises:
            GakuenPermissionError: ユーザーIDと暗号化されたパスワードが必要な場合
            GakuenAPIError: システムのホームページの取得やセッション情報の抽出に失敗した場合

        Returns:
            dict: ユーザースケジュールの辞書。以下のキーを含む:
                - "date_info": 日付情報
                    - "date": 日付（例: "2025/07/15"）
                    - "day_of_week": 曜日（例: "火"）
                - "all_day_events": 終日イベントのリスト
                    - "title": イベントタイトル
                    - "id": イベントID
                    - "is_important": 重要なイベントかどうか（bool）
                - "time_table": 時間割のリスト
                    - "time": 授業時間（例: "09:00-10:40"）
                    - "special_tags": 特別なタグのリスト（存在する場合）
                    - "lesson_num": 授業限数（1-7）
                    - "name": 授業名
                    - "teachers": 教員名（リスト）
                    - "room": 教室名
                    - "previous_room": 変更前の教室名（存在する場合）
        """
        out_data: dict[str, Any] = {
            "date_info": {},
            "all_day_events": [],
            "time_table": [],
        }

        if user_id:
            self.user_id = user_id
        if encrypted_login_password:
            self.encrypted_login_password = encrypted_login_password
        if target_date is None:
            target_date = date.today() + timedelta(days=1)
        target_date_str = target_date.strftime("%Y/%m/%d")
        try:
            if not skip_login:
                await self._mobile_login()
            schedule_url = f"{self.base_url}/uprx/up/bs/bsa501/Bsa50101.xhtml"
            cal = self._ids.calendar_id or "pmPage:funcForm:j_idt104"
            acc_active = self._ids.accordion_active_id or "pmPage:funcForm:j_idt107_active"
            data = {
                "javax.faces.partial.ajax": "true",
                "javax.faces.source": cal,
                "javax.faces.partial.execute": cal,
                "javax.faces.partial.render": "pmPage:funcForm:mainContent",
                "javax.faces.behavior.event": "dateSelect",
                "javax.faces.partial.event": "dateSelect",
                "pmPage:funcForm": "pmPage:funcForm",
                "rx-token": self._state.rx_tokens["token"],
                "rx-loginKey": self._state.rx_tokens["loginKey"],
                "rx-deviceKbn": self._state.rx_tokens["deviceKbn"],
                "rx-loginType": self._state.rx_tokens["loginType"],
                f"{cal}_input": target_date_str,
                acc_active: "0,1",
                "javax.faces.ViewState": self._state.view_state,
                "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
            }
            soup = await self._http.fetch(
                schedule_url, method="POST", data=data, features="xml"
            )
            if (
                isinstance(soup, BeautifulSoup)
                and isinstance(
                    update_tag := soup.find("update", text=re.compile("rx-token")), Tag
                )
                and isinstance(
                    main_content := soup.find(
                        "update", {"id": "pmPage:funcForm:mainContent"}
                    ),
                    Tag,
                )
            ):
                inner_soup = BeautifulSoup(update_tag.text, "html.parser")
                self._state.update_from_soup(inner_soup)
                content_soup = BeautifulSoup(main_content.text, "html.parser")

                # 1. 日期信息
                date_display = content_soup.find("span", class_="dateDisp")
                if date_display:
                    raw_date = date_display.text.strip()
                    out_data["date_info"] = {
                        "date": raw_date.split("(")[0],
                        "day_of_week": raw_date.split("(")[1].rstrip(")"),
                    }

                # 2. 終日活動 (全天事件)
                all_day_panel = content_soup.find("div", class_="syujitsuPanel")
                if isinstance(all_day_panel, Tag):
                    all_day_events = all_day_panel.find_all("a", recursive=True)
                    for event in all_day_events:
                        if isinstance(event, Tag):
                            out_data["all_day_events"].append(
                                {
                                    "title": event.text.strip().replace("\u3000", " "),
                                    "id": event.get("id", ""),
                                    "is_important": "重要" in event.text
                                    or "【重要】" in event.text,
                                }
                            )

                # 3. 課程情報 (時間表)
                time_panel = None
                for panel in content_soup.find_all("div", class_="ui-panel-m"):
                    header = panel.find("h3")
                    if header and "時間別" in header.text:
                        time_panel = panel.find("div", class_="ui-datalist")
                        break
                if not isinstance(time_panel, Tag):  # fallback for layout/id changes
                    time_panel = content_soup.find("div", class_="ui-datalist")
                if isinstance(time_panel, Tag):
                    class_items = time_panel.select("li")
                    for item in class_items:
                        class_data = {}

                        # 時間
                        time_header = item.find("div", class_="jugyoInfoArea")
                        if isinstance(time_header, Tag):
                            # 時間情報中の span[@floatRight] タグを除去
                            if _fr := time_header.find("span", class_="floatRight"):
                                _fr.extract()
                            class_data["time"] = time_header.text.strip()
                            # 教室変更マークを確認
                            if change_room_tag := time_header.find_all(
                                "span", class_="signLesson"
                            ):
                                class_data["special_tags"] = []
                                for tag in change_room_tag:
                                    class_data["special_tags"].append(tag.text.strip())
                                    class_data["time"] = class_data["time"].replace(
                                        tag.text.strip(), ""
                                    )
                            if "09:00" in class_data["time"]:
                                class_data["lesson_num"] = 1
                            elif "10:40" in class_data["time"]:
                                class_data["lesson_num"] = 2
                            elif "13:00" in class_data["time"]:
                                class_data["lesson_num"] = 3
                            elif "14:40" in class_data["time"]:
                                class_data["lesson_num"] = 4
                            elif "16:20" in class_data["time"]:
                                class_data["lesson_num"] = 5
                            elif "18:00" in class_data["time"]:
                                class_data["lesson_num"] = 6
                            elif "19:40" in class_data["time"]:
                                class_data["lesson_num"] = 7

                        # 課程名称
                        class_name = item.find("span", class_="jugyoName")
                        if class_name:
                            class_data["name"] = class_name.text.strip().replace(
                                "\u3000", " "
                            )

                        # 教師
                        teachers = []
                        teacher_tags = item.find_all("a", class_="tantoKyoin")
                        if teacher_tags:
                            for teacher in teacher_tags:
                                teachers.append(
                                    teacher.text.strip().replace("\u3000", " ")
                                )
                            class_data["teachers"] = teachers

                        # 教室
                        if isinstance(
                            class_details := item.find("div", class_="jknbtDtl"), Tag
                        ):
                            # 通常教室情報を取得
                            # 教室は教員リンク(a.tantoKyoin)を含むdivの次の兄弟divにある
                            teacher_parent = None
                            for div in class_details.find_all("div", recursive=False):
                                if isinstance(div, Tag) and div.find("a", class_="tantoKyoin"):
                                    teacher_parent = div
                                    break
                            if teacher_parent:
                                next_div = teacher_parent.find_next_sibling("div")
                                if (
                                    isinstance(next_div, Tag)
                                    and not next_div.get("id")
                                    and not next_div.get("class")
                                ):
                                    room_text = next_div.get_text(strip=True)
                                    if room_text:
                                        class_data["room"] = room_text

                            # 変更前教室を取得
                            if isinstance(
                                change_room_div := class_details.find(
                                    "div",
                                    {"id": lambda x: x is not None and "j_idt248" in x},
                                ),
                                Tag,
                            ) and isinstance(
                                previous_room := change_room_div.find("div"), Tag
                            ):
                                class_data["previous_room"] = previous_room.text.strip()

                        if class_data:
                            out_data["time_table"].append(class_data)

            # 教室情報を Redis にキャッシュ
            try:
                from tutnext.config import redis
                for entry in out_data["time_table"]:
                    if entry.get("name") and entry.get("room"):
                        await redis.set(
                            f"room:{entry['name']}", entry["room"], ex=_ROOM_CACHE_TTL
                        )
            except Exception:
                pass  # Redis 障害時はキャッシュをスキップ

            return out_data
        except Exception as e:
            if isinstance(e, GakuenAPIError):
                raise
            raise GakuenAPIError(
                f"ユーザーのスケジュール取得中に予想外エラーが発生しました: {str(e)}",
                error_code="UNEXPECTED_USER_SCHEDULE_ERROR",
            )

    async def get_user_kadai(
        self,
        user_id: Optional[str] = None,
        encrypted_login_password: Optional[str] = None,
        skip_login: bool = False,
    ) -> list[dict]:
        """ユーザーの課題データを取得 (Mobile loginが必要) Student Only

        Args:
            user_id: ユーザーID（学籍番号）。省略時はインスタンスのuser_idを使用します。
            encrypted_login_password: 暗号化されたログインパスワード。省略時はインスタンスの encrypted_login_password を使用します。

        Raises:
            GakuenAPIError: ユーザーの課題データの取得に失敗した場合

        Returns:
            list[dict]: 課題データのリスト
            各課題は辞書形式で、以下のキーを含む:
                - "id": 課題ID
                - "courseSemesterName": コースの学期名
                - "courseName": コース名
                - "courseId": コースID
                - "group": グループ名（存在する場合）
                - "title": 課題タイトル
                - "publishStart": 課題の公開開始日時（`2025/07/15(火) 10:40`形式の文字列）
                - "publishEnd": 課題の公開終了日時（`2025/07/31(木) 23:59`形式の文字列）
                - "submitStart": 課題の提出開始日時（`2025/07/15(火) 10:40`形式の文字列）
                - "submitEnd": 課題の提出終了日時（`2025/07/31(木) 23:59`形式の文字列）
                - "dueDate": 課題の締切日時（`YYYY-MM-DD`形式の文字列）
                - "dueTime": 課題の締切時間（`HH:MM`形式の文字列）
                - "description": 課題の説明（存在する場合）
                - "proposedMethod": 課題の提出方法（存在する場合）
                - "minLength": 課題の最小文字数（存在する場合）
                - "maxLength": 課題の最大文字数（存在する場合）
        """
        if user_id:
            self.user_id = user_id
        if encrypted_login_password:
            self.encrypted_login_password = encrypted_login_password
        try:
            if not skip_login:
                await self._mobile_login()
            kadai_url = f"{self.base_url}/uprx/up/bs/bsa501/Bsa50101.xhtml"
            # Fix 2: use dynamically discovered IDs with fallbacks
            acc_active = self._ids.accordion_active_id or "pmPage:funcForm:j_idt107_active"
            kadai_link = self._ids.kadai_tab_link_id or "pmPage:funcForm:j_idt107:j_idt125"
            data = {
                "pmPage:funcForm": "pmPage:funcForm",
                "rx-token": self._state.rx_tokens["token"],
                "rx-loginKey": self._state.rx_tokens["loginKey"],
                "rx-deviceKbn": self._state.rx_tokens["deviceKbn"],
                "rx-loginType": self._state.rx_tokens["loginType"],
                acc_active: "0,1",
                "javax.faces.ViewState": self._state.view_state,
                "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
                "rx.sync.source": kadai_link,
                kadai_link: kadai_link,
            }
            soup = await self._http.fetch(kadai_url, method="POST", data=data)
            if not isinstance(soup, BeautifulSoup):
                raise GakuenAPIError(
                    "ユーザーの課題データの取得に失敗しました",
                    error_code="USER_KADAI_FETCH_ERROR",
                )
            self._state.update_from_soup(soup)
            kadai_list = []
            main_content = soup.find("div", class_="mainContent")
            if not isinstance(main_content, Tag):
                raise GakuenAPIError(
                    "ユーザーの課題データの取得に失敗しました",
                    error_code="USER_KADAI_FETCH_ERROR",
                )
            all_items = main_content.find_all("li")
            total_items = len(all_items)
            retry_count = 0  # 再試行カウンター
            max_retries = 5  # 最大再試行回数
            for item_index, item in enumerate(all_items):
                if not isinstance(item, Tag):
                    continue
                link = item.find("a")
                if not isinstance(link, Tag):
                    continue
                kaidai_id = link.get("id")
                if not isinstance(kaidai_id, str) or "j_idt81" not in kaidai_id:
                    continue
                kadai_info_url = f"{self.base_url}/uprx/up/bs/bsa501/Bsa50102.xhtml"
                data = {
                    "pmPage:funcForm": "pmPage:funcForm",
                    "rx-token": self._state.rx_tokens["token"],
                    "rx-loginKey": self._state.rx_tokens["loginKey"],
                    "rx-deviceKbn": self._state.rx_tokens["deviceKbn"],
                    "javax.faces.ViewState": self._state.view_state,
                    "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
                    "rx.sync.source": kaidai_id,
                    kaidai_id: kaidai_id,
                }
                soup = await self._http.fetch(kadai_info_url, method="POST", data=data)
                if not isinstance(soup, BeautifulSoup):
                    logging.warning(
                        f"ユーザー: {self.user_id} の課題データの取得に失敗しました (ID: {kaidai_id} - {link.text}) (スキップします)"
                    )
                    continue
                self._state.update_from_soup(soup)
                # 授業インフォ
                kadai_data = {}
                kadai_data["id"] = kaidai_id
                if isinstance(class_info := soup.find("div", class_="jugyoInfo"), Tag):
                    lesson_title_detail = class_info.find_all(
                        "span", class_="nendoGakkiDisp"
                    )
                    kadai_data["courseSemesterName"] = lesson_title_detail[0].text
                    kadai_data["courseName"] = lesson_title_detail[1].text
                    course_id = re.search(r"\[(.*?)\]", class_info.text)
                    kadai_data["courseId"] = course_id.group(1) if course_id else ""
                # 課題インフォ
                if isinstance(kadai_info := soup.find("ul", class_="tableData"), Tag):
                    if (
                        isinstance(
                            kadai_group := kadai_info.find(  # type: ignore[call-overload]
                                "label",
                                string=re.compile(r"グループ"),  # type: ignore[arg-type]
                            ),
                            Tag,
                        )
                        and isinstance(
                            kadai_group_parent := kadai_group.parent,
                            Tag,
                        )
                        and isinstance(
                            kadai_group_li := kadai_group_parent.find_next_sibling(
                                "li"
                            ),
                            Tag,
                        )
                    ):
                        kadai_data["group"] = kadai_group_li.text.strip()
                    if (
                        isinstance(
                            kadai_title := kadai_info.find(  # type: ignore[call-overload]
                                "label",
                                string=re.compile(r"^(課題名|テスト名)$"),  # type: ignore[arg-type]
                            ),
                            Tag,
                        )
                        and isinstance(kadai_title_parent := kadai_title.parent, Tag)
                        and isinstance(
                            kadai_title_li := kadai_title_parent.find_next_sibling(
                                "li"
                            ),
                            Tag,
                        )
                    ):
                        kadai_data["title"] = kadai_title_li.text.strip()
                    if (
                        isinstance(
                            kadai_public_period := kadai_info.find(  # type: ignore[call-overload]
                                "label",
                                string=re.compile(r"課題公開期間"),  # type: ignore[arg-type]
                            ),
                            Tag,
                        )
                        and isinstance(
                            kadai_public_period_parent := kadai_public_period.parent,
                            Tag,
                        )
                        and isinstance(
                            kadai_public_period_li := kadai_public_period_parent.find_next_sibling(
                                "li"
                            ),
                            Tag,
                        )
                    ):
                        spans = kadai_public_period_li.find_all("span")
                        if len(spans) >= 3:
                            kadai_data["publishStart"] = spans[0].text.strip()
                            kadai_data["publishEnd"] = spans[2].text.strip()
                    if (
                        isinstance(
                            kadai_submit_period := kadai_info.find(  # type: ignore[call-overload]
                                "label",
                                string=re.compile(r"^(課題提出期間|テスト期間)$"),  # type: ignore[arg-type]
                            ),
                            Tag,
                        )
                        and isinstance(
                            kadai_submit_period_parent := kadai_submit_period.parent,
                            Tag,
                        )
                        and isinstance(
                            kadai_submit_period_li := kadai_submit_period_parent.find_next_sibling(
                                "li"
                            ),
                            Tag,
                        )
                    ):
                        spans = kadai_submit_period_li.find_all("span")
                        if len(spans) >= 3:
                            kadai_data["submitStart"] = spans[0].text.strip()
                            kadai_data["submitEnd"] = spans[2].text.strip()
                            if due_date := re.search(
                                r"(\d{4}/\d{2}/\d{2})", spans[2].text
                            ):
                                kadai_data["dueDate"] = due_date.group(1).replace(
                                    "/", "-"
                                )
                            if due_time := re.search(r"(\d{2}:\d{2})", spans[2].text):
                                kadai_data["dueTime"] = due_time.group(1)
                        elif len(spans) == 2:
                            kadai_data["submitStart"] = spans[0].text.strip()
                            kadai_data["submitEnd"] = spans[1].text.strip()
                            if due_date := re.search(
                                r"(\d{4}/\d{2}/\d{2})", spans[1].text
                            ):
                                kadai_data["dueDate"] = due_date.group(1).replace(
                                    "/", "-"
                                )
                            if due_time := re.search(r"(\d{2}:\d{2})", spans[1].text):
                                kadai_data["dueTime"] = due_time.group(1)
                    if (
                        isinstance(
                            kadai_content := kadai_info.find(  # type: ignore[call-overload]
                                "label",
                                string=re.compile(r"^(課題内容|テスト説明)$"),  # type: ignore[arg-type]
                            ),
                            Tag,
                        )
                        and isinstance(
                            kadai_content_parent := kadai_content.parent,
                            Tag,
                        )
                        and isinstance(
                            kadai_content_li := kadai_content_parent.find_next_sibling(
                                "li"
                            ),
                            Tag,
                        )
                    ):
                        kadai_data["description"] = (
                            kadai_content_li.text.strip().replace("\u3000", "")
                        )
                    if isinstance(
                        kadai_proposed_method := kadai_info.find(  # type: ignore[call-overload]
                            "li",
                            string=re.compile(r"課題提出方法"),  # type: ignore[arg-type]
                        ),
                        Tag,
                    ) and isinstance(
                        kadai_proposed_method_li := kadai_proposed_method.find_next_sibling(
                            "li"
                        ),
                        Tag,
                    ):
                        kadai_data["proposedMethod"] = (
                            kadai_proposed_method_li.text.strip()
                        )
                        if min_length := kadai_proposed_method_li.find_all(
                            "span", class_="smallInput"
                        ):
                            kadai_data["minLength"] = min_length[0].text.strip()
                            kadai_data["maxLength"] = min_length[1].text.strip()
                    kadai_data["url"] = self._build_mobile_login_url()
                    kadai_list.append(kadai_data)
                back_kadai_list_url = (
                    f"{self.base_url}/uprx/up/jg/jga505/Jga50503.xhtml"
                )
                data = {
                    "pmPage:funcForm": "pmPage:funcForm",
                    "rx-token": self._state.rx_tokens["token"],
                    "rx-loginKey": self._state.rx_tokens["loginKey"],
                    "rx-deviceKbn": self._state.rx_tokens["deviceKbn"],
                    "javax.faces.ViewState": self._state.view_state,
                    "pmPage:funcForm:tstContent": "",
                    "pmPage:funcForm:tstComment": "",
                    "pmPage:funcForm:j_idt278:j_idt281": "",
                    "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
                    "rx.sync.source": "pmPage:funcForm:j_idt278:j_idt281",
                }
                try:
                    soup = await self._http.fetch(
                        back_kadai_list_url, method="POST", data=data
                    )
                except GakuenAPIError as e:
                    if e.error_code == "HTTP_ERROR":
                        # HTTPエラーが発生した場合、最後の課題かどうかをチェック
                        is_last_item = item_index >= total_items - 1
                        if is_last_item:
                            # 最後の課題の場合は正常終了
                            logging.info(f"ユーザー: {self.user_id} の課題一覧の最後に到達しました")
                            break
                        else:
                            # 最後ではない場合、再試行回数をチェック
                            retry_count += 1
                            if retry_count > max_retries:
                                raise GakuenAPIError(
                                    f"ユーザー: {self.user_id} の課題取得中にHTTPエラーが発生しました。最大再試行回数({max_retries}回)を超えました (位置: {item_index + 1}/{total_items}, ID: {kaidai_id})",
                                    error_code="MAX_RETRIES_EXCEEDED",
                                )
                            # 現在位置を記録して再ログインして再試行
                            logging.warning(
                                f"ユーザー: {self.user_id} の課題取得中にHTTPエラーが発生しました (位置: {item_index + 1}/{total_items}, ID: {kaidai_id}, 再試行: {retry_count}/{max_retries})"
                            )
                            logging.warning("再ログインして続行します...")
                            # 再ログインを実行
                            await self._mobile_login()
                            # 課題一覧ページに戻る
                            kadai_url = f"{self.base_url}/uprx/up/bs/bsa501/Bsa50101.xhtml"
                            acc_active = self._ids.accordion_active_id or "pmPage:funcForm:j_idt107_active"
                            kadai_link = self._ids.kadai_tab_link_id or "pmPage:funcForm:j_idt107:j_idt125"
                            data_relogin = {
                                "pmPage:funcForm": "pmPage:funcForm",
                                "rx-token": self._state.rx_tokens["token"],
                                "rx-loginKey": self._state.rx_tokens["loginKey"],
                                "rx-deviceKbn": self._state.rx_tokens["deviceKbn"],
                                "rx-loginType": self._state.rx_tokens["loginType"],
                                acc_active: "0,1",
                                "javax.faces.ViewState": self._state.view_state,
                                "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
                                "rx.sync.source": kadai_link,
                                kadai_link: kadai_link,
                            }
                            soup_relogin = await self._http.fetch(kadai_url, method="POST", data=data_relogin)
                            if not isinstance(soup_relogin, BeautifulSoup):
                                raise GakuenAPIError(
                                    "再ログイン後の課題データの取得に失敗しました",
                                    error_code="USER_KADAI_FETCH_ERROR",
                                )
                            self._state.update_from_soup(soup_relogin)
                            # 次の課題に進む
                            continue
                    else:
                        raise
                # 正常に処理できた場合、再試行カウンターをリセット
                retry_count = 0
                if not isinstance(soup, BeautifulSoup):
                    raise GakuenAPIError(
                        "ユーザーの課題一覧ページの取得に失敗しました",
                        error_code="USER_KADAI_LIST_FETCH_ERROR",
                    )
                self._state.update_from_soup(soup)
            return kadai_list
        except Exception as e:
            if isinstance(e, GakuenAPIError):
                raise
            raise GakuenAPIError(
                f"ユーザーの課題取得中に予想外エラーが発生しました: {str(e)}",
                error_code="UNEXPECTED_USER_KADAI_ERROR",
            )

    def _build_mobile_login_url(self) -> str:
        """モバイルログイン URL を構築する
        encryptedPassword は api_login 経由で取得した場合、すでに URL エンコード済み。
        そのまま使うとダブルエンコードになるため、一度デコードしてから再エンコードする。
        """
        raw = self.encrypted_login_password or ""
        # api_login から取得した encryptedPassword は既に URL エンコード済み
        # 一度デコードしてから再エンコードすることで正しい値にする
        decoded = urllib.parse.unquote(raw.replace(" ", "+"))
        enc = urllib.parse.quote(decoded, safe="")
        uid = urllib.parse.quote(self.user_id or "", safe="")
        return (
            f"{self.base_url}/uprx/up/pk/pky501/Pky50101.xhtml"
            f"?webApiLoginInfo=%7B%22password%22%3A%22%22%2C%22autoLoginAuthCd%22%3A%22%22"
            f"%2C%22encryptedPassword%22%3A%22{enc}%22"
            f"%2C%22userId%22%3A%22{uid}%22"
            f"%2C%22parameterMap%22%3A%22%22%7D"
        )

    async def _mobile_login(self) -> BeautifulSoup:
        """モバイルログイン"""
        if not self.user_id or not self.encrypted_login_password:
            raise GakuenPermissionError(
                "ユーザーIDと暗号化されたパスワードが必要です",
                error_code="MISSING_USER_ID_OR_PASSWORD",
            )
        # Build webApiLoginInfo as a dict and let aiohttp handle encoding
        # encrypted_login_password from api_login is already URL-encoded, so decode it first
        decoded_password = urllib.parse.unquote(self.encrypted_login_password.replace(" ", "+"))
        web_api_login_info = {
            "password": "",
            "autoLoginAuthCd": "",
            "encryptedPassword": decoded_password,
            "userId": self.user_id,
            "parameterMap": "",
        }
        login_url = f"{self.base_url}/uprx/up/pk/pky501/Pky50101.xhtml"
        params = {"webApiLoginInfo": json.dumps(web_api_login_info)}
        to_index_url = f"{self.base_url}/uprx/up/pk/pky501/Pky50101.xhtml"
        data = {
            "pmPage:loginForm": "pmPage:loginForm",
            "pmPage:loginForm:autoLogin": "",
            "pmPage:loginForm:userId_input": "",
            "pmPage:loginForm:password": "",
            "javax.faces.ViewState": "stateless",
            "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
        }
        try:
            # Reset mobile IDs for fresh extraction on each login
            self._ids.calendar_id = None
            self._ids.accordion_id = None
            self._ids.kadai_tab_link_id = None
            self._ids.menu_button_id = None

            await self._http.fetch(login_url, method="GET", params=params)
            soup = await self._http.fetch(to_index_url, method="POST", data=data)
            if not isinstance(soup, BeautifulSoup):
                raise GakuenAPIError(
                    "システムのホームページの取得に失敗しました",
                    error_code="HOME_PAGE_FETCH_ERROR",
                )
            self._state.update_from_soup(soup)
            if not self._state.rx_tokens:
                error_span = soup.find("span", class_="ui-messages-error-detail")
                page_title = getattr(soup.find("title"), "text", "unknown")
                auth_err = error_span.text.strip() if error_span else None
                logging.warning(
                    f"[mobile_login] user={self.user_id} "
                    f"page_title={page_title!r} auth_err={auth_err!r}"
                )
                if auth_err:
                    raise GakuenLoginError(
                        f"モバイルログイン認証エラー: {auth_err}",
                        error_code="MOBILE_LOGIN_AUTH_ERROR",
                    )
                raise GakuenAPIError(
                    "セッション情報の抽出に失敗しました",
                    error_code="SESSION_EXTRACT_ERROR",
                )
            if soup.find("span", class_="questTitle"):  # 重要アンケートがある場合
                # Dynamically find the home navigation button in the questionnaire menu form
                menu_form = soup.find("form", id="pmPage:menuForm")
                if isinstance(menu_form, Tag):
                    for inp in menu_form.find_all("input"):
                        name = inp.get("name", "")
                        if isinstance(name, str) and "menuBtnF" in name:
                            self._ids.menu_button_id = name
                            break
                soup = await self._to_mobile_home_page()
                self._state.update_from_soup(soup)
            # Fix 2: dynamically extract mobile component IDs from login response
            self._ids.extract_mobile_ids(soup)
            return soup
        except Exception as e:
            if isinstance(e, GakuenAPIError):
                raise
            raise GakuenAPIError(
                f"モバイルログイン処理中に予想外エラーが発生しました: {str(e)}",
                error_code="UNEXPECTED_MOBILE_LOGIN_ERROR",
            )

    async def _to_home_page(self) -> BeautifulSoup:
        """ホームページに移動"""
        home_url = f"{self.base_url}/uprx/up/bs/bsa001/Bsa00101.xhtml"
        data = {
            "headerForm": "headerForm",
            "rx-token": self._state.rx_tokens["token"],
            "rx-loginKey": self._state.rx_tokens["loginKey"],
            "rx-deviceKbn": self._state.rx_tokens["deviceKbn"],
            "rx-loginType": self._state.rx_tokens["loginType"],
            "headerForm:logo": "",
            "javax.faces.ViewState": self._state.view_state,
            "rx.sync.source": "headerForm:logo",
        }
        try:
            soup = await self._http.fetch(home_url, method="POST", data=data)
            if not isinstance(soup, BeautifulSoup):
                raise GakuenAPIError(
                    "ホームページの取得に失敗しました",
                    error_code="HOME_PAGE_ERROR",
                )
            return soup
        except Exception as e:
            raise GakuenAPIError(
                f"ホームページ移動中に予想外エラーが発生しました: {str(e)}",
                error_code="HOME_PAGE_NAVIGATION_ERROR",
            )

    async def _to_mobile_home_page(self) -> BeautifulSoup:
        """モバイルホームページに移動"""
        mobile_home_url = f"{self.base_url}/uprx/up/bs/bsc505/Bsc50501.xhtml"
        btn = self._ids.menu_button_id or "pmPage:menuForm:j_idt36:0:menuBtnF"
        data = {
            "pmPage:menuForm": "pmPage:menuForm",
            "rx-token": self._state.rx_tokens["token"],
            "rx-loginKey": self._state.rx_tokens["loginKey"],
            "rx-deviceKbn": self._state.rx_tokens["deviceKbn"],
            "rx-loginType": self._state.rx_tokens["loginType"],
            btn: "",
            "javax.faces.ViewState": self._state.view_state,
            "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
            "rx.sync.source": btn,
        }
        try:
            soup = await self._http.fetch(mobile_home_url, method="POST", data=data)
            if not isinstance(soup, BeautifulSoup):
                raise GakuenAPIError(
                    "モバイルホームページの取得に失敗しました",
                    error_code="MOBILE_HOME_PAGE_ERROR",
                )
            return soup
        except Exception as e:
            raise GakuenAPIError(
                f"モバイルホームページ移動中に予想外エラーが発生しました: {str(e)}",
                error_code="MOBILE_HOME_PAGE_NAVIGATION_ERROR",
            )

    async def _fetch_class_list(self, soup: BeautifulSoup) -> None:
        """クラス一覧を取得

        Args:
            soup: BeautifulSoupオブジェクト

        Raises:
            GakuenDataError: クラスデータの抽出に失敗した場合
        """

        def __extract_class_tags(soup: BeautifulSoup) -> list:
            """クラスタグを抽出"""
            try:
                return [
                    ({class_attr[1]: tag_data.text}, index)
                    for index, lesson_head in enumerate(
                        soup.find_all("div", class_="lessonHead")
                    )
                    if isinstance(lesson_head, Tag)
                    for tag_data in lesson_head.find_all("span", class_="signLesson")
                    if isinstance(tag_data, Tag)
                    and (class_attr := tag_data.get("class"))
                    and isinstance(class_attr, list)
                    and len(class_attr) > 1
                ]
            except (AttributeError, IndexError) as e:
                raise GakuenDataError(
                    f"クラスタグ抽出エラー: {str(e)}",
                    error_code="CLASS_TAG_EXTRACTION_ERROR",
                )

        def __extract_lesson_info(lesson_main) -> Optional[dict]:
            """授業情報を抽出"""
            try:
                p_tag = lesson_main.find("p")
                if not p_tag:
                    return None

                span_tag = p_tag.find("span")
                span_text = span_tag.text if span_tag else ""

                # 授業タイトルを正規化
                lesson_title = (
                    p_tag.text.strip().replace(span_text, "").replace("\u3000", " ")
                )

                lesson_detail = lesson_main.find("div", class_="lessonDetail")
                if not lesson_detail:
                    return None

                # 教師情報を取得
                teachers = " / ".join(
                    teacher.text.replace("\u3000", " ")
                    for teacher in lesson_detail.find_all("a")
                )

                # 教室情報を取得
                classroom = __extract_classroom_info(lesson_detail, span_text)

                return {
                    "title": lesson_title,
                    "teachers": teachers,
                    "classroom": classroom,
                }
            except Exception as e:
                # 個別のレッスン解析エラーはログに記録するが、処理は続行
                logging.warning(f"レッスン情報抽出エラー: {str(e)}")
                return None

        def __extract_classroom_info(lesson_detail: Tag, span_text: str) -> str:
            """教室情報を抽出"""
            if lesson_detail.find("label"):
                # 教室変更がある場合
                class_divs = lesson_detail.find_all("div")
                if len(class_divs) >= 3:
                    return f"変更: {class_divs[2].text.strip()} → {class_divs[0].text.strip()}"
            else:
                # 通常の教室情報
                classroom = " / ".join(
                    div.text.strip() for div in lesson_detail.find_all("div")
                )
                if span_text:
                    classroom = f"{span_text}: {classroom}"
                return classroom

            return ""

        def __parse_class_details(soup: BeautifulSoup) -> None:
            """クラス詳細情報を解析"""
            for lesson_main in soup.find_all("div", class_="lessonMain"):
                lesson_data = __extract_lesson_info(lesson_main)
                if lesson_data and lesson_data["title"] not in self._state.class_list:
                    self._state.class_list[lesson_data["title"]] = {
                        "lessonTeachers": lesson_data["teachers"],
                        "lessonClass": lesson_data["classroom"],
                    }

        def __attach_tags_to_classes(class_tags: list) -> None:
            """タグ情報をクラスデータに追加"""
            course_keys = list(self._state.class_list.keys())
            for tag_data, index in class_tags:
                if index < len(course_keys):
                    course_name = course_keys[index]
                    self._state.class_list[course_name].setdefault("tags", []).append(tag_data)

        # クラスタグを取得
        class_tags = __extract_class_tags(soup)

        # クラス詳細を解析
        __parse_class_details(soup)

        # タグ情報をクラスデータに追加
        __attach_tags_to_classes(class_tags)

        # 教室情報を Redis にキャッシュ
        try:
            from tutnext.config import redis
            for course_name, info in self._state.class_list.items():
                if info.get("lessonClass"):
                    await redis.set(
                        f"room:{course_name}", info["lessonClass"], ex=_ROOM_CACHE_TTL
                    )
        except Exception:
            pass  # Redis 障害時はキャッシュをスキップ

    async def _call_first_setting(self) -> dict:
        """firstSetting API を呼び出す (api_login 後に必須)

        Returns:
            dict: firstSetting のレスポンスデータ（失敗時は空辞書）
        """
        url = f"{self.base_url}/uprx/webapi/up/ap/Apa001Resource/firstSetting"
        _json: dict[str, Any] = {
            "productCd": "ap",
            "subProductCd": "apa",
            "loginUserId": self.user_id,
            "encryptedLoginPassword": self.encrypted_login_password,
            "langCd": "",
            "data": {},
        }
        try:
            data = await self._http.fetch(
                url, method="POST", _json=_json, response_type="json"
            )
            return data.get("data", {}) if isinstance(data, dict) else {}
        except GakuenAPIError:
            return {}
