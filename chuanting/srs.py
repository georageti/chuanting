"""复习排程：FSRS-5，公式和参数与川打 src/js/srs.js 一致（官方默认参数，按天排程）。

评分按拼写结果自动给：
  1 忘记：错了 3 次以上，或者看了答案    2 模糊：错一两次，或者看过提示    3 记得：一次拼对
"""
from __future__ import annotations

import math
from datetime import date, timedelta

W = [0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046, 1.54575, 0.1192,
     1.01925, 1.9395, 0.11, 0.29605, 2.2698, 0.2315, 2.9898, 0.51655, 0.6621]
DECAY = -0.5
FACTOR = 19 / 81
RETENTION = 0.9
MAX_DAYS = 3650
MASTERED_DAYS = 60
AGAIN, HARD, GOOD = 1, 2, 3


def today() -> str:
    return date.today().isoformat()


def add_days(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def clamp_d(d: float) -> float:
    return min(10.0, max(1.0, d))


def init_s(g: int) -> float:
    return max(W[g - 1], 0.1)


def init_d(g: int) -> float:
    return clamp_d(W[4] - math.exp(W[5] * (g - 1)) + 1)


def recall(t: float, s: float) -> float:
    return (1 + FACTOR * t / s) ** DECAY


def interval(s: float) -> int:
    days = (s / FACTOR) * (RETENTION ** (1 / DECAY) - 1)
    return min(MAX_DAYS, max(1, round(days)))


def next_d(d: float, g: int) -> float:
    delta = -W[6] * (g - 3)
    damped = d + delta * (10 - d) / 9
    return clamp_d(W[7] * init_d(4) + (1 - W[7]) * damped)


def recall_s(d: float, s: float, r: float, g: int) -> float:
    hard = W[15] if g == HARD else 1
    return s * (1 + math.exp(W[8]) * (11 - d) * s ** (-W[9]) * (math.exp((1 - r) * W[10]) - 1) * hard)


def forget_s(d: float, s: float, r: float) -> float:
    long = W[11] * d ** (-W[12]) * ((s + 1) ** W[13] - 1) * math.exp((1 - r) * W[14])
    return min(long, s / math.exp(W[17] * W[18]), s)


def grade(mistakes: int, peeked: bool, hinted: bool = False) -> int:
    if mistakes >= 3 or peeked:
        return AGAIN
    if mistakes > 0 or hinted:
        return HARD
    return GOOD


def learn(g: int, day: str | None = None) -> dict:
    """第一次学：刚听过就拼，靠的是短时记忆，最多按“模糊”起步（与川打一致）"""
    day = day or today()
    g = min(g, HARD)
    s = init_s(g)
    return {"s": s, "df": init_d(g), "last": day, "due": add_days(day, interval(s)), "reps": 1,
            "lapses": 1 if g == AGAIN else 0, "learned": day}


def review(state: dict, g: int, day: str | None = None) -> dict:
    day = day or today()
    st = dict(state)
    if st.get("last") == day:  # 同一天第二遍不改排程
        return st
    elapsed = max(0, days_between(st.get("last") or st.get("learned") or day, day))
    r = recall(elapsed, st["s"])
    st["reps"] = st.get("reps", 0) + 1
    if g == AGAIN:
        st["lapses"] = st.get("lapses", 0) + 1
        st["s"] = forget_s(st["df"], st["s"], r)
    else:
        st["s"] = recall_s(st["df"], st["s"], r, g)
    st["df"] = next_d(st["df"], g)
    st["last"] = day
    st["due"] = add_days(day, interval(st["s"]))
    return st
