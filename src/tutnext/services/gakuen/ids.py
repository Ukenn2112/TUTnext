# tutnext/services/gakuen/ids.py
# Dynamic registry for PrimeFaces component IDs discovered at runtime.
import re
from typing import Optional

from bs4 import BeautifulSoup, Tag


class _MobilePageIds:
    """PrimeFaces コンポーネント ID の動的レジストリ"""

    def __init__(self) -> None:
        self.schedule_component_id: Optional[str] = None  # desktop: j_idt387
        self.kadai_tab_id: Optional[str] = None           # desktop: j_idt176:j_idt229
        self.calendar_id: Optional[str] = None            # mobile: pmPage:funcForm:j_idt104
        self.accordion_id: Optional[str] = None           # mobile: pmPage:funcForm:j_idt107
        self.kadai_tab_link_id: Optional[str] = None      # mobile: pmPage:funcForm:j_idt107:j_idt125
        self.menu_button_id: Optional[str] = None         # questionnaire bypass: pmPage:menuForm:j_idt36:0:menuBtnF

    @property
    def accordion_active_id(self) -> Optional[str]:
        """AccordionPanel のアクティブ状態 hidden input ID"""
        return f"{self.accordion_id}_active" if self.accordion_id else None

    def extract_desktop_ids(self, soup: BeautifulSoup) -> None:
        """デスクトップ画面の PrimeFaces コンポーネント ID を抽出
        Fix 1: script_tags[34] の位置指定を廃止し、スクリプト内容で検索する
        """
        for script in soup.find_all("script", type="text/javascript"):
            txt = script.string or ""
            if 'PrimeFaces.cw("Schedule"' in txt:
                m = re.search(r'id:"(funcForm:[^"]+)"', txt)
                if m:
                    self.schedule_component_id = m.group(1).split(":")[1]
                break

        portal_support = soup.find("ul", role="tablist")
        if (
            isinstance(portal_support, Tag)
            and (a := portal_support.find_all("a"))
            and len(a) > 1
            and isinstance(b := a[-1], Tag)
            and isinstance(href := b.get("href"), str)
        ):
            self.kadai_tab_id = href.lstrip("#funcForm:")

    def extract_mobile_ids(self, soup: BeautifulSoup) -> None:
        """モバイル画面の PrimeFaces コンポーネント ID を動的に抽出
        Fix 2: ハードコードされた ID を廃止し、PrimeFaces スクリプトから動的に取得する
        """
        for script in soup.find_all("script", type="text/javascript"):
            txt = script.string or ""
            if 'PrimeFaces.cw("Calendar"' in txt and not self.calendar_id:
                m = re.search(
                    r'PrimeFaces\.cw\("Calendar".*?id:"(pmPage:funcForm:[^"]+)"',
                    txt, re.DOTALL
                )
                if m:
                    self.calendar_id = m.group(1)
            if 'PrimeFaces.cw("AccordionPanel"' in txt and not self.accordion_id:
                m = re.search(
                    r'PrimeFaces\.cw\("AccordionPanel".*?id:"(pmPage:funcForm:[^"]+)"',
                    txt, re.DOTALL
                )
                if m:
                    self.accordion_id = m.group(1)

        # Find the "期限あり" tab navigation link ID
        if not self.kadai_tab_link_id:
            for a_tag in soup.find_all("a"):
                if isinstance(a_tag, Tag) and "期限あり" in a_tag.get_text():
                    link_id = a_tag.get("id")
                    if isinstance(link_id, str):
                        self.kadai_tab_link_id = link_id
                        break
