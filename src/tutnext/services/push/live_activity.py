"""
Live Activity push scheduling.

Computes transition events for a user's daily schedule and dispatches
them as APNs ``liveactivity`` pushes at the appropriate times.

Transition logic here MUST stay in sync with the iOS
``LiveActivityScheduler.computeTransitions`` implementation.

Redis keys used by this module
------------------------------
``la:tokens:{username}``           hash activity_id -> token JSON   (TTL → midnight + 1 h)
``la:transitions:{username}``      zset content-state JSON by ts    (TTL → midnight + 1 h)
``la:pts:{username}``              push-to-start token              (TTL 30 d)
``la:pts:pw:{username}``           fallback encryptedPassword       (TTL 30 d)
``la:start:{username}``            zset with exactly one start event (TTL → midnight + 1 h)
``la:pending_schedule:{username}`` JSON retry record for /register  (TTL → midnight + 1 h)
``la:schedule:{username}:{date}``  cached raw schedule payload      (TTL 300 s)
"""
import json
import logging
from datetime import date as date_type
from datetime import datetime, timedelta
from datetime import time as dt_time
from uuid import uuid4

from tutnext.config import JAPAN_TZ, live_activity_topic, redis
from tutnext.services.gakuen.client import GakuenAPIError
from tutnext.services.gakuen.session_manager import get_session_manager
from tutnext.services.push.apns_client import NotificationRequest, PushType, get_apns_client

logger = logging.getLogger(__name__)

# Apple reference date offset: 2001-01-01 00:00:00 UTC
_APPLE_EPOCH_OFFSET = 978307200.0

# APNs topic for Live Activity (main app bundle ID, NOT widget) — resolved lazily
# via config.live_activity_topic() because Worker secrets are unavailable at import.

# Swift ActivityAttributes type name (required by push-to-start payloads)
_LA_ATTRIBUTES_TYPE = "ClassLiveActivityAttributes"

# Grace period added on top of the next expected transition for ``stale-date``
_STALE_GRACE_SECONDS = 600

# Transient push failure retry policy
_RETRY_DELAY_SECONDS = 30
_MAX_PUSH_ATTEMPTS = 3

# /register scheduling retry policy
_PENDING_RETRY_INTERVAL = 60
_MAX_PENDING_ATTEMPTS = 5

# Push-to-start token TTL (30 days)
_PTS_TTL = 30 * 86400

# Raw schedule cache TTL
_SCHEDULE_CACHE_TTL = 300

# Phases that are cheap client-side countdown states → low priority (no budget cost)
_LOW_PRIORITY_PHASES = frozenset({"upcoming", "imminent"})

# APNs reasons that mean the device token must be dropped
_INVALID_TOKEN_REASONS = frozenset({"Unregistered", "BadDeviceToken", "ExpiredToken"})

# Private (non content-state) keys carried inside sorted-set members
_PRIVATE_KEYS = ("_next_ts", "_attempt")

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


def _display_name(name: str, teachers: list | None) -> str:
    """Strip the teacher-name suffix T-NEXT appends to the course name.

    ``"ホームゼミVI 小林 英夫"`` with ``teachers=["小林 英夫"]`` → ``"ホームゼミVI"``.
    Mirrors the iOS display-name logic so both sides render identically.
    """
    if not name:
        return name
    teacher = (teachers or [""])[0] if teachers else ""
    if not teacher:
        return name
    suffix = f" {teacher}"
    if name.endswith(suffix) and len(name) > len(suffix):
        return name[: -len(suffix)].rstrip()
    return name


def _midnight_ttl(from_date: date_type | None = None) -> int:
    """Seconds until the midnight following ``from_date`` (default: today) + 1 h."""
    now_jst = datetime.now(JAPAN_TZ)
    base = from_date or now_jst.date()
    midnight = JAPAN_TZ.localize(
        datetime.combine(base + timedelta(days=1), dt_time(0, 0))
    )
    return max(int((midnight - now_jst).total_seconds()), 0) + 3600


def _strip_private(member: dict) -> dict:
    """Return the content-state without the private bookkeeping keys."""
    return {k: v for k, v in member.items() if k not in _PRIVATE_KEYS}


def _push_priority(phase: str) -> int:
    """APNs priority: 5 for countdown-only phases (no per-hour budget cost)."""
    return 5 if phase in _LOW_PRIORITY_PHASES else 10


def _decode(value) -> str:
    return value if isinstance(value, str) else value.decode()


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

        teachers = lesson.get("teachers") or [""]
        teacher = teachers[0] if teachers else ""
        name = _display_name(lesson.get("name", ""), teachers)
        room = _clean_room(lesson.get("room", ""))
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
                        next_teachers = next_lesson.get("teachers") or [""]
                        next_teacher = next_teachers[0] if next_teachers else ""
                        next_name = _display_name(next_lesson.get("name", ""), next_teachers)
                        next_room = _clean_room(next_lesson.get("room", ""))

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
# Schedule fetching (with a short-lived cache shared by /register + retries)
# ---------------------------------------------------------------------------

def active_lessons(data: dict) -> list[dict]:
    """Return the day's lessons with cancelled / unnamed entries removed."""
    return [
        t for t in (data.get("time_table") or [])
        if not (t.get("special_tags") and "休講" in t["special_tags"])
        and t.get("name")
    ]


async def fetch_day_schedule(
    username: str,
    encrypted_password: str,
    target_date: date_type,
) -> dict:
    """Fetch a day's schedule, reusing a 5 minute Redis cache when possible."""
    cache_key = f"la:schedule:{username}:{target_date.isoformat()}"
    cached = await redis.get(cache_key)
    if cached:
        logger.debug("LA: schedule cache hit for %s/%s", username, target_date)
        return json.loads(_decode(cached))

    async with get_session_manager().acquire(username, encrypted_password) as gakuen:
        try:
            data = await gakuen.get_later_user_schedule(
                username, encrypted_password, target_date=target_date, skip_login=True
            )
        except GakuenAPIError as e:
            logger.error("LA schedule fetch failed for %s: %s", username, e)
            raise

    try:
        await redis.set(cache_key, json.dumps(data), ex=_SCHEDULE_CACHE_TTL)
    except Exception as e:  # cache failures must never break scheduling
        logger.warning("LA: schedule cache write failed for %s: %s", username, e)
    return data


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------

async def store_la_token(username: str, la_token: str, activity_id: str) -> None:
    """Persist an update token for a running Live Activity.

    The hash expires at midnight JST + 1 h, like the other per-day keys: an
    activity never outlives its day, and a stale token must not make the next
    morning's push-to-start look like "already running".
    """
    token_key = f"la:tokens:{username}"
    token_data = json.dumps({
        "token": la_token,
        "registered_at": datetime.now(JAPAN_TZ).isoformat(),
    })
    await redis.hset(token_key, activity_id, token_data)  # type: ignore[misc]
    await redis.expire(token_key, _midnight_ttl())  # type: ignore[misc]


async def store_push_to_start_token(
    username: str,
    token: str,
    encrypted_password: str | None = None,
) -> None:
    """Persist a push-to-start token (30 d TTL, refreshed on every call)."""
    await redis.set(f"la:pts:{username}", token, ex=_PTS_TTL)
    if encrypted_password:
        await redis.set(f"la:pts:pw:{username}", encrypted_password, ex=_PTS_TTL)


# ---------------------------------------------------------------------------
# Transition storage
# ---------------------------------------------------------------------------

def _annotate_next_ts(transitions: list[dict]) -> list[dict]:
    """Attach ``_next_ts`` (the following transition's timestamp) to each entry.

    Deterministic at scheduling time, so the dispatcher can derive ``stale-date``
    without an extra Redis round trip.
    """
    annotated: list[dict] = []
    for idx, t in enumerate(transitions):
        member = dict(t["content_state"])
        if idx + 1 < len(transitions):
            member["_next_ts"] = transitions[idx + 1]["timestamp"]
        annotated.append({"timestamp": t["timestamp"], "member": member})
    return annotated


async def store_transitions(username: str, transitions: list[dict]) -> int:
    """Replace the user's transition sorted set with the future transitions."""
    trans_key = f"la:transitions:{username}"
    await redis.delete(trans_key)

    now_ts = datetime.now(JAPAN_TZ).timestamp()
    stored = 0
    for entry in _annotate_next_ts(transitions):
        # Past transitions are dropped: the client catches its own state up.
        if entry["timestamp"] <= now_ts:
            continue
        await redis.zadd(trans_key, {json.dumps(entry["member"]): entry["timestamp"]})
        stored += 1

    if stored > 0:
        await redis.expire(trans_key, _midnight_ttl())
    return stored


# ---------------------------------------------------------------------------
# Scheduling for a user
# ---------------------------------------------------------------------------

async def schedule_live_activity_pushes(
    username: str,
    encrypted_password: str,
    la_token: str | None = None,
    activity_id: str | None = None,
) -> int:
    """Fetch today's schedule and store transition events in Redis.

    The token (when given) is expected to be stored by the caller *before* this
    runs, so a T-NEXT failure can never cost us the token.

    Returns the number of transitions scheduled.
    """
    data = await fetch_day_schedule(username, encrypted_password, date_type.today())

    active = active_lessons(data)
    if not active:
        logger.info("LA: %s has no active classes today", username)
        await redis.delete(f"la:transitions:{username}")
        return 0

    date_str = data["date_info"]["date"]
    transitions = compute_transitions(active, date_str, push_only=False)
    stored = await store_transitions(username, transitions)

    logger.info("LA: %s scheduled %d transitions (of %d total)", username, stored, len(transitions))
    return stored


# ---------------------------------------------------------------------------
# Pending-schedule retry queue (used when T-NEXT is down at /register time)
# ---------------------------------------------------------------------------

async def enqueue_pending_schedule(username: str, encrypted_password: str) -> None:
    """Remember that this user's transitions still need to be computed."""
    key = f"la:pending_schedule:{username}"
    payload = json.dumps({
        "encryptedPassword": encrypted_password,
        "attempts": 0,
        "next_try": datetime.now(JAPAN_TZ).timestamp() + _PENDING_RETRY_INTERVAL,
    })
    await redis.set(key, payload, ex=_midnight_ttl())


async def _retry_pending_schedule(key: str, now_ts: float) -> None:
    """Retry one pending /register scheduling job if it is due."""
    raw = await redis.get(key)
    if not raw:
        return
    username = key.split(":", 2)[2]
    record = json.loads(_decode(raw))
    if record.get("next_try", 0) > now_ts:
        return

    attempts = int(record.get("attempts", 0)) + 1
    try:
        count = await schedule_live_activity_pushes(username, record["encryptedPassword"])
        await redis.delete(key)
        logger.info("LA: pending schedule for %s succeeded on attempt %d (%d transitions)",
                    username, attempts, count)
        return
    except Exception as e:
        if attempts >= _MAX_PENDING_ATTEMPTS:
            await redis.delete(key)
            logger.warning("LA: pending schedule for %s gave up after %d attempts: %s",
                           username, attempts, e)
            return
        record["attempts"] = attempts
        record["next_try"] = now_ts + _PENDING_RETRY_INTERVAL
        await redis.set(key, json.dumps(record), ex=_midnight_ttl())
        logger.warning("LA: pending schedule for %s failed (attempt %d): %s", username, attempts, e)


async def retry_pending_schedules() -> None:
    """Scan and retry all pending /register scheduling jobs that are due."""
    now_ts = datetime.now(JAPAN_TZ).timestamp()
    keys = [_decode(k) async for k in redis.scan_iter("la:pending_schedule:*")]
    for key in keys:
        try:
            await _retry_pending_schedule(key, now_ts)
        except Exception as e:
            logger.error("LA: pending schedule retry error for %s: %s", key, e)


# ---------------------------------------------------------------------------
# Push-to-start pre-scheduling (runs from the 20:30 JST daily job)
# ---------------------------------------------------------------------------

async def schedule_push_to_start(username: str, data: dict) -> bool:
    """Store tomorrow's *first* transition for a push-to-start capable user.

    Only the first ``upcoming`` transition is stored: the remaining ones are
    scheduled by ``/register`` once the device has started the activity and
    reported its update token.

    Returns True when a start event was stored.
    """
    pts_token = await redis.get(f"la:pts:{username}")
    if not pts_token:
        return False

    start_key = f"la:start:{username}"
    active = active_lessons(data)
    if not active:
        await redis.delete(start_key)
        return False

    date_str = data["date_info"]["date"]
    transitions = compute_transitions(active, date_str, push_only=False)
    if not transitions:
        await redis.delete(start_key)
        return False

    annotated = _annotate_next_ts(transitions)
    first = annotated[0]

    await redis.delete(start_key)
    await redis.zadd(start_key, {json.dumps(first["member"]): first["timestamp"]})
    await redis.expire(start_key, _midnight_ttl(datetime.strptime(date_str, "%Y/%m/%d").date()))
    logger.info("LA: %s push-to-start scheduled for %s (%s)",
                username, date_str, first["member"].get("courseName"))
    return True


async def schedule_push_to_start_for_unregistered_users(known_usernames: set[str]) -> int:
    """Pre-schedule start events for push-to-start users absent from the users table.

    ``send_9pm_push_pool`` only iterates DB users; users who registered a
    push-to-start token without a device push registration are handled here
    using the encryptedPassword captured by ``/live-activity/push-to-start``.
    """
    scheduled = 0
    keys = [_decode(k) async for k in redis.scan_iter("la:pts:*")]
    for key in keys:
        username = key.split(":", 2)[2]
        if username.startswith("pw:") or username in known_usernames:
            continue
        password = await redis.get(f"la:pts:pw:{username}")
        if not password:
            continue
        try:
            data = await fetch_day_schedule(
                username, _decode(password), date_type.today() + timedelta(days=1)
            )
            if await schedule_push_to_start(username, data):
                scheduled += 1
        except Exception as e:
            logger.warning("LA: push-to-start pre-scheduling failed for %s: %s", username, e)
    return scheduled


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

async def _due_keys(pattern: str, now_ts: float) -> list[str]:
    """Keys under *pattern* with something due. On D1 this is one query: scanning every
    user's key and popping each (one D1 round trip per user) made every cron run take
    ~25 s once all users had transitions (2026-10-04 evening)."""
    zkeys_due = getattr(redis, "zkeys_due", None)
    if zkeys_due is not None:
        return [_decode(k) for k in await zkeys_due(pattern, now_ts)]
    return [_decode(k) async for k in redis.scan_iter(pattern)]


async def dispatch_live_activity_pushes() -> int:
    """Check all users' transition / start sorted sets and send due pushes.

    Returns the total number of pushes sent.
    """
    now_ts = datetime.now(JAPAN_TZ).timestamp()
    total_sent = 0

    keys = await _due_keys("la:transitions:*", now_ts)
    for key in keys:
        username = key.split(":", 2)[2]
        while True:
            member_raw = await redis.eval(_LUA_POP_DUE, 1, key, str(now_ts))  # type: ignore[misc]
            if member_raw is None:
                break
            total_sent += await _dispatch_transition(username, key, json.loads(_decode(member_raw)))

    start_keys = await _due_keys("la:start:*", now_ts)
    for key in start_keys:
        username = key.split(":", 2)[2]
        while True:
            member_raw = await redis.eval(_LUA_POP_DUE, 1, key, str(now_ts))  # type: ignore[misc]
            if member_raw is None:
                break
            total_sent += await _dispatch_start(username, key, json.loads(_decode(member_raw)))

    return total_sent


async def _reenqueue(key: str, member: dict, ttl_date: date_type | None = None) -> None:
    """Re-add a failed member for another attempt, or drop it after 3 tries."""
    attempts = int(member.get("_attempt", 0)) + 1
    phase = member.get("phase")
    if attempts >= _MAX_PUSH_ATTEMPTS:
        logger.warning("LA: giving up on %s push after %d attempts (%s)", phase, attempts, key)
        return
    member["_attempt"] = attempts
    retry_ts = datetime.now(JAPAN_TZ).timestamp() + _RETRY_DELAY_SECONDS
    await redis.zadd(key, {json.dumps(member): retry_ts})
    await redis.expire(key, _midnight_ttl(ttl_date))
    logger.info("LA: re-queued %s push (attempt %d) for %s", phase, attempts, key)


async def _dispatch_transition(username: str, key: str, member: dict) -> int:
    """Send one popped transition to every live token of the user."""
    token_key = f"la:tokens:{username}"
    tokens = await redis.hgetall(token_key)  # type: ignore[misc]
    if not tokens:
        return 0

    content_state = _strip_private(member)
    is_finished = content_state.get("phase") == "finished"
    next_ts = member.get("_next_ts")

    sent = 0
    ended = 0
    needs_retry = False
    for activity_id_raw, token_json_raw in tokens.items():
        aid = _decode(activity_id_raw)
        token_data = json.loads(_decode(token_json_raw))
        result = await _send_la_push(
            token_data["token"], content_state, next_ts, event="end" if is_finished else "update"
        )
        if result == "ok":
            sent += 1
            if is_finished:
                # The activity is over and its update token is dead. The app is
                # usually not running to call /unregister, so drop it here; a
                # lingering token would make _dispatch_start skip tomorrow's start.
                await redis.hdel(token_key, aid)  # type: ignore[misc]
                ended += 1
                logger.info("LA: removed ended activity token for %s/%s", username, aid)
        elif result == "invalid_token":
            await redis.hdel(token_key, aid)  # type: ignore[misc]
            logger.info("LA: removed invalid token for %s/%s", username, aid)
        else:
            needs_retry = True

    if ended:
        # Mirror /unregister: with no tokens left, the remaining transitions are moot.
        remaining: int = await redis.hlen(token_key)  # type: ignore[misc]
        if remaining == 0:
            await redis.delete(key)
            logger.info("LA: %s has no tokens left after end push → transitions deleted", username)

    if needs_retry:
        await _reenqueue(key, member)
    return sent


async def _dispatch_start(username: str, key: str, member: dict) -> int:
    """Send one popped push-to-start event, unless the activity is already running."""
    tokens = await redis.hlen(f"la:tokens:{username}")  # type: ignore[misc]
    if tokens:
        logger.info("LA: %s already has a running activity → skip start push", username)
        return 0

    pts_token = await redis.get(f"la:pts:{username}")
    if not pts_token:
        logger.info("LA: %s has no push-to-start token → skip start push", username)
        return 0

    content_state = _strip_private(member)
    result = await _send_la_push(
        _decode(pts_token), content_state, member.get("_next_ts"), event="start"
    )
    if result == "ok":
        return 1
    if result == "invalid_token":
        await redis.delete(f"la:pts:{username}")
        logger.info("LA: removed invalid push-to-start token for %s", username)
        return 0

    await _reenqueue(key, member)
    return 0


async def _build_payload(content_state: dict, next_ts: float | None, event: str) -> dict:
    """Build the APNs ``aps`` payload for one Live Activity push."""
    now_ts = int(datetime.now(JAPAN_TZ).timestamp())
    aps: dict = {
        "timestamp": now_ts,
        "event": event,
        "content-state": content_state,
    }

    if event == "end":
        # No stale-date on the end push; the activity is dismissed shortly after.
        aps["dismissal-date"] = now_ts + 900  # 15 minutes
    else:
        # stale-date means "no update arrived when one was expected".
        if next_ts:
            aps["stale-date"] = int(next_ts) + _STALE_GRACE_SECONDS

    if event == "start":
        aps["attributes-type"] = _LA_ATTRIBUTES_TYPE
        aps["attributes"] = {}

    return {"aps": aps}


async def _send_la_push(
    device_token: str,
    content_state: dict,
    next_ts: float | None,
    *,
    event: str = "update",
) -> str:
    """Send a single Live Activity APNs push.

    Returns ``"ok"``, ``"invalid_token"`` (drop the token) or ``"retry"``
    (transient failure — the caller should re-queue).
    """
    payload = await _build_payload(content_state, next_ts, event)
    priority = 10 if event == "start" else _push_priority(content_state.get("phase", ""))

    notification = NotificationRequest(
        device_token=device_token,
        message=payload,
        notification_id=str(uuid4()),
        push_type=PushType.LIVEACTIVITY,
        priority=priority,
        apns_topic=live_activity_topic(),
    )

    try:
        apns = get_apns_client()
        result = await apns.send_notification(notification)
        if result.is_successful:
            logger.debug("LA push sent: event=%s phase=%s", event, content_state.get("phase"))
            return "ok"
        logger.warning("LA push failed: %s", result.description)
        if result.description in _INVALID_TOKEN_REASONS:
            return "invalid_token"
        return "retry"
    except Exception as e:
        logger.error("LA push error: %s", e)
        return "retry"
