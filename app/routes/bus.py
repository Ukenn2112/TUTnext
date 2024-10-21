# app/routes/bus.py
from fastapi import APIRouter, HTTPException

router = APIRouter()

# 平日（水曜日を除く）
heijitsu = {
    "seiseki_school": {
        7: {25: {"grade": "中学生", "status": None}},
        8: {
            0: {"grade": "高校生", "status": None},
            35: {"grade": "指定なし", "status": None},
        },
        9: {},
        10: {15: {"grade": "指定なし", "status": None}},
        11: {0: {"grade": "指定なし", "status": None}},
        12: {40: {"grade": "指定なし", "status": None}},
        13: {25: {"grade": "指定なし", "status": "永山駅経由学校行き"}},
        14: {10: {"grade": "指定なし", "status": None}},
        15: {45: {"grade": "指定なし", "status": None}},
    },
    "nagayama_school": {
        7: {
            10: {"grade": "指定なし", "status": None},
            15: {"grade": "指定なし", "status": None},
            25: {"grade": "中学生", "status": None},
            35: {"grade": "中学生", "status": None},
            40: {"grade": "中学生", "status": None},
            50: {"grade": "高校生", "status": None},
        },
        8: {
            0: {"grade": "高校生", "status": None},
            5: {"grade": "高校生", "status": None},
            20: {"grade": "指定なし", "status": None},
            35: {"grade": "指定なし", "status": None},
            40: {"grade": "指定なし", "status": None},
            45: {"grade": "指定なし", "status": None},
        },
        9: {},
        10: {
            0: {"grade": "指定なし", "status": None},
            15: {"grade": "指定なし", "status": None},
            20: {"grade": "指定なし", "status": None},
            25: {"grade": "指定なし", "status": None},
            55: {"grade": "指定なし", "status": None},
        },
        11: {45: {"grade": "指定なし", "status": None}},
        12: {
            15: {"grade": "指定なし", "status": None},
            35: {"grade": "指定なし", "status": None},
            45: {"grade": "指定なし", "status": None},
        },
        13: {
            25: {
                "grade": "指定なし",
                "status": "聖蹟桜ヶ丘駅から永山駅経由学校行き",
            },
            40: {"grade": "指定なし", "status": None},
        },
        14: {
            15: {"grade": "指定なし", "status": None},
            55: {"grade": "指定なし", "status": None},
        },
        16: {0: {"grade": "指定なし", "status": None}},
    },
    "school_nagayama": {
        9: {45: {"grade": "指定なし", "status": None}},
        10: {
            5: {"grade": "指定なし", "status": None},
            40: {"grade": "指定なし", "status": None},
        },
        11: {30: {"grade": "指定なし", "status": None}},
        12: {
            0: {"grade": "指定なし", "status": None},
            20: {"grade": "指定なし", "status": None},
            30: {"grade": "指定なし", "status": None},
        },
        13: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            }
        },
        14: {
            0: {"grade": "指定なし", "status": None},
            40: {"grade": "指定なし", "status": None},
            45: {"grade": "指定なし", "status": None},
        },
        15: {
            15: {"grade": "指定なし", "status": None},
            45: {"grade": "指定なし", "status": None},
        },
        16: {
            0: {"grade": "指定なし", "status": None},
            5: {"grade": "指定なし", "status": None},
            15: {"grade": "指定なし", "status": None},
            30: {"grade": "指定なし", "status": None},
            45: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        17: {
            15: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            45: {"grade": "指定なし", "status": None},
            50: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        18: {
            0: {"grade": "指定なし", "status": None},
            5: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            35: {"grade": "指定なし", "status": None},
            40: {"grade": "指定なし", "status": None},
        },
        19: {
            40: {
                "grade": "大学生用マイクロバス（火・木のみ）",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
    },
    "school_seiseki": {
        9: {55: {"grade": "指定なし", "status": None}},
        10: {40: {"grade": "指定なし", "status": None}},
        12: {20: {"grade": "指定なし", "status": None}},
        13: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            50: {"grade": "指定なし", "status": None},
        },
        14: {50: {"grade": "指定なし", "status": None}},
        15: {},
        16: {
            5: {"grade": "指定なし", "status": None},
            25: {"grade": "指定なし", "status": None},
            45: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        17: {
            15: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            50: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        18: {
            5: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            40: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        19: {
            40: {
                "grade": "大学生用マイクロバス（火・木のみ）",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
    },
}

# 水曜日
suiyoubi = {
    "seiseki_school": {
        7: {25: {"grade": "中学生", "status": None}},
        8: {
            0: {"grade": "高校生", "status": None},
            35: {"grade": "指定なし", "status": None},
        },
        9: {},
        10: {15: {"grade": "指定なし", "status": None}},
        11: {0: {"grade": "指定なし", "status": None}},
        12: {40: {"grade": "指定なし", "status": None}},
        13: {25: {"grade": "指定なし", "status": "永山駅経由学校行き"}},
        14: {10: {"grade": "指定なし", "status": None}},
        15: {10: {"grade": "指定なし", "status": None}},
    },
    "nagayama_school": {
        7: {
            10: {"grade": "指定なし", "status": None},
            15: {"grade": "指定なし", "status": None},
            25: {"grade": "中学生", "status": None},
            35: {"grade": "中学生", "status": None},
            40: {"grade": "中学生", "status": None},
            50: {"grade": "高校生", "status": None},
        },
        8: {
            0: {"grade": "高校生", "status": None},
            5: {"grade": "高校生", "status": None},
            20: {"grade": "指定なし", "status": None},
            35: {"grade": "指定なし", "status": None},
            40: {"grade": "指定なし", "status": None},
            45: {"grade": "指定なし", "status": None},
        },
        9: {},
        10: {
            0: {"grade": "指定なし", "status": None},
            15: {"grade": "指定なし", "status": None},
            20: {"grade": "指定なし", "status": None},
            25: {"grade": "指定なし", "status": None},
            55: {"grade": "指定なし", "status": None},
        },
        11: {45: {"grade": "指定なし", "status": None}},
        12: {
            15: {"grade": "指定なし", "status": None},
            35: {"grade": "指定なし", "status": None},
            45: {"grade": "指定なし", "status": None},
        },
        13: {
            25: {
                "grade": "指定なし",
                "status": "聖蹟桜ヶ丘駅から永山駅経由学校行き",
            },
            40: {"grade": "指定なし", "status": None},
        },
        14: {
            15: {"grade": "指定なし", "status": None},
            55: {"grade": "指定なし", "status": None},
        },
        16: {0: {"grade": "指定なし", "status": None}},
    },
    "school_nagayama": {
        9: {45: {"grade": "指定なし", "status": None}},
        10: {
            5: {"grade": "指定なし", "status": None},
            40: {"grade": "指定なし", "status": None},
        },
        11: {30: {"grade": "指定なし", "status": None}},
        12: {
            0: {"grade": "指定なし", "status": None},
            20: {"grade": "指定なし", "status": None},
            30: {"grade": "指定なし", "status": None},
            55: {"grade": "指定なし", "status": None},
        },
        13: {
            0: {"grade": "指定なし", "status": None},
            5: {"grade": "指定なし", "status": None},
            20: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        14: {
            0: {"grade": "指定なし", "status": None},
            35: {"grade": "指定なし", "status": None},
            40: {"grade": "指定なし", "status": None},
            45: {"grade": "指定なし", "status": None},
            50: {"grade": "指定なし", "status": None},
        },
        15: {
            0: {"grade": "指定なし", "status": None},
            15: {"grade": "指定なし", "status": None},
            45: {"grade": "指定なし", "status": None},
        },
        16: {
            5: {"grade": "指定なし", "status": None},
            15: {"grade": "指定なし", "status": None},
            30: {"grade": "指定なし", "status": None},
            45: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        17: {
            15: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            50: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        18: {
            0: {"grade": "指定なし", "status": None},
            5: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            40: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        19: {
            40: {
                "grade": "大学生用マイクロバス",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
    },
    "school_seiseki": {
        9: {55: {"grade": "指定なし", "status": None}},
        10: {40: {"grade": "指定なし", "status": None}},
        11: {},
        12: {20: {"grade": "指定なし", "status": None}},
        13: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            20: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            50: {"grade": "指定なし", "status": None},
        },
        14: {50: {"grade": "指定なし", "status": None}},
        15: {
            25: {"grade": "指定なし", "status": None},
        },
        16: {
            5: {"grade": "指定なし", "status": None},
            25: {"grade": "指定なし", "status": None},
            45: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        17: {
            15: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            50: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        18: {
            5: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            40: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        19: {
            40: {
                "grade": "大学生用マイクロバス（火・木のみ）",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
    },
}

# 土曜日
doyoubi = {
    "seiseki_school": {
        7: {25: {"grade": "中学生", "status": None}},
        8: {
            0: {"grade": "高校生", "status": None},
            25: {"grade": "指定なし", "status": "永山駅経由学校行き"},
            55: {"grade": "指定なし", "status": "永山駅経由学校行き"},
        },
        9: {
            25: {"grade": "指定なし", "status": "永山駅経由学校行き"},
            55: {"grade": "指定なし", "status": "永山駅経由学校行き"},
        },
        10: {
            25: {"grade": "指定なし", "status": "永山駅経由学校行き"},
            55: {"grade": "指定なし", "status": "永山駅経由学校行き"},
        },
        11: {
            55: {"grade": "指定なし", "status": "永山駅経由学校行き"},
        },
        12: {
            50: {"grade": "指定なし", "status": "永山駅経由学校行き"},
        },
        13: {
            20: {"grade": "指定なし", "status": "永山駅経由学校行き"},
        },
        14: {
            25: {"grade": "指定なし", "status": "永山駅経由学校行き"},
        },
    },
    "nagayama_school": {
        7: {
            10: {"grade": "指定なし", "status": None},
            15: {"grade": "指定なし", "status": None},
            25: {"grade": "中学生", "status": None},
            35: {"grade": "中学生", "status": None},
            40: {"grade": "中学生", "status": None},
            50: {"grade": "高校生", "status": None},
        },
        8: {
            0: {"grade": "高校生", "status": None},
            5: {"grade": "高校生", "status": None},
            25: {
                "grade": "指定なし",
                "status": "聖蹟桜ヶ丘駅から永山駅経由学校行き",
            },
            40: {"grade": "指定なし", "status": None},
            55: {
                "grade": "指定なし",
                "status": "聖蹟桜ヶ丘駅から永山駅経由学校行き",
            },
        },
        9: {
            10: {"grade": "指定なし", "status": None},
            25: {
                "grade": "指定なし",
                "status": "聖蹟桜ヶ丘駅から永山駅経由学校行き",
            },
            40: {"grade": "指定なし", "status": None},
            55: {
                "grade": "指定なし",
                "status": "聖蹟桜ヶ丘駅から永山駅経由学校行き",
            },
        },
        10: {
            10: {"grade": "指定なし", "status": None},
            25: {
                "grade": "指定なし",
                "status": "聖蹟桜ヶ丘駅から永山駅経由学校行き",
            },
            40: {"grade": "指定なし", "status": None},
            55: {
                "grade": "指定なし",
                "status": "聖蹟桜ヶ丘駅から永山駅経由学校行き",
            },
        },
        11: {
            10: {"grade": "指定なし", "status": None},
            55: {
                "grade": "指定なし",
                "status": "聖蹟桜ヶ丘駅から永山駅経由学校行き",
            },
        },
        12: {
            10: {"grade": "指定なし", "status": None},
            45: {"grade": "指定なし", "status": None},
            50: {"grade": "指定なし", "status": None},
        },
        13: {
            5: {"grade": "指定なし", "status": None},
            20: {
                "grade": "指定なし",
                "status": "聖蹟桜ヶ丘駅から永山駅経由学校行き",
            },
            35: {"grade": "指定なし", "status": None},
        },
        14: {
            25: {
                "grade": "指定なし",
                "status": "聖蹟桜ヶ丘駅から永山駅経由学校行き",
            },
            40: {"grade": "指定なし", "status": None},
        },
    },
    "school_nagayama": {
        9: {
            30: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        10: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            30: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        11: {
            0: {"grade": "指定なし", "status": None},
            30: {"grade": "指定なし", "status": None},
            35: {"grade": "指定なし", "status": None},
            40: {"grade": "指定なし", "status": None},
            55: {"grade": "指定なし", "status": None},
        },
        12: {
            25: {"grade": "指定なし", "status": None},
            30: {"grade": "指定なし", "status": None},
            35: {"grade": "指定なし", "status": None},
            50: {"grade": "指定なし", "status": None},
            55: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        13: {
            25: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        14: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            30: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        15: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            30: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        16: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            30: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            45: {"grade": "指定なし", "status": None},
        },
        17: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            30: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        18: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            40: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
    },
    "school_seiseki": {
        9: {
            30: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        10: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            30: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        11: {20: {"grade": "指定なし", "status": None}},
        12: {
            30: {"grade": "指定なし", "status": None},
            55: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        13: {
            25: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        14: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            30: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        15: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            30: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        16: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            30: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        17: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            30: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
        18: {
            0: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
            40: {
                "grade": "指定なし",
                "status": "永山駅経由聖蹟桜ヶ丘駅行き",
            },
        },
    },
}


@router.get("/")
async def bus_schedule(_type: int = None, route: str = None):
    bus_data = {
        1: heijitsu,
        2: suiyoubi,
        3: doyoubi,
    }
    if _type is None:
        return data
    elif _type in bus_data:
        if route in bus_data[_type]:
            return bus_data[_type][route]
        else:
            return bus_data[_type]
    else:
        return bus_data


@router.get("/messige")
async def bus_messige(_type: int, route: str, hour: int, minute: int):
    if route == "seiseki_school":
        route_text = "聖蹟桜ヶ丘駅から学校行き"
    elif route == "nagayama_school":
        route_text = "永山駅から学校行き"
    elif route == "school_nagayama":
        route_text = "学校から永山駅行き"
    elif route == "school_seiseki":
        route_text = "学校から聖蹟桜ヶ丘駅行き"
    else:
        return HTTPException(status_code=404, detail="Route Not Found")
    bus_data = await bus_schedule(_type, route)  # バスデータ取得
    # 列表推导式
    next_bus = [
        (h, m)
        for h in bus_data.keys()  # 外层循环: 遍历 bus_data 字典中的所有小时 h
        for m in bus_data[
            h
        ].keys()  # 内层循环: 对于每个小时 h，遍历该小时下的所有分钟 m
        if (h > hour) or (h == hour and m >= minute)
        # (h > hour): 如果小时 h 大于当前小时 hour
        # (h == hour and m >= minute): 如果小时 h 等于当前小时 hour，那么只要分钟 m 大于等于当前分钟 minute
    ]
    if not next_bus:
        return None
    next_bus.sort()  # 对 next_bus 列表进行排序
    next_hour, next_minute = next_bus[0]
    time_deff = (next_hour - hour) * 60 + (next_minute - minute)
    the_time_data = bus_data[next_hour][next_minute]
    msg_text = f"{next_hour}時{next_minute:02d}分発の\n{route_text}のバスが\n{time_deff}分後に出発します。"
    if the_time_data["status"] is not None:
        msg_text += f"\n\nこれは{the_time_data['status']}のバスです。"
    if the_time_data["grade"] != "指定なし":
        msg_text += f"\n\n乗車対象は{the_time_data['grade']}のみです。"
    msg_text += "\n=========これ以降========\n"
    if len(next_bus) > 1:
        next_bus_texts = [
            f"{h}:{m:02d}"
            + ("◎" if bus_data[h][m]["status"] is not None else "")
            + (
                f"*({bus_data[h][m]['grade']})"
                if bus_data[h][m]["grade"] != "指定なし"
                else ""
            )
            for h, m in next_bus[1:]
        ]
        msg_text += "、".join(next_bus_texts)
    else:
        msg_text += "なし。"
    msg_text += "\n\n※以上全ては2024年度通常バスダイヤです。\n臨時バスまたは祝日授業日の場合は異なる場合にもございますのでご了承ください。"
    return {"message": msg_text}
