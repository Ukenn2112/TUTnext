# app/routes/bus.py
from requests import get
from bs4 import BeautifulSoup, Tag
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
            {
                "hour": 19,
                "times": [
                    {"hour": 19, "minute": 40, "isSpecial": True, "specialNote": "M"},
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
            {
                "hour": 19,
                "times": [
                    {"hour": 19, "minute": 40, "isSpecial": True, "specialNote": "M"},
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
    # app_data 进行修正处理
    route_mapping = {
        "fromSeisekiToSchool": "fromNagayamaToSchool",
        "fromSchoolToSeiseki": "fromSchoolToNagayama",
    }

    # 处理特殊标记"*"的时间，将其复制到对应的Nagayama路线
    for day in ["weekday", "wednesday", "saturday"]:
        for source_route, target_route in route_mapping.items():
            if source_route not in app_data[day]:
                continue

            # 为目标路线创建小时索引以提高查找效率
            target_hours = {
                hour_data["hour"]: hour_data
                for hour_data in app_data[day][target_route]
            }

            for hour_data in app_data[day][source_route]:
                # 筛选出带有"*"标记的时间
                special_times = [
                    time for time in hour_data["times"] if time["specialNote"] == "*"
                ]

                if special_times:
                    hour = hour_data["hour"]
                    # 如果目标路线中已存在该小时，直接添加时间
                    if hour in target_hours:
                        for time in special_times:
                            # 检查时间是否已存在于目标路线中 如果存在将其时间的isSpecial标记设置为True
                            if existing_time := next(
                                (
                                    t
                                    for t in target_hours[hour]["times"]
                                    if t["hour"] == time["hour"]
                                    and t["minute"] == time["minute"]
                                ),
                                None,
                            ):
                                existing_time["isSpecial"] = True
                                existing_time["specialNote"] = "*"
                            else:
                                target_hours[hour]["times"].append(time)
                    else:
                        # 创建新的小时条目并添加到目标路线
                        new_hour_data = {"hour": hour, "times": special_times.copy()}
                        app_data[day][target_route].append(new_hour_data)
                        target_hours[hour] = new_hour_data

    # 对所有路线的时间进行排序
    for day_data in app_data.values():
        for route_data in day_data.values():
            route_data.sort(key=lambda x: x["hour"])
            for hour_data in route_data:
                hour_data["times"].sort(key=lambda x: (x["hour"], x["minute"]))
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
    if isinstance(_web_data := soup.find("div", class_="rinji"), Tag):
        for web_data in _web_data.find_all("a"):
            _messages.append(
                {
                    "title": web_data.text,
                    "url": "https://www.tama.ac.jp/guide/campus/"
                    + web_data.get("href"),
                }
            )
    return {"messages": _messages, "data": app_data}
