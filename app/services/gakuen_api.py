# app/services/gakuen_api.py
import re
import aiohttp
import json
import urllib.parse

from bs4 import BeautifulSoup
from datetime import date, datetime, timedelta


class GakuenAPIError(Exception):
    def __init__(self, message):
        super().__init__(message)


class GakuenAPI:
    def __init__(
        self, userId: str, password: str, hosts: str, encryptedLoginPassword: str = None
    ) -> None:
        """GakuenAPI

        Args:
            userId (str): user_id (学籍番号)
            password (str): password (パスワード)
            hosts (str): 大学のアドレス　例: https://you.university.url
        """
        self.userId = userId
        self.password = password
        self.hosts = hosts
        self.j_idt: str = None
        self.j_idt_kaitai: str = None
        self.rx: dict = {}
        self.view_state: str = None
        self.class_list: dict = {}
        self.encryptedLoginPassword: str = encryptedLoginPassword
        self.s = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

    async def close(self):
        await self.s.close()

    async def login(self) -> dict:
        """login ログイン

        Raises:
            GakuenAPIError: ログインエラー

        Returns:
            dict: クラスデータ
        """
        async with self.s.post(
            f"{self.hosts}/uprx/up/pk/pky001/Pky00101.xhtml",
            data={
                "loginForm": "loginForm",
                "loginForm:userId": self.userId,
                "loginForm:password": self.password,
                "loginForm:loginButton": "",
                "javax.faces.ViewState": "stateless",
            },
        ) as r:
            if r.status != 200:
                raise GakuenAPIError(
                    "ログインエラー: ログインページが取得できませんでした"
                )
            soup = BeautifulSoup(await r.text(), "html.parser")
            if error_msg := soup.find("span", class_="ui-messages-error-detail"):
                raise GakuenAPIError(f"ログインエラー: {error_msg.text}")
            for input_tag in soup.find_all("input"):
                name = input_tag.get("name")
                value = input_tag.get("value")
                if name == "rx-token":
                    self.rx["token"] = value
                elif name == "rx-loginKey":
                    self.rx["loginKey"] = value
                elif name == "rx-deviceKbn":
                    self.rx["deviceKbn"] = value
                elif name == "rx-loginType":
                    self.rx["loginType"] = value
                elif name == "javax.faces.ViewState":
                    self.view_state = value
            if soup.find("dt", class_="msgArea"):  # 重要アンケートがある場合
                soup = await self.to_home_page()
                self.rx["token"] = soup.find("input", {"name": "rx-token"}).get("value")
                self.view_state = soup.find(
                    "input", {"name": "javax.faces.ViewState"}
                ).get("value")
            self.j_idt = (
                soup.find_all("script", type="text/javascript")[34]
                .get("id")
                .split(":")[1]
            )
            self.j_idt_kaitai = (
                soup.find("div", id="portalSupport")
                .find("li")
                .find("a")
                .get("href")
                .lstrip("#funcForm:")
            )
            # クラス tag
            classs_tag = [
                ({tag_data.get("class")[1]: tag_data.text}, index)
                for index, h in enumerate(soup.find_all("div", class_="lessonHead"))
                for tag_data in h.find_all("span", class_="signLesson")
            ]
            # クラス 教室変更
            for c in soup.find_all("div", class_="lessonMain"):
                p_tag = c.find("p")
                span_tag = p_tag.find("span")
                span_text = span_tag.text if span_tag else ""
                lessonTitle = (
                    p_tag.text.strip().replace(span_text, "").replace("\u3000", " ")
                )
                if lessonTitle in self.class_list:
                    continue

                lessonDetail = c.find("div", class_="lessonDetail")
                lessonTeachers = " / ".join(
                    teacher.text.replace("\u3000", " ")
                    for teacher in lessonDetail.find_all("a")
                )

                if lessonDetail.find("label"):
                    __Class = lessonDetail.find_all("div")
                    lessonClass = (
                        f"変更: {__Class[2].text.strip()} → {__Class[0].text.strip()}"
                    )
                else:
                    lessonClass = " / ".join(
                        _Class.text.strip() for _Class in lessonDetail.find_all("div")
                    )
                    if span_text:
                        lessonClass = f"{span_text}: {lessonClass}"

                self.class_list[lessonTitle] = {
                    "lessonTeachers": lessonTeachers,
                    "lessonClass": lessonClass,
                }

            if not self.class_list:
                raise GakuenAPIError("クラスデータが取得できませんでした")

            course_keys = list(self.class_list.keys())
            for data, index in classs_tag:
                course_name = course_keys[index]
                self.class_list[course_name].setdefault("tags", []).append(data)
        return self.class_list

    async def webapi_login(self) -> dict:
        """webapi login ログイン

        Raises:
            GakuenAPIError: ログインエラー

        Returns:
            dict: ログインデータ
        """
        async with self.s.post(
            f"{self.hosts}/uprx/webapi/up/pk/Pky001Resource/login",
            json={
                "data": {
                    "loginUserId": self.userId,
                    "plainLoginPassword": self.password,
                }
            },
        ) as r:
            try:
                data = await r.text()
                if "innerInfo" in data:
                    soup = BeautifulSoup(await r.text(), "html.parser")
                    if error_msg := soup.find("p", class_="innerInfo"):
                        raise GakuenAPIError(
                            "ログインエラー: "
                            + error_msg.text.replace("\n", "").replace("\t", "")
                        )
                res = json.loads(urllib.parse.unquote(data).replace("\u3000", " "))
            except json.JSONDecodeError:
                raise GakuenAPIError(
                    "ログインエラー: ログインページが取得できませんでした", data
                )
            if r.status != 200:
                raise GakuenAPIError(
                    f"ログインエラー: {''.join(res['statusDto']['messageList'])}"
                )
            self.encryptedLoginPassword = res["data"]["encryptedPassword"]
            return res["data"]

    async def webapi_logout(self) -> dict:
        """webapi logout ログアウト

        Raises:
            GakuenAPIError: ログアウトエラー

        Returns:
            dict: ログアウトデータ
        """
        async with self.s.post(
            f"{self.hosts}/uprx/webapi/up/pk/Pky002Resource/logout",
            json={
                "subProductCd": "apa",
                "plainLoginPassword": "",
                "loginUserId": self.userId,
                "langCd": "",
                "productCd": "ap",
                "encryptedLoginPassword": self.encryptedLoginPassword,
            },
        ) as r:
            res = json.loads(urllib.parse.unquote(await r.text()))
            if r.status != 200:
                raise GakuenAPIError(
                    f"ログアウトエラー: {''.join(res['statusDto']['messageList'])}"
                )
            return res["data"]

    async def get_new_read_keiji_list(
        self, userId: str = None, encryptedLoginPassword: str = None
    ) -> dict:
        """未読掲示データ取得 (webapi loginが必要) Student Only

        Args:
            encryptedLoginPassword (str, optional): 暗号化されたパスワード. Defaults to None.

        Returns:
            dict: 掲示データ

        """
        if userId:
            self.userId = userId
        if encryptedLoginPassword:
            self.encryptedLoginPassword = encryptedLoginPassword
        async with self.s.get(
            f"{self.hosts}/uprx/up/pk/pky501/Pky50101.xhtml?webApiLoginInfo=%7B%22funcId%22%3A%22Bsd507%22%2C%22autoLoginAuthCd%22%3A%22%22%2C%22encryptedPassword%22%3A%22{self.encryptedLoginPassword}%22%2C%22userId%22%3A%22{self.userId}%22%2C%22parameterMap%22%3A%22%22%2C%22password%22%3A%22%22%2C%22formId%22%3A%22Bsd50701%22%7D",
        ) as r:
            async with self.s.post(
                f"{self.hosts}/uprx/up/pk/pky501/Pky50101.xhtml",
                data={
                    "pmPage:loginForm": "pmPage:loginForm",
                    "pmPage:loginForm:autoLogin": "",
                    "pmPage:loginForm:userId_input": "",
                    "pmPage:loginForm:password": "",
                    "javax.faces.ViewState": "stateless",
                    "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
                },
            ) as r:
                soup = BeautifulSoup(await r.text(), "html.parser")
                for input_tag in soup.find_all("input"):
                    name = input_tag.get("name")
                    value = input_tag.get("value")
                    if name == "rx-token":
                        self.rx["token"] = value
                    elif name == "rx-loginKey":
                        self.rx["loginKey"] = value
                    elif name == "rx-deviceKbn":
                        self.rx["deviceKbn"] = value
                    elif name == "rx-loginType":
                        self.rx["loginType"] = value
                    elif name == "javax.faces.ViewState":
                        self.view_state = value
                async with self.s.post(
                    f"{self.hosts}/uprx/up/bs/bsd507/Bsd50701.xhtml",
                    data={
                        "javax.faces.partial.ajax": True,
                        "javax.faces.source": "pmPage:funcForm:j_idt103:2:j_idt145",
                        "javax.faces.partial.execute": "pmPage:funcForm:j_idt103:2:j_idt145",
                        "javax.faces.partial.render": "pmPage:funcForm:mainContent",
                        "javax.faces.behavior.event": "click",
                        "javax.faces.partial.event": "click",
                        "pmPage:funcForm:j_idt103_newTab": "pmPage:funcForm:j_idt103:2:j_idt104",
                        "pmPage:funcForm:j_idt103_tabindex": "2",
                        "pmPage:funcForm": "pmPage:funcForm",
                        "rx-token": self.rx["token"],
                        "rx-loginKey": self.rx["loginKey"],
                        "rx-deviceKbn": self.rx["deviceKbn"],
                        "rx-loginType": self.rx["loginType"],
                        "pmPage:funcForm:keyword_input": "",
                        "pmPage:funcForm:j_idt98": "",
                        "pmPage:funcForm:j_idt95_collapsed": True,
                        "pmPage:funcForm:j_idt103:0:j_idt109_active": "0,1,2,3,4,5,-1",
                        "pmPage:funcForm:j_idt103_activeIndex": "2",
                        "javax.faces.ViewState": self.view_state,
                        "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
                    },
                ) as r:
                    soup = BeautifulSoup(await r.text(), "xml")
                    # 找到包含未读公告的 update 标签
                    # if main_key_content := soup.find("update", {"id": "pmPage:funcForm:j_id6"}):
                    #     # 解析 CDATA 内容
                    #     content_soup = BeautifulSoup(main_key_content.text, "html.parser")
                    #     # 找到所有 input 标签
                    #     for input_tag in content_soup.find_all("input"):
                    #         name = input_tag.get("name")
                    #         value = input_tag.get("value")
                    #         if name == "rx-token":
                    #             self.rx["token"] = value
                    #         elif name == "rx-loginKey":
                    #             self.rx["loginKey"] = value
                    #         elif name == "rx-deviceKbn":
                    #             self.rx["deviceKbn"] = value
                    #         elif name == "rx-loginType":
                    #             self.rx["loginType"] = value
                    #         elif name == "javax.faces.ViewState":
                    #             self.view_state = value
                    if main_content := soup.find(
                        "update", {"id": "pmPage:funcForm:mainContent"}
                    ):
                        # 解析 CDATA 内容
                        content_soup = BeautifulSoup(main_content.text, "html.parser")

                        if from_2 := content_soup.find(
                            "div", {"id": "pmPage:funcForm:j_idt103:2:j_idt104"}
                        ):
                            # 找到所有未读公告
                            new_read_list = from_2.select(
                                "li[class*='listIndent'][class*='newRead']"
                            )

                            announcements = []
                            for item in new_read_list:
                                if link := item.find("a"):
                                    announcements.append(
                                        {
                                            "title": link.text.replace("\u3000", " "),
                                            "func_form": link.get("id", ""),
                                            "is_important": "importantRead"
                                            in item.get("class", []),
                                        }
                                    )
                            return announcements
                    return []

    async def get_later_user_schedule(
        self, userId: str = None, encryptedLoginPassword: str = None
    ) -> dict:
        """後日ユーザースケジュール取得 (webapi loginが必要) Student Only

        Returns:
            dict: ユーザースケジュール（日付情報、終日活動、課程情報を含む）
        """
        if userId:
            self.userId = userId
        if encryptedLoginPassword:
            self.encryptedLoginPassword = encryptedLoginPassword
        async with self.s.get(
            f"{self.hosts}/uprx/up/pk/pky501/Pky50101.xhtml?webApiLoginInfo=%7B%22password%22%3A%22%22%2C%22autoLoginAuthCd%22%3A%22%22%2C%22encryptedPassword%22%3A%22{self.encryptedLoginPassword}%22%2C%22userId%22%3A%22{self.userId}%22%2C%22parameterMap%22%3A%22%22%7D",
        ) as r:
            if r.status != 200:
                raise GakuenAPIError(
                    "ログインエラー: ログインページが取得できませんでした"
                )
        async with self.s.post(
            f"{self.hosts}/uprx/up/pk/pky501/Pky50101.xhtml",
            data={
                "pmPage:loginForm": "pmPage:loginForm",
                "pmPage:loginForm:autoLogin": "",
                "pmPage:loginForm:userId_input": "",
                "pmPage:loginForm:password": "",
                "javax.faces.ViewState": "stateless",
                "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
            },
        ) as r:
            if r.status != 200:
                raise GakuenAPIError(
                    "ログインエラー: ログインページが取得できませんでした"
                )
            soup = BeautifulSoup(await r.text(), "html.parser")
            if error_msg := soup.find("span", class_="ui-messages-error-detail"):
                raise GakuenAPIError(f"ログインエラー: {error_msg.text}")
            for input_tag in soup.find_all("input"):
                name = input_tag.get("name")
                value = input_tag.get("value")
                if name == "rx-token":
                    self.rx["token"] = value
                elif name == "rx-loginKey":
                    self.rx["loginKey"] = value
                elif name == "rx-deviceKbn":
                    self.rx["deviceKbn"] = value
                elif name == "rx-loginType":
                    self.rx["loginType"] = value
                elif name == "javax.faces.ViewState":
                    self.view_state = value
        async with self.s.post(
            f"{self.hosts}/uprx/up/bs/bsa501/Bsa50101.xhtml",
            data={
                "javax.faces.partial.ajax": True,
                "javax.faces.source": "pmPage:funcForm:j_idt98",
                "javax.faces.partial.execute": "pmPage:funcForm:j_idt98",
                "javax.faces.partial.render": "pmPage:funcForm:mainContent",
                "pmPage:funcForm:j_idt98": "pmPage:funcForm:j_idt98",
                "pmPage:funcForm": "pmPage:funcForm",
                "rx-token": self.rx["token"],
                "rx-loginKey": self.rx["loginKey"],
                "rx-deviceKbn": self.rx["deviceKbn"],
                "rx-loginType": self.rx["loginType"],
                "pmPage:funcForm:j_idt107_active": "0,1",
                "javax.faces.ViewState": self.view_state,
                "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
            },
        ) as r:
            soup = BeautifulSoup(await r.text(), "xml")

            result = {"date_info": {}, "all_day_events": [], "time_table": []}

            # 更新rx-token和ViewState (如果在响应中存在)
            for update_tag in soup.find_all("update"):
                if "j_id6" in update_tag.get("id", ""):
                    token_soup = BeautifulSoup(update_tag.text, "html.parser")
                    for input_tag in token_soup.find_all("input"):
                        name = input_tag.get("name")
                        value = input_tag.get("value")
                        if name == "rx-token":
                            self.rx["token"] = value
                elif "ViewState" in update_tag.get("id", ""):
                    self.view_state = update_tag.text

            # 获取主内容
            main_content = soup.find("update", {"id": "pmPage:funcForm:mainContent"})
            if not main_content:
                return result

            content_soup = BeautifulSoup(main_content.text, "html.parser")

            # 1. 日期信息
            date_display = content_soup.find("span", class_="dateDisp")
            if date_display:
                raw_date = date_display.text.strip()
                result["date_info"] = {
                    "date": raw_date.split("(")[0],
                    "day_of_week": raw_date.split("(")[1].rstrip(")"),
                }

            # 2. 终日活动 (全天事件)
            all_day_panel = content_soup.find("div", class_="syujitsuPanel")
            if all_day_panel:
                all_day_events = all_day_panel.find_all("a", recursive=True)
                for event in all_day_events:
                    result["all_day_events"].append(
                        {
                            "title": event.text.strip().replace("\u3000", " "),
                            "id": event.get("id", ""),
                            "is_important": "重要" in event.text
                            or "【重要】" in event.text,
                        }
                    )

            # 3. 课程信息 (时间表)
            time_panel = content_soup.find("div", id=lambda x: x and "j_idt177" in x)
            if time_panel:
                class_items = time_panel.select("li")
                for item in class_items:
                    class_data = {}

                    # 时间
                    time_header = item.find("div", class_="jknbtHdr")
                    if time_header:
                        class_data["time"] = time_header.text.strip()
                        # 检查是否有教室变更标记
                        change_room_tag = time_header.find("span", class_="signLesson")
                        if change_room_tag:
                            class_data["special_tag"] = change_room_tag.text.strip()
                            class_data["time"] = class_data["time"].replace(
                                class_data["special_tag"], ""
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

                    # 课程名称
                    class_name = item.find("span", class_="jugyoName")
                    if class_name:
                        class_data["name"] = class_name.text.strip().replace(
                            "\u3000", " "
                        )

                    # 教师
                    teachers = []
                    teacher_tags = item.find_all("a", class_="tantoKyoin")
                    if teacher_tags:
                        for teacher in teacher_tags:
                            teachers.append(teacher.text.strip().replace("\u3000", " "))
                        class_data["teachers"] = teachers

                    # 教室
                    class_details = item.find("div", class_="jknbtDtl")
                    if class_details:
                        # 获取常规教室信息（当前教室）
                        room_divs = class_details.find_all("div", recursive=False)
                        for div in room_divs:
                            if (
                                not div.get("id")
                                and not div.get("class")
                                and "教室" in div.text
                            ):
                                class_data["room"] = div.text.strip()

                        # 获取变更前教室
                        change_room_div = class_details.find(
                            "div", {"id": lambda x: x and "j_idt248" in x}
                        )
                        if change_room_div:
                            previous_room = change_room_div.find("div")
                            if previous_room:
                                class_data["previous_room"] = previous_room.text.strip()

                    if class_data:
                        result["time_table"].append(class_data)

            return result

    async def get_user_kadai(
        self, userId: str = None, encryptedLoginPassword: str = None
    ) -> dict:
        """課題データ取得 (webapi loginが必要) Student Only

        Args:
            userId (str, optional): 学籍番号. Defaults to None.
            encryptedLoginPassword (str, optional): 暗号化されたパスワード. Defaults to None.

        Returns:
            dict: 課題データ
        """
        if userId:
            self.userId = userId
        if encryptedLoginPassword:
            self.encryptedLoginPassword = encryptedLoginPassword
        async with self.s.get(
            f"{self.hosts}/uprx/up/pk/pky501/Pky50101.xhtml?webApiLoginInfo=%7B%22password%22%3A%22%22%2C%22autoLoginAuthCd%22%3A%22%22%2C%22encryptedPassword%22%3A%22{self.encryptedLoginPassword}%22%2C%22userId%22%3A%22{self.userId}%22%2C%22parameterMap%22%3A%22%22%7D",
        ) as r:
            if r.status != 200:
                raise GakuenAPIError(
                    "ログインエラー: ログインページが取得できませんでした"
                )
        async with self.s.post(
            f"{self.hosts}/uprx/up/pk/pky501/Pky50101.xhtml",
            data={
                "pmPage:loginForm": "pmPage:loginForm",
                "pmPage:loginForm:autoLogin": "",
                "pmPage:loginForm:userId_input": "",
                "pmPage:loginForm:password": "",
                "javax.faces.ViewState": "stateless",
                "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
            },
        ) as r:
            if r.status != 200:
                raise GakuenAPIError(
                    "ログインエラー: ログインページが取得できませんでした"
                )
            soup = BeautifulSoup(await r.text(), "html.parser")
            if error_msg := soup.find("span", class_="ui-messages-error-detail"):
                raise GakuenAPIError(f"ログインエラー: {error_msg.text}")
            for input_tag in soup.find_all("input"):
                name = input_tag.get("name")
                value = input_tag.get("value")
                if name == "rx-token":
                    self.rx["token"] = value
                elif name == "rx-loginKey":
                    self.rx["loginKey"] = value
                elif name == "rx-deviceKbn":
                    self.rx["deviceKbn"] = value
                elif name == "rx-loginType":
                    self.rx["loginType"] = value
                elif name == "javax.faces.ViewState":
                    self.view_state = value
        async with self.s.post(
            f"{self.hosts}/uprx/up/bs/bsa501/Bsa50101.xhtml",
            data={
                "pmPage:funcForm": "pmPage:funcForm",
                "rx-token": self.rx["token"],
                "rx-loginKey": self.rx["loginKey"],
                "rx-deviceKbn": self.rx["deviceKbn"],
                "rx-loginType": self.rx["loginType"],
                "pmPage:funcForm:j_idt107_active": "0,1",
                "javax.faces.ViewState": self.view_state,
                "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
                "rx.sync.source": "pmPage:funcForm:j_idt107:j_idt126",
                "pmPage:funcForm:j_idt107:j_idt126": "pmPage:funcForm:j_idt107:j_idt126",
            },
        ) as r:
            soup = BeautifulSoup(await r.text(), "html.parser")
            if error_msg := soup.find("span", class_="ui-messages-error-detail"):
                raise GakuenAPIError(f"ログインエラー: {error_msg.text}")
            for input_tag in soup.find_all("input"):
                name = input_tag.get("name")
                value = input_tag.get("value")
                if name == "rx-token":
                    self.rx["token"] = value
                elif name == "rx-loginKey":
                    self.rx["loginKey"] = value
                elif name == "rx-deviceKbn":
                    self.rx["deviceKbn"] = value
                elif name == "rx-loginType":
                    self.rx["loginType"] = value
                elif name == "javax.faces.ViewState":
                    self.view_state = value
            kadai_list = []
            if main_content := soup.find("div", class_="mainContent"):
                for item in main_content.find_all("li"):
                    if link := item.find("a"):
                        kaidai_id = link.get("id", "")
                        if "j_idt81" not in kaidai_id or not kaidai_id:
                            continue
                        async with self.s.post(
                            f"{self.hosts}/uprx/up/bs/bsa501/Bsa50102.xhtml",
                            data={
                                "pmPage:funcForm": "pmPage:funcForm",
                                "rx-token": self.rx["token"],
                                "rx-loginKey": self.rx["loginKey"],
                                "rx-deviceKbn": self.rx["deviceKbn"],
                                "javax.faces.ViewState": self.view_state,
                                "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
                                "rx.sync.source": kaidai_id,
                                kaidai_id: kaidai_id,
                            },
                        ) as r:
                            soup = BeautifulSoup(await r.text(), "html.parser")
                            for input_tag in soup.find_all("input"):
                                name = input_tag.get("name")
                                value = input_tag.get("value")
                                if name == "rx-token":
                                    self.rx["token"] = value
                                elif name == "rx-loginKey":
                                    self.rx["loginKey"] = value
                                elif name == "rx-deviceKbn":
                                    self.rx["deviceKbn"] = value
                                elif name == "rx-loginType":
                                    self.rx["loginType"] = value
                                elif name == "javax.faces.ViewState":
                                    self.view_state = value
                            # 授業インフォ
                            kadai_data = {}
                            if class_info := soup.find("div", class_="jugyoInfo"):
                                lesson_title_detail = class_info.find_all(
                                    "span", class_="nendoGakkiDisp"
                                )
                                kadai_data["courseSemesterName"] = lesson_title_detail[
                                    0
                                ].text
                                kadai_data["courseName"] = lesson_title_detail[1].text
                                kadai_data["courseId"] = re.search(
                                    r"\[(.*?)\]", class_info.text
                                ).group(1)
                            # 課題インフォ
                            if kadai_info := soup.find("ul", class_="tableData"):
                                if kadai_group := kadai_info.find(
                                    "label", text="課題グループ"
                                ).parent.find_next_sibling("li"):
                                    kadai_data["group"] = kadai_group.text.strip()
                                if kadai_title := kadai_info.find(
                                    "label", text="課題名"
                                ).parent.find_next_sibling("li"):
                                    kadai_data["title"] = kadai_title.text.strip()
                                if kadai_public_period := kadai_info.find(
                                    "label", string=lambda s: s and "課題公開期間" in s
                                ):
                                    if period_li := kadai_public_period.parent.find_next_sibling(
                                        "li"
                                    ):
                                        spans = period_li.find_all("span")
                                        if len(spans) >= 3:
                                            kadai_data["publish_start"] = spans[
                                                0
                                            ].text.strip()
                                            kadai_data["publish_end"] = spans[
                                                2
                                            ].text.strip()
                                            if due_date := re.search(
                                                r"(\d{4}/\d{2}/\d{2})", spans[2].text
                                            ):
                                                kadai_data["dueDate"] = due_date.group(
                                                    1
                                                ).replace("/", "-")
                                            if due_time := re.search(
                                                r"(\d{2}:\d{2})", spans[2].text
                                            ):
                                                kadai_data["dueTime"] = due_time.group(
                                                    1
                                                )
                                if kadai_submit_period := kadai_info.find(
                                    "label", string=lambda s: s and "課題提出期間" in s
                                ):
                                    if submit_li := kadai_submit_period.parent.find_next_sibling(
                                        "li"
                                    ):
                                        spans = submit_li.find_all("span")
                                        if len(spans) >= 3:
                                            kadai_data["submit_start"] = spans[
                                                0
                                            ].text.strip()
                                            kadai_data["submit_end"] = spans[
                                                2
                                            ].text.strip()
                                if kadai_content := kadai_info.find(
                                    "label", text="課題内容"
                                ).parent.find_next_sibling("li"):
                                    kadai_data["description"] = (
                                        kadai_content.text.strip()
                                    )
                                if kadai_proposed_method := kadai_info.find(
                                    "li", string=lambda s: s and "課題提出方法" in s
                                ):
                                    if method_li := kadai_proposed_method.find_next_sibling(
                                        "li"
                                    ):
                                        kadai_data["proposed_method"] = (
                                            method_li.text.strip()
                                        )
                                        if min_length := method_li.find_all(
                                            "span", class_="smallInput"
                                        ):
                                            kadai_data["min_length"] = min_length[
                                                0
                                            ].text.strip()
                                            kadai_data["max_length"] = min_length[
                                                1
                                            ].text.strip()
                                kadai_data["url"] = (
                                    f"{self.hosts}/uprx/up/pk/pky501/Pky50101.xhtml?webApiLoginInfo=%7B%22password%22%3A%22%22%2C%22autoLoginAuthCd%22%3A%22%22%2C%22encryptedPassword%22%3A%22{self.encryptedLoginPassword}%22%2C%22userId%22%3A%22{self.userId}%22%2C%22parameterMap%22%3A%22%22%7D"
                                )
                            kadai_list.append(kadai_data)
                        # 返回課題列表
                        async with self.s.post(
                            f"{self.hosts}/uprx/up/jg/jga505/Jga50503.xhtml",
                            data={
                                "pmPage:funcForm": "pmPage:funcForm",
                                "rx-token": self.rx["token"],
                                "rx-loginKey": self.rx["loginKey"],
                                "rx-deviceKbn": self.rx["deviceKbn"],
                                "javax.faces.ViewState": self.view_state,
                                "pmPage:funcForm:tstContent": "",
                                "pmPage:funcForm:tstComment": "",
                                "pmPage:funcForm:j_idt278:j_idt281": "",
                                "javax.faces.RenderKitId": "PRIMEFACES_MOBILE",
                                "rx.sync.source": "pmPage:funcForm:j_idt278:j_idt281",
                            },
                        ) as r:
                            soup = BeautifulSoup(await r.text(), "html.parser")
                            for input_tag in soup.find_all("input"):
                                name = input_tag.get("name")
                                value = input_tag.get("value")
                                if name == "rx-token":
                                    self.rx["token"] = value
                                elif name == "rx-loginKey":
                                    self.rx["loginKey"] = value
                                elif name == "rx-deviceKbn":
                                    self.rx["deviceKbn"] = value
                                elif name == "rx-loginType":
                                    self.rx["loginType"] = value
                                elif name == "javax.faces.ViewState":
                                    self.view_state = value
            return kadai_list

    async def class_bulletin(self, year: int = 0, semester: int = 0) -> dict:
        """クラスデータ取得 (webapi loginが必要) Student Only

        Args:
            year (int, optional): 開講年度. Defaults to 0.
            semester (int[0,1,2], optional): 学期[全学期0,春学期1,秋学期2]. Defaults to 0.

        Raises:
            GakuenAPIError: クラスデータ取得エラー

        Returns:
            dict: クラスデータ
        """
        async with self.s.post(
            f"{self.hosts}/uprx/webapi/up/ap/Apa004Resource/getJugyoKeijiMenuInfo",
            json={
                "plainLoginPassword": "",
                "data": {
                    "kaikoNendo": year,
                    "gakkiNo": semester,
                },
                "langCd": "",
                "encryptedLoginPassword": self.encryptedLoginPassword,
                "loginUserId": self.userId,
                "productCd": "ap",
                "subProductCd": "apa",
            },
        ) as r:
            res = json.loads(
                urllib.parse.unquote(await r.text())
                .replace("\u3000", " ")
                .replace("+", " ")
            )
            if r.status != 200:
                raise GakuenAPIError(
                    f"クラスデータ取得エラー: {''.join(res['statusDto']['messageList'])}"
                )
            return res["data"]

    async def class_data_info(self, class_data: dict) -> dict:
        """クラス掲示データ取得 (webapi loginが必要)

        Args:
            class_data (dict): クラスデータ

        Raises:
            GakuenAPIError: クラス掲示データ取得エラー

        Returns:
            dict: クラス掲示データ
        """
        async with self.s.post(
            f"{self.hosts}/uprx/webapi/up/ap/Apa004Resource/getJugyoDetailInfo",
            json={
                "loginUserId": self.userId,
                "langCd": "",
                "encryptedLoginPassword": self.encryptedLoginPassword,
                "productCd": "ap",
                "plainLoginPassword": "",
                "subProductCd": "apa",
                "data": class_data,
            },
        ) as r:
            res = json.loads(
                urllib.parse.unquote(await r.text())
                .replace("\u3000", " ")
                .replace("+", " ")
            )
            if r.status != 200:
                raise GakuenAPIError(
                    f"クラス掲示データ取得エラー: {''.join(res['statusDto']['messageList'])}"
                )
            return res["data"]

    async def month_data(self, year: int, month: int) -> list:
        """月の授業データを取得

        Args:
            year (int): 年
            month (int): 月

        Returns:
            list: 授業データ
        """
        month_start = str(int(datetime(year, month, 1).timestamp())) + "000"
        async with self.s.post(
            f"{self.hosts}/uprx/up/bs/bsa001/Bsa00101.xhtml",
            data={
                "javax.faces.partial.ajax": True,
                f"javax.faces.partial.render": f"funcForm:{self.j_idt}:content",
                f"funcForm:{self.j_idt}:content": f"funcForm:{self.j_idt}:content",
                f"funcForm:{self.j_idt}:content_start": month_start,
                f"funcForm:{self.j_idt}:content_end": month_start,
                "rx-token": self.rx["token"],
                "rx-loginKey": self.rx["loginKey"],
                "rx-deviceKbn": self.rx["deviceKbn"],
                "rx-loginType": self.rx["loginType"],
                "javax.faces.ViewState": self.view_state,
            },
        ) as r:
            r_text = await r.text()
            soup = BeautifulSoup(r_text, "xml")
            self.rx["token"] = re.findall('name="rx-token" value="(.*?)"', r_text)[0]
            course_list = json.loads(
                soup.find(
                    "update", {"id": f"funcForm:{self.j_idt}:content"}
                ).text.strip()
            )
            new_events = []
            for m in course_list["events"]:
                m["title"] = m["title"].replace("\u3000", " ").strip()
                if not m["allDay"]:  # 1時間半単位の授業
                    m["start"] = datetime.strptime(m["start"], "%Y-%m-%dT%H:%M:%S%z")
                    if m["title"] == "ホームゼミII 小林 英夫":
                        m["start"] = m["start"] - timedelta(days=1)
                    elif m["title"] == "ホームゼミII 出原 至道":
                        m["start"] = m["start"] + timedelta(days=3)
                    m["end"] = m["start"] + timedelta(minutes=90)
                else:  # 1日単位の授業
                    if m["className"] == "eventKeijiAd":
                        continue
                    t = datetime.strptime(m["start"], "%Y-%m-%dT%H:%M:%S%z")
                    t_e = datetime.strptime(m["end"], "%Y-%m-%dT%H:%M:%S%z")
                    m["start"] = date(t.year, t.month, t.day) + timedelta(days=1)
                    m["end"] = date(t_e.year, t_e.month, t_e.day)
                if m["title"] in self.class_list:  # 授業名が一致するものがあれば
                    m["teacher"] = self.class_list[m["title"]]["lessonTeachers"]
                    m["room"] = self.class_list[m["title"]]["lessonClass"]
                new_events.append(m)
        return new_events

    async def kadai_data(self) -> list:
        """課題データを取得

        Returns:
            list: 課題データ
        """
        async with self.s.post(
            f"{self.hosts}/uprx/up/bs/bsa001/Bsa00101.xhtml",
            data={
                "javax.faces.partial.ajax": True,
                "javax.faces.partial.render": f"funcForm:{self.j_idt_kaitai}",
                f"funcForm:{self.j_idt_kaitai}": f"funcForm:{self.j_idt_kaitai}",
                "rx-token": self.rx["token"],
                "rx-loginKey": self.rx["loginKey"],
                "rx-deviceKbn": self.rx["deviceKbn"],
                "rx-loginType": self.rx["loginType"],
                "javax.faces.ViewState": self.view_state,
            },
        ) as r:
            soup = BeautifulSoup(
                BeautifulSoup(await r.content.read(), "xml")
                .find("update", {"id": f"funcForm:{self.j_idt_kaitai}"})
                .text.strip(),
                "html.parser",
            )
            kaitai_list = []
            for item in soup.find_all("li", class_="ui-datalist-item"):
                if task := item.find(class_="signPortal signPortalKadai"):
                    deadline = datetime.strptime(
                        item.find_all(class_="textDate")[-1].text + "/23:59",
                        "%Y/%m/%d/%H:%M",
                    )
                    kaitai_list.append(
                        {
                            "task": task.text,
                            "date": item.find(class_="textDate").text,
                            "title": item.find(class_="textTitle").text.replace(
                                "\u3000", " "
                            ),
                            "from": item.find_all(class_="textFrom")[1].text.replace(
                                "\u3000", " "
                            ),
                            "deadline": deadline,
                        }
                    )
            return kaitai_list

    async def to_home_page(self):
        """ホームページに戻る

        Raises:
            GakuenAPIError: ログインエラー

        Returns:
            BeautifulSoup: ページデータ
        """
        async with self.s.post(
            f"{self.hosts}/uprx/up/bs/bsc005/Bsc00501.xhtml",
            data={
                "headerForm": "headerForm",
                "rx-token": self.rx["token"],
                "rx-loginKey": self.rx["loginKey"],
                "rx-deviceKbn": self.rx["deviceKbn"],
                "rx-loginType": self.rx["loginType"],
                "headerForm:logo": "",
                "javax.faces.ViewState": self.view_state,
                "rx.sync.source": "headerForm:logo",
            },
        ) as r:
            if r.status != 200:
                raise GakuenAPIError(
                    "ログインエラー: ログインページが取得できませんでした"
                )
            soup = BeautifulSoup(await r.text(), "html.parser")
            if error_msg := soup.find("span", class_="ui-messages-error-detail"):
                raise GakuenAPIError(f"ログインエラー: {error_msg.text}")
            return soup
