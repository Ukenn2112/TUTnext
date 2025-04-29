# app/routes/bus.py
from requests import get
from bs4 import BeautifulSoup
from fastapi import APIRouter
from datetime import datetime

router = APIRouter()

app_data = {
    "weekday": {
        "fromSeisekiToSchool": [
            {
                "hour": 7,
                "times": [
                    {"hour": 7, "minute": 25, "isSpecial": True, "specialNote": "C"}
                ],
            },
            {
                "hour": 8,
                "times": [
                    {"hour": 8, "minute": 0, "isSpecial": True, "specialNote": "K"},
                    {"hour": 8, "minute": 35, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 10,
                "times": [
                    {"hour": 10, "minute": 15, "isSpecial": False, "specialNote": None}
                ],
            },
            {
                "hour": 11,
                "times": [
                    {"hour": 11, "minute": 0, "isSpecial": False, "specialNote": None}
                ],
            },
            {
                "hour": 12,
                "times": [
                    {"hour": 12, "minute": 40, "isSpecial": False, "specialNote": None}
                ],
            },
            {
                "hour": 13,
                "times": [
                    {"hour": 13, "minute": 25, "isSpecial": True, "specialNote": "◯"}
                ],
            },
            {
                "hour": 14,
                "times": [
                    {"hour": 14, "minute": 10, "isSpecial": False, "specialNote": None}
                ],
            },
            {
                "hour": 15,
                "times": [
                    {"hour": 15, "minute": 10, "isSpecial": False, "specialNote": None}
                ],
            },
        ],
        "fromNagayamaToSchool": [
            {
                "hour": 7,
                "times": [
                    {"hour": 7, "minute": 10, "isSpecial": False, "specialNote": None},
                    {"hour": 7, "minute": 15, "isSpecial": False, "specialNote": None},
                    {"hour": 7, "minute": 25, "isSpecial": True, "specialNote": "C"},
                    {"hour": 7, "minute": 35, "isSpecial": True, "specialNote": "C"},
                    {"hour": 7, "minute": 40, "isSpecial": True, "specialNote": "C"},
                    {"hour": 7, "minute": 50, "isSpecial": True, "specialNote": "K"},
                ],
            },
            {
                "hour": 8,
                "times": [
                    {"hour": 8, "minute": 0, "isSpecial": True, "specialNote": "K"},
                    {"hour": 8, "minute": 5, "isSpecial": True, "specialNote": "K"},
                    {"hour": 8, "minute": 20, "isSpecial": False, "specialNote": None},
                    {"hour": 8, "minute": 35, "isSpecial": False, "specialNote": None},
                    {"hour": 8, "minute": 40, "isSpecial": False, "specialNote": None},
                    {"hour": 8, "minute": 45, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 10,
                "times": [
                    {"hour": 10, "minute": 0, "isSpecial": False, "specialNote": None},
                    {"hour": 10, "minute": 15, "isSpecial": False, "specialNote": None},
                    {"hour": 10, "minute": 20, "isSpecial": False, "specialNote": None},
                    {"hour": 10, "minute": 25, "isSpecial": False, "specialNote": None},
                    {"hour": 10, "minute": 55, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 11,
                "times": [
                    {"hour": 11, "minute": 45, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 12,
                "times": [
                    {"hour": 12, "minute": 15, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 35, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 45, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 13,
                "times": [
                    {"hour": 13, "minute": 40, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 14,
                "times": [
                    {"hour": 14, "minute": 15, "isSpecial": False, "specialNote": None},
                    {"hour": 14, "minute": 55, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 16,
                "times": [
                    {"hour": 16, "minute": 0, "isSpecial": False, "specialNote": None},
                ],
            },
        ],
        "fromSchoolToSeiseki": [
            {
                "hour": 9,
                "times": [
                    {"hour": 9, "minute": 55, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 10,
                "times": [
                    {"hour": 10, "minute": 40, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 12,
                "times": [
                    {"hour": 12, "minute": 20, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 13,
                "times": [
                    {"hour": 13, "minute": 0, "isSpecial": True, "specialNote": "*"},
                    {"hour": 13, "minute": 50, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 14,
                "times": [
                    {"hour": 14, "minute": 50, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 16,
                "times": [
                    {"hour": 16, "minute": 5, "isSpecial": False, "specialNote": None},
                    {"hour": 16, "minute": 25, "isSpecial": False, "specialNote": None},
                    {"hour": 16, "minute": 45, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 17,
                "times": [
                    {"hour": 17, "minute": 15, "isSpecial": True, "specialNote": "*"},
                    {"hour": 17, "minute": 50, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 18,
                "times": [
                    {"hour": 18, "minute": 5, "isSpecial": True, "specialNote": "*"},
                    {"hour": 18, "minute": 40, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 19,
                "times": [
                    {"hour": 19, "minute": 40, "isSpecial": True, "specialNote": "M"},
                ],
            },
        ],
        "fromSchoolToNagayama": [
            {
                "hour": 9,
                "times": [
                    {"hour": 9, "minute": 45, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 10,
                "times": [
                    {"hour": 10, "minute": 5, "isSpecial": False, "specialNote": None},
                    {"hour": 10, "minute": 40, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 11,
                "times": [
                    {"hour": 11, "minute": 30, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 12,
                "times": [
                    {"hour": 12, "minute": 0, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 20, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 30, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 14,
                "times": [
                    {"hour": 14, "minute": 0, "isSpecial": False, "specialNote": None},
                    {"hour": 14, "minute": 40, "isSpecial": False, "specialNote": None},
                    {"hour": 14, "minute": 45, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 16,
                "times": [
                    {"hour": 16, "minute": 0, "isSpecial": False, "specialNote": None},
                    {"hour": 16, "minute": 5, "isSpecial": False, "specialNote": None},
                    {"hour": 16, "minute": 15, "isSpecial": False, "specialNote": None},
                    {"hour": 16, "minute": 30, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 17,
                "times": [
                    {"hour": 17, "minute": 45, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 18,
                "times": [
                    {"hour": 18, "minute": 0, "isSpecial": False, "specialNote": None},
                    {"hour": 18, "minute": 35, "isSpecial": False, "specialNote": None},
                    {"hour": 18, "minute": 40, "isSpecial": False, "specialNote": None},
                ],
            },
        ],
    },
    "wednesday": {
        "fromSeisekiToSchool": [
            {
                "hour": 7,
                "times": [
                    {"hour": 7, "minute": 25, "isSpecial": True, "specialNote": "C"}
                ],
            },
            {
                "hour": 8,
                "times": [
                    {"hour": 8, "minute": 0, "isSpecial": True, "specialNote": "K"},
                    {"hour": 8, "minute": 35, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 10,
                "times": [
                    {"hour": 10, "minute": 15, "isSpecial": False, "specialNote": None}
                ],
            },
            {
                "hour": 11,
                "times": [
                    {"hour": 11, "minute": 0, "isSpecial": False, "specialNote": None}
                ],
            },
            {
                "hour": 12,
                "times": [
                    {"hour": 12, "minute": 40, "isSpecial": False, "specialNote": None}
                ],
            },
            {
                "hour": 13,
                "times": [
                    {"hour": 13, "minute": 25, "isSpecial": True, "specialNote": "◯"}
                ],
            },
            {
                "hour": 14,
                "times": [
                    {"hour": 14, "minute": 10, "isSpecial": False, "specialNote": None}
                ],
            },
            {
                "hour": 15,
                "times": [
                    {"hour": 15, "minute": 10, "isSpecial": False, "specialNote": None}
                ],
            },
        ],
        "fromNagayamaToSchool": [
            {
                "hour": 7,
                "times": [
                    {"hour": 7, "minute": 10, "isSpecial": False, "specialNote": None},
                    {"hour": 7, "minute": 15, "isSpecial": False, "specialNote": None},
                    {"hour": 7, "minute": 25, "isSpecial": True, "specialNote": "C"},
                    {"hour": 7, "minute": 35, "isSpecial": True, "specialNote": "C"},
                    {"hour": 7, "minute": 40, "isSpecial": True, "specialNote": "C"},
                    {"hour": 7, "minute": 50, "isSpecial": True, "specialNote": "K"},
                ],
            },
            {
                "hour": 8,
                "times": [
                    {"hour": 8, "minute": 0, "isSpecial": True, "specialNote": "K"},
                    {"hour": 8, "minute": 5, "isSpecial": True, "specialNote": "K"},
                    {"hour": 8, "minute": 20, "isSpecial": False, "specialNote": None},
                    {"hour": 8, "minute": 35, "isSpecial": False, "specialNote": None},
                    {"hour": 8, "minute": 40, "isSpecial": False, "specialNote": None},
                    {"hour": 8, "minute": 45, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 10,
                "times": [
                    {"hour": 10, "minute": 0, "isSpecial": False, "specialNote": None},
                    {"hour": 10, "minute": 15, "isSpecial": False, "specialNote": None},
                    {"hour": 10, "minute": 20, "isSpecial": False, "specialNote": None},
                    {"hour": 10, "minute": 25, "isSpecial": False, "specialNote": None},
                    {"hour": 10, "minute": 55, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 11,
                "times": [
                    {"hour": 11, "minute": 45, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 12,
                "times": [
                    {"hour": 12, "minute": 15, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 35, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 45, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 13,
                "times": [
                    {"hour": 13, "minute": 40, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 14,
                "times": [
                    {"hour": 14, "minute": 15, "isSpecial": False, "specialNote": None},
                    {"hour": 14, "minute": 55, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 16,
                "times": [
                    {"hour": 16, "minute": 0, "isSpecial": False, "specialNote": None},
                ],
            },
        ],
        "fromSchoolToSeiseki": [
            {
                "hour": 9,
                "times": [
                    {"hour": 9, "minute": 55, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 10,
                "times": [
                    {"hour": 10, "minute": 40, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 12,
                "times": [
                    {"hour": 12, "minute": 20, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 13,
                "times": [
                    {"hour": 13, "minute": 0, "isSpecial": True, "specialNote": "*"},
                    {"hour": 13, "minute": 20, "isSpecial": True, "specialNote": "*"},
                    {"hour": 13, "minute": 50, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 14,
                "times": [
                    {"hour": 14, "minute": 50, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 15,
                "times": [
                    {"hour": 15, "minute": 25, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 16,
                "times": [
                    {"hour": 16, "minute": 5, "isSpecial": False, "specialNote": None},
                    {"hour": 16, "minute": 25, "isSpecial": False, "specialNote": None},
                    {"hour": 16, "minute": 45, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 17,
                "times": [
                    {"hour": 17, "minute": 15, "isSpecial": True, "specialNote": "*"},
                    {"hour": 17, "minute": 50, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 18,
                "times": [
                    {"hour": 18, "minute": 5, "isSpecial": True, "specialNote": "*"},
                    {"hour": 18, "minute": 40, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 19,
                "times": [
                    {"hour": 19, "minute": 40, "isSpecial": True, "specialNote": "M"},
                ],
            },
        ],
        "fromSchoolToNagayama": [
            {
                "hour": 9,
                "times": [
                    {"hour": 9, "minute": 45, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 10,
                "times": [
                    {"hour": 10, "minute": 5, "isSpecial": False, "specialNote": None},
                    {"hour": 10, "minute": 40, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 11,
                "times": [
                    {"hour": 11, "minute": 30, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 12,
                "times": [
                    {"hour": 12, "minute": 0, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 20, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 30, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 55, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 13,
                "times": [
                    {"hour": 13, "minute": 0, "isSpecial": False, "specialNote": None},
                    {"hour": 13, "minute": 5, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 14,
                "times": [
                    {"hour": 14, "minute": 0, "isSpecial": False, "specialNote": None},
                    {"hour": 14, "minute": 35, "isSpecial": False, "specialNote": None},
                    {"hour": 14, "minute": 40, "isSpecial": False, "specialNote": None},
                    {"hour": 14, "minute": 50, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 15,
                "times": [
                    {"hour": 14, "minute": 0, "isSpecial": False, "specialNote": None},
                    {"hour": 14, "minute": 15, "isSpecial": False, "specialNote": None},
                    {"hour": 14, "minute": 45, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 16,
                "times": [
                    {"hour": 16, "minute": 5, "isSpecial": False, "specialNote": None},
                    {"hour": 16, "minute": 15, "isSpecial": False, "specialNote": None},
                    {"hour": 16, "minute": 30, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 18,
                "times": [
                    {"hour": 18, "minute": 0, "isSpecial": False, "specialNote": None},
                ],
            },
        ],
    },
    "saturday": {
        "fromSeisekiToSchool": [
            {
                "hour": 7,
                "times": [
                    {"hour": 7, "minute": 25, "isSpecial": True, "specialNote": "C"}
                ],
            },
            {
                "hour": 8,
                "times": [
                    {"hour": 8, "minute": 0, "isSpecial": True, "specialNote": "K"},
                    {"hour": 8, "minute": 25, "isSpecial": True, "specialNote": "◯"},
                    {"hour": 8, "minute": 55, "isSpecial": True, "specialNote": "◯"},
                ],
            },
            {
                "hour": 9,
                "times": [
                    {"hour": 9, "minute": 25, "isSpecial": True, "specialNote": "◯"},
                    {"hour": 9, "minute": 55, "isSpecial": True, "specialNote": "◯"},
                ],
            },
            {
                "hour": 10,
                "times": [
                    {"hour": 10, "minute": 25, "isSpecial": True, "specialNote": "◯"},
                    {"hour": 10, "minute": 55, "isSpecial": True, "specialNote": "◯"},
                ],
            },
            {
                "hour": 11,
                "times": [
                    {"hour": 11, "minute": 55, "isSpecial": True, "specialNote": "◯"},
                ],
            },
            {
                "hour": 12,
                "times": [
                    {"hour": 12, "minute": 50, "isSpecial": True, "specialNote": "◯"},
                ],
            },
            {
                "hour": 13,
                "times": [
                    {"hour": 13, "minute": 20, "isSpecial": True, "specialNote": "◯"}
                ],
            },
            {
                "hour": 14,
                "times": [
                    {"hour": 14, "minute": 25, "isSpecial": False, "specialNote": None}
                ],
            },
        ],
        "fromNagayamaToSchool": [
            {
                "hour": 7,
                "times": [
                    {"hour": 7, "minute": 10, "isSpecial": False, "specialNote": None},
                    {"hour": 7, "minute": 15, "isSpecial": False, "specialNote": None},
                    {"hour": 7, "minute": 25, "isSpecial": True, "specialNote": "C"},
                    {"hour": 7, "minute": 35, "isSpecial": True, "specialNote": "C"},
                    {"hour": 7, "minute": 40, "isSpecial": True, "specialNote": "C"},
                    {"hour": 7, "minute": 50, "isSpecial": True, "specialNote": "K"},
                ],
            },
            {
                "hour": 8,
                "times": [
                    {"hour": 8, "minute": 0, "isSpecial": True, "specialNote": "K"},
                    {"hour": 8, "minute": 5, "isSpecial": True, "specialNote": "K"},
                    {"hour": 8, "minute": 40, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 9,
                "times": [
                    {"hour": 9, "minute": 10, "isSpecial": False, "specialNote": None},
                    {"hour": 9, "minute": 40, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 10,
                "times": [
                    {"hour": 10, "minute": 10, "isSpecial": False, "specialNote": None},
                    {"hour": 10, "minute": 40, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 11,
                "times": [
                    {"hour": 11, "minute": 10, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 12,
                "times": [
                    {"hour": 12, "minute": 10, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 40, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 50, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 13,
                "times": [
                    {"hour": 13, "minute": 5, "isSpecial": False, "specialNote": None},
                    {"hour": 13, "minute": 35, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 14,
                "times": [
                    {"hour": 14, "minute": 40, "isSpecial": False, "specialNote": None},
                ],
            },
        ],
        "fromSchoolToSeiseki": [
            {
                "hour": 9,
                "times": [
                    {"hour": 9, "minute": 30, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 10,
                "times": [
                    {"hour": 10, "minute": 0, "isSpecial": True, "specialNote": "*"},
                    {"hour": 10, "minute": 30, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 11,
                "times": [
                    {"hour": 11, "minute": 40, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 12,
                "times": [
                    {"hour": 12, "minute": 30, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 55, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 13,
                "times": [
                    {"hour": 13, "minute": 25, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 14,
                "times": [
                    {"hour": 14, "minute": 0, "isSpecial": True, "specialNote": "*"},
                    {"hour": 14, "minute": 30, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 15,
                "times": [
                    {"hour": 15, "minute": 0, "isSpecial": True, "specialNote": "*"},
                    {"hour": 15, "minute": 30, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 16,
                "times": [
                    {"hour": 16, "minute": 0, "isSpecial": True, "specialNote": "*"},
                    {"hour": 16, "minute": 30, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 17,
                "times": [
                    {"hour": 17, "minute": 0, "isSpecial": True, "specialNote": "*"},
                    {"hour": 17, "minute": 30, "isSpecial": True, "specialNote": "*"},
                ],
            },
            {
                "hour": 18,
                "times": [
                    {"hour": 18, "minute": 0, "isSpecial": True, "specialNote": "*"},
                    {"hour": 18, "minute": 30, "isSpecial": True, "specialNote": "*"},
                ],
            },
        ],
        "fromSchoolToNagayama": [
            {
                "hour": 11,
                "times": [
                    {"hour": 11, "minute": 0, "isSpecial": False, "specialNote": None},
                    {"hour": 11, "minute": 30, "isSpecial": False, "specialNote": None},
                    {"hour": 11, "minute": 35, "isSpecial": False, "specialNote": None},
                    {"hour": 11, "minute": 40, "isSpecial": False, "specialNote": None},
                    {"hour": 11, "minute": 55, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 12,
                "times": [
                    {"hour": 12, "minute": 25, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 30, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 35, "isSpecial": False, "specialNote": None},
                    {"hour": 12, "minute": 50, "isSpecial": False, "specialNote": None},
                ],
            },
            {
                "hour": 16,
                "times": [
                    {"hour": 16, "minute": 45, "isSpecial": False, "specialNote": None},
                ],
            },
        ],
    },
}


@router.get("/app_data")
async def app_schedule():
    web_data = get("https://www.tama.ac.jp/guide/campus/schoolbus.html")
    soup = BeautifulSoup(web_data.text, "html.parser")
    _messages = []
    holidays_data = get("https://holidays-jp.github.io/api/v1/date.json").json()
    now_day_str = datetime.now().strftime("%Y-%m-%d")
    if now_day_str in holidays_data:
        _messages.append(
            {
                "title": f"本日 {now_day_str} 祝日授業日のスクールバス時刻表 ",
                "url": "https://www.tama.ac.jp/guide/campus/img/bus_2025holidays.pdf",
            }
        )
    if _web_data := soup.find("div", class_="rinji"):
        for web_data in _web_data.find_all("a"):
            _messages.append(
                {
                    "title": web_data.text,
                    "url": "https://www.tama.ac.jp/guide/campus/"
                    + web_data.get("href"),
                }
            )
    return {"messages": _messages, "data": app_data}
