"""
Live Activity push scheduling.

Computes transition events for a user's daily schedule and dispatches
them as APNs ``liveactivity`` pushes at the appropriate times.

Transition logic here MUST stay in sync with the iOS
``LiveActivityScheduler.computeTransitions`` implementation.
"""
import json
import logging
from datetime import datetime, timedelta, time as dt_time
from typing import Optional
from uuid import uuid4

from aioapns import NotificationRequest, PushType

from tutnext.config import JAPAN_TZ, HTTP_PROXY, redis, APNS_CONFIG
from tutnext.services.gakuen.client import GakuenAPI, GakuenAPIError
from tutnext.services.push.apns_client import get_apns_client

logger = logging.getLogger(__name__)

# Apple reference date offset: 2001-01-01 00:00:00 UTC
_APPLE_EPOCH_OFFSET = 978307200.0

# APNs topic for Live Activity (main app bundle ID, NOT widget)
_LA_APNS_TOPIC = f"{APNS_CONFIG['topic']}.push-type.liveactivity"

# Period times (JST): lesson_num -> (start_h, start_m, end_h, end_m)
PERIOD_TIMES: dict[int, tuple[int, int, int, int]] = {
    1: (9, 0, 10, 30),
    2: (10, 40, 12, 10),
    3: (13, 0, 14, 30),
    4: (14, 40, 16, 10),
    5: (16, 20, 17, 50),
    6: (18, 0, 19, 30),
    7: (19, 40, 21, 10),
}

# Lua script for atomic pop from sorted set
_LUA_POP_DUE = """
local result = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, 1)
if #result > 0 then
    redis.call('ZREM', KEYS[1], result[1])
    return result[1]
end
return nil
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jst_dt(date_str: str, hour: int, minute: int) -> datetime:
    """Create a timezone-aware JST datetime from 'YYYY/MM/DD' + time."""
    d = datetime.strptime(date_str, "%Y/%m/%d")
    return JAPAN_TZ.localize(d.replace(hour=hour, minute=minute, second=0))


def _apple_ts(dt: datetime) -> float:
    """Convert datetime → Apple's timeIntervalSinceReferenceDate (for Codable Date)."""
    return dt.timestamp() - _APPLE_EPOCH_OFFSET


def _clean_room(room: str) -> str:
    return room.replace("教室", "").strip() if room else ""


# ---------------------------------------------------------------------------
# Transition computation
# ---------------------------------------------------------------------------

def compute_transitions(
    lessons: list[dict],
    date_str: str,
    *,
    push_only: bool = True,
) -> list[dict]:
    """Compute Live Activity transition events for a day's lessons.

    Args:
        lessons: Filtered (no cancelled) lesson dicts from the schedule API.
        date_str: Date in ``YYYY/MM/DD`` format.
        push_only: If True, only include push-worthy transitions
                   (inProgress, breakTime, finished).

    Returns:
        Sorted list of ``{timestamp, content_state}`` dicts.
    """
    transitions: list[dict] = []
    sorted_lessons = sorted(lessons, key=lambda x: x.get("lesson_num", 0))

    for i, lesson in enumerate(sorted_lessons):
        lesson_num = lesson.get("lesson_num")
        if not lesson_num or lesson_num not in PERIOD_TIMES:
            continue

        name = lesson.get("name", "")
        room = _clean_room(lesson.get("room", ""))
        teachers = lesson.get("teachers") or [""]
        teacher = teachers[0] if teachers else ""
        has_room_change = "previous_room" in lesson

        sh, sm, eh, em = PERIOD_TIMES[lesson_num]
        start_dt = _make_jst_dt(date_str, sh, sm)
        end_dt = _make_jst_dt(date_str, eh, em)

        base = {
            "courseName": name,
            "room": room,
            "teacher": teacher,
            "period": lesson_num,
            "startDate": _apple_ts(start_dt),
            "endDate": _apple_ts(end_dt),
            "hasRoomChange": has_room_change,
            "newRoom": room if has_room_change else None,
        }

        # ---- upcoming ----
        if not push_only:
            if i == 0:
                up_dt = start_dt - timedelta(minutes=30)
            else:
                prev_num = sorted_lessons[i - 1].get("lesson_num", 0)
                if prev_num in PERIOD_TIMES:
                    _, _, peh, pem = PERIOD_TIMES[prev_num]
                    prev_end_dt = _make_jst_dt(date_str, peh, pem)
                    gap_minutes = (start_dt - prev_end_dt).total_seconds() / 60
                    if gap_minutes > 10:
                        # 長い休憩: upcoming は授業開始10分前から
                        up_dt = start_dt - timedelta(minutes=10)
                    else:
                        # 短い休憩: upcoming は前の授業終了直後から
                        up_dt = prev_end_dt
                else:
                    up_dt = start_dt - timedelta(minutes=30)

            transitions.append({
                "timestamp": up_dt.timestamp(),
                "content_state": {
                    **base,
                    "phase": "upcoming",
                    "countdownDate": _apple_ts(start_dt),
                },
            })

            # ---- imminent (local only) ----
            imm_dt = start_dt - timedelta(minutes=5)
            if imm_dt > up_dt:
                transitions.append({
                    "timestamp": imm_dt.timestamp(),
                    "content_state": {
                        **base,
                        "phase": "imminent",
                        "countdownDate": _apple_ts(start_dt),
                    },
                })

        # ---- inProgress (push-worthy) ----
        transitions.append({
            "timestamp": start_dt.timestamp(),
            "content_state": {
                **base,
                "phase": "inProgress",
                "countdownDate": _apple_ts(end_dt),
            },
        })

        # ---- breakTime or finished ----
        next_lesson = sorted_lessons[i + 1] if i + 1 < len(sorted_lessons) else None

        if next_lesson:
            next_num = next_lesson.get("lesson_num", 0)
            if next_num in PERIOD_TIMES:
                nsh, nsm, neh, nem = PERIOD_TIMES[next_num]
                next_start_dt = _make_jst_dt(date_str, nsh, nsm)

                # Adjacent class rule: if next starts at or before current ends, skip breakTime
                if next_start_dt > end_dt:
                    gap_minutes = (next_start_dt - end_dt).total_seconds() / 60

                    # 10分超 → breakTime を表示（昼休み等）
                    # 10分以下 → breakTime スキップ、upcoming がそのまま続く
                    if gap_minutes > 10:
                        next_name = next_lesson.get("name", "")
                        next_room = _clean_room(next_lesson.get("room", ""))
                        next_teachers = next_lesson.get("teachers") or [""]
                        next_teacher = next_teachers[0] if next_teachers else ""

                        transitions.append({
                            "timestamp": end_dt.timestamp(),
                            "content_state": {
                                **base,
                                "phase": "breakTime",
                                "countdownDate": _apple_ts(next_start_dt),
                                "hasRoomChange": False,
                                "newRoom": None,
                                "nextCourseName": next_name,
                                "nextCourseRoom": next_room,
                                "nextCourseTeacher": next_teacher,
                                "nextCoursePeriod": next_num,
                            },
                        })
        else:
            # Last class → finished
            transitions.append({
                "timestamp": end_dt.timestamp(),
                "content_state": {
                    **base,
                    "phase": "finished",
                    "countdownDate": _apple_ts(end_dt),
                    "hasRoomChange": False,
                    "newRoom": None,
                },
            })

    transitions.sort(key=lambda x: x["timestamp"])
    return transitions


# ---------------------------------------------------------------------------
# Schedule pushes for a user
# ---------------------------------------------------------------------------

async def schedule_live_activity_pushes(
    username: str,
    encrypted_password: str,
    la_token: str,
    activity_id: str,
) -> int:
    """Fetch today's schedule and store transition events in Redis.

    Returns the number of transitions scheduled.
    """
    # # ---- 测试用假数据: 22311330mw ----
    # if username == "22311330mw":
    #     from datetime import date as _date
    #     _today = _date.today()
    #     _now = datetime.now(JAPAN_TZ)
    #     # 1限目: +7分後開始、3分間
    #     _s1 = _now + timedelta(minutes=7)
    #     _e1 = _s1 + timedelta(minutes=3)
    #     # 2限目: 1限目終了の12分後に開始（昼休みテスト）、3分間
    #     _s2 = _e1 + timedelta(minutes=12)
    #     _e2 = _s2 + timedelta(minutes=3)
    #     _midnight = datetime(_today.year, _today.month, _today.day, 23, 59, tzinfo=JAPAN_TZ)
    #     if _e2 > _midnight:
    #         _e2 = _midnight
    #     PERIOD_TIMES[3] = (_s1.hour, _s1.minute, _e1.hour, _e1.minute)
    #     PERIOD_TIMES[4] = (_s2.hour, _s2.minute, _e2.hour, _e2.minute)
    #     _weekdays_jp = ["月", "火", "水", "木", "金", "土", "日"]
    #     data = {
    #         "date_info": {
    #             "date": _today.strftime("%Y/%m/%d"),
    #             "day_of_week": _weekdays_jp[_today.weekday()],
    #         },
    #         "all_day_events": [],
    #         "time_table": [
    #             {
    #                 "lesson_num": 3,
    #                 "name": "情報工学概論",
    #                 "teachers": ["中村 教授"],
    #                 "room": "101",
    #             },
    #             {
    #                 "lesson_num": 4,
    #                 "name": "データサイエンス入門",
    #                 "teachers": ["田中 准教授"],
    #                 "room": "305",
    #                 "previous_room": "242",
    #             },
    #         ],
    #     }
    #     logger.info("LA TEST: PERIOD_TIMES[5] = %s", PERIOD_TIMES[5])
    # # ---- 测试用假数据 END ----
    # else:
    gakuen = GakuenAPI("", "", "https://next.tama.ac.jp", http_proxy=HTTP_PROXY)
    try:
        from datetime import date
        data = await gakuen.get_later_user_schedule(
            username, encrypted_password, target_date=date.today()
        )
    except GakuenAPIError as e:
        logger.error("LA schedule fetch failed for %s: %s", username, e)
        raise
    finally:
        await gakuen.close()

    if not data.get("time_table"):
        logger.info("LA: %s has no classes today", username)
        return 0

    # Filter cancelled
    active = [
        t for t in data["time_table"]
        if not (t.get("special_tags") and "休講" in t["special_tags"])
        and t.get("name")
    ]
    if not active:
        logger.info("LA: %s all classes cancelled", username)
        return 0

    date_str = data["date_info"]["date"]
    transitions = compute_transitions(active, date_str, push_only=False)

    # Store token
    token_key = f"la:tokens:{username}"
    token_data = json.dumps({
        "token": la_token,
        "registered_at": datetime.now(JAPAN_TZ).isoformat(),
    })
    await redis.hset(token_key, activity_id, token_data)  # type: ignore[misc]
    await redis.expire(token_key, 86400)  # type: ignore[misc]

    # Store transitions in sorted set
    trans_key = f"la:transitions:{username}"
    await redis.delete(trans_key)

    now_ts = datetime.now(JAPAN_TZ).timestamp()
    stored = 0
    for t in transitions:
        if t["timestamp"] <= now_ts:
            continue
        member = json.dumps(t["content_state"])
        await redis.zadd(trans_key, {member: t["timestamp"]})
        stored += 1

    # TTL: midnight JST + 1 hour
    now_jst = datetime.now(JAPAN_TZ)
    midnight = JAPAN_TZ.localize(
        datetime.combine(now_jst.date() + timedelta(days=1), dt_time(0, 0))
    )
    ttl = int((midnight - now_jst).total_seconds()) + 3600
    if stored > 0:
        await redis.expire(trans_key, ttl)

    logger.info("LA: %s scheduled %d transitions (of %d total)", username, stored, len(transitions))
    return stored


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

async def dispatch_live_activity_pushes() -> int:
    """Check all users' transition sorted sets and send due pushes.

    Returns the total number of pushes sent.
    """
    now_ts = datetime.now(JAPAN_TZ).timestamp()
    total_sent = 0

    # Collect all transition keys
    keys: list[str] = []
    async for key in redis.scan_iter("la:transitions:*"):
        keys.append(key if isinstance(key, str) else key.decode())

    for key in keys:
        username = key.split(":")[2]

        while True:
            # Atomic pop of due event
            member = await redis.eval(_LUA_POP_DUE, 1, key, str(now_ts))  # type: ignore[misc]
            if member is None:
                break

            if isinstance(member, bytes):
                member = member.decode()
            content_state = json.loads(member)

            # Get all tokens for this user
            token_key = f"la:tokens:{username}"
            tokens = await redis.hgetall(token_key)  # type: ignore[misc]
            if not tokens:
                continue

            is_finished = content_state.get("phase") == "finished"

            for activity_id_raw, token_json_raw in tokens.items():
                aid = activity_id_raw if isinstance(activity_id_raw, str) else activity_id_raw.decode()
                tj = token_json_raw if isinstance(token_json_raw, str) else token_json_raw.decode()
                token_data = json.loads(tj)
                la_token = token_data["token"]

                success = await _send_la_push(la_token, content_state, is_finished)
                if success:
                    total_sent += 1
                else:
                    # Invalid token → clean up
                    await redis.hdel(token_key, aid)  # type: ignore[misc]
                    logger.info("LA: removed invalid token for %s/%s", username, aid)

    return total_sent


async def _send_la_push(
    device_token: str,
    content_state: dict,
    is_end: bool,
) -> bool:
    """Send a single Live Activity APNs push. Returns True on success."""
    now_ts = int(datetime.now(JAPAN_TZ).timestamp())

    # stale-date: countdownDate を Unix timestamp に変換
    # countdownDate は Apple reference date (2001-01-01) からの秒数
    countdown_apple = content_state.get("countdownDate", 0)
    stale_ts = int(countdown_apple + _APPLE_EPOCH_OFFSET)

    payload: dict = {
        "aps": {
            "timestamp": now_ts,
            "event": "end" if is_end else "update",
            "content-state": content_state,
            "stale-date": stale_ts,
        },
    }

    if is_end:
        payload["aps"]["dismissal-date"] = now_ts + 900  # 15 minutes

    notification = NotificationRequest(
        device_token=device_token,
        message=payload,
        notification_id=str(uuid4()),
        push_type=PushType.LIVEACTIVITY,
        priority=10,
        apns_topic=_LA_APNS_TOPIC,
    )

    try:
        apns = get_apns_client()
        result = await apns.send_notification(notification)
        if result.is_successful:
            logger.debug("LA push sent: phase=%s", content_state.get("phase"))
            return True
        else:
            logger.warning("LA push failed: %s", result.description)
            # Check for unregistered / invalid token errors
            if result.description in ("Unregistered", "BadDeviceToken", "ExpiredToken"):
                return False
            return True  # Don't remove token for transient errors
    except Exception as e:
        logger.error("LA push error: %s", e)
        return True  # Don't remove token on network errors
