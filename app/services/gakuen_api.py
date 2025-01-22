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
    def __init__(self, userId: str, password: str, hosts: str) -> None:
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
        self.encryptedLoginPassword: str = None
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
                lessonTitle = p_tag.text.strip().replace(span_text, "").replace("\u3000", " ")
                if lessonTitle in self.class_list:
                    continue

                lessonDetail = c.find("div", class_="lessonDetail")
                lessonTeachers = " / ".join(
                    teacher.text.replace("\u3000", " ") for teacher in lessonDetail.find_all("a")
                )

                if lessonDetail.find("label"):
                    __Class = lessonDetail.find_all("div")
                    lessonClass = f"変更: {__Class[2].text.strip()} → {__Class[0].text.strip()}"
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
                    m["end"] = datetime.strptime(m["end"], "%Y-%m-%dT%H:%M:%S%z")
                else:  # 1日単位の授業
                    if m["className"] == "eventKeijiAd":
                        continue
                    m["start"] = datetime.strptime(m["start"], "%Y-%m-%dT%H:%M:%S%z")
                    m["end"] = datetime.strptime(m["end"], "%Y-%m-%dT%H:%M:%S%z")
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
