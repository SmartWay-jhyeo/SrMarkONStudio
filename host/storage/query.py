"""저장한 레코드를 다시 꺼내 쓴다.

회전 정책(`paths.py`) 때문에 데이터가 여러 sqlite 파일에 걸쳐 있을 수
있다. 이 모듈의 함수들은 디렉터리 하나(`base_dir`)를 대상으로, 필요한
파일만 찾아 열어 조회한다.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from host.storage.paths import date_of

_COLUMNS = ("t", "seq", "type", "connector_id", "quantity", "value", "state", "raw")

#: `find_nearest`의 기본 "너무 묵었다" 문턱.
#:
#: 근거: 기본 `tx.period_ms`는 100ms 다(규격 §7.3 카탈로그 기본값) — 정상
#: 동작 중이면 표본이 그보다 훨씬 촘촘하다. 2000ms 는 그 20배로,
#: 카메라 프레임(최대 33ms 간격, §7.1 주석)에 이렇게 오래된 값을 붙이면
#: 명백히 잘못된 상관관계다. 호출자가 다른 주기를 쓰면(`tx.period_ms`를
#: 크게 잡았다든지) 인자로 바꾼다 — 이 기본값은 "적당히 큰 값"이 아니라
#: "정상 동작을 확실히 벗어난 값"을 노린 것이다.
DEFAULT_MAX_AGE_MS = 2000


@dataclass
class Nearest:
    """`find_nearest` 결과.

    `found`가 `False`면 후보가 아예 없었다는 뜻이고, 이때도 `stale`은
    `True`다(없는 값은 정의상 "너무 묵었다"의 극단이다) — 호출측이
    `stale`만 봐도 안전한 쪽으로 판단하게 한다.
    """

    record: dict | None
    age_ms: int | None
    found: bool
    stale: bool


def _files_covering(base_dir, t_start_ms: int, t_end_ms: int) -> list[Path]:
    """`[t_start_ms, t_end_ms]`와 겹칠 수 있는 회전 파일들을 고른다.

    파일 하루 안에 크기 회전으로 접미사 파일(`_01`·`_02`...)이 여럿일 수
    있으므로, 정확한 min/max t 를 보려면 결국 파일을 열어야 한다. 그런데
    조회마다 디렉터리의 모든 파일을 여는 것은 파일이 많아지면 느리다 —
    날짜 문자열만으로 먼저 후보를 좁히고, 그 날짜로 시작하는 접미사
    파일까지 전부 포함한다(startswith 매칭).
    """
    start_date = datetime.fromtimestamp(t_start_ms / 1000, tz=timezone.utc).date()
    end_date = datetime.fromtimestamp(t_end_ms / 1000, tz=timezone.utc).date()
    wanted = set()
    d = start_date
    while d <= end_date:
        wanted.add(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return [p for p in sorted(Path(base_dir).glob("records_*.sqlite3"))
            if date_of(p) in wanted]


def _filter_sql(type=None, connector_id=None, quantity=None):  # noqa: A002
    clauses = []
    params: list = []
    if type is not None:
        clauses.append("type = ?")
        params.append(type)
    if connector_id is not None:
        clauses.append("connector_id = ?")
        params.append(connector_id)
    if quantity is not None:
        clauses.append("quantity = ?")
        params.append(quantity)
    sql = "".join(f" AND {c}" for c in clauses)
    return sql, params


def _row_to_dict(row) -> dict:
    return dict(zip(_COLUMNS, row))


def to_record(row: dict) -> dict:
    """조회 결과 행의 `raw`를 원래 NDJSON dict 로 되살린다.

    행 자체에 담긴 컬럼(`value`·`connector_id` 등)은 조회용 파생값이라
    필드 마스크로 꺼진 항목이 빠져 있을 수 있다. 원문 그대로가 필요하면
    이 함수를 쓴다.
    """
    return json.loads(row["raw"])


def query_range(base_dir, t_start_ms: int, t_end_ms: int, *,
                 type=None, connector_id=None, quantity=None) -> list[dict]:  # noqa: A002
    """`[t_start_ms, t_end_ms]`(양끝 포함) 구간의 레코드를 `t` 순으로 반환한다."""
    where, params = _filter_sql(type, connector_id, quantity)
    out = []
    for path in _files_covering(base_dir, t_start_ms, t_end_ms):
        conn = sqlite3.connect(str(path))
        try:
            rows = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM records "
                f"WHERE t BETWEEN ? AND ?{where}",
                [t_start_ms, t_end_ms, *params],
            ).fetchall()
        finally:
            conn.close()
        out.extend(rows)
    out.sort(key=lambda r: r[0])
    return [_row_to_dict(r) for r in out]


def find_nearest(base_dir, t_target_ms: int, *, type=None, connector_id=None,  # noqa: A002
                  quantity=None, max_age_ms: int = DEFAULT_MAX_AGE_MS) -> Nearest:
    """`t_target_ms`에 가장 가까운 레코드를 찾는다.

    카메라 프레임 시각에 센서 값을 맞추는 것이 이 저장소의 최종 용도다
    (작업 브리핑). 앞(`t <= target`)과 뒤(`t >= target`) 후보를 각각 하나씩
    찾아 더 가까운 쪽을 고른다. **정확히 같은 거리면 이전 값을 고른다** —
    카메라는 "그 순간까지 알려진 값"으로 보정하는 것이 정상이고, 아직
    오지 않은 미래 값을 쓰면 인과관계가 뒤집힌다.

    `max_age_ms`보다 멀면 `stale=True`로 표시한다 — 값을 조용히 돌려주면
    "센서가 그 순간에 살아 있었다"는 거짓 인상을 준다. 그 판단은 호출측이
    하게 하되, 이 함수는 사실(나이)을 숨기지 않는다.
    """
    # 자정 근처 조회는 하루 앞뒤 파일에 답이 있을 수 있다 — 넉넉히 하루씩
    # 여유를 둔다. `max_age_ms`가 하루보다 크면 그만큼 더 넓힌다.
    margin_days = max(1, (max_age_ms // (24 * 3600 * 1000)) + 1)
    target_date = datetime.fromtimestamp(t_target_ms / 1000, tz=timezone.utc).date()
    wanted = {
        (target_date + timedelta(days=d)).strftime("%Y%m%d")
        for d in range(-margin_days, margin_days + 1)
    }
    files = [p for p in sorted(Path(base_dir).glob("records_*.sqlite3"))
             if date_of(p) in wanted]

    where, params = _filter_sql(type, connector_id, quantity)
    cols = ", ".join(_COLUMNS)
    before = None
    after = None
    for path in files:
        conn = sqlite3.connect(str(path))
        try:
            b = conn.execute(
                f"SELECT {cols} FROM records WHERE t <= ?{where} "
                f"ORDER BY t DESC LIMIT 1",
                [t_target_ms, *params],
            ).fetchone()
            a = conn.execute(
                f"SELECT {cols} FROM records WHERE t >= ?{where} "
                f"ORDER BY t ASC LIMIT 1",
                [t_target_ms, *params],
            ).fetchone()
        finally:
            conn.close()
        if b is not None and (before is None or b[0] > before[0]):
            before = b
        if a is not None and (after is None or a[0] < after[0]):
            after = a

    if before is None and after is None:
        return Nearest(record=None, age_ms=None, found=False, stale=True)

    candidates = [r for r in (before, after) if r is not None]
    best = min(
        candidates,
        key=lambda r: (abs(r[0] - t_target_ms), 0 if r is before else 1),
    )
    age = abs(best[0] - t_target_ms)
    return Nearest(
        record=_row_to_dict(best), age_ms=age, found=True,
        stale=age > max_age_ms,
    )
