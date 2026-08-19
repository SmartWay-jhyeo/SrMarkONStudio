"""저장 파일 회전 정책 — 순수 함수만 있다. sqlite 를 열지 않는다.

레코드는 초당 최대 100줄(작업 브리핑 기준 — `ain`·`i2c`가 10~100ms 마다
하나씩), 하루 최대 864만 줄까지 온다. 파일 하나가 무한히 커지면 안 되므로
**하루 단위**로 새 파일을 연다 — 파일명에 날짜가 그대로 박히므로 "그
날짜의 데이터가 있는 파일"을 시각 범위 조회가 디렉터리를 열어보지 않고도
바로 좁힐 수 있다(`query.py`).

🔴 날짜 기준은 **레코드의 `t`(획득 시각)**이지 수집기가 관찰한 지금
시각이 아니다. `t`는 보드가 확정한다(설계 원칙 2) — 파일 이름도 그것을
따라야 "이 시간대 데이터가 어느 파일에 있나"가 어긋나지 않는다.

하루치가 예상보다 커지는 경우(설정을 최소 주기로 몰아 썼다든지)를 대비해
크기 상한도 둔다. 넘으면 같은 날짜 안에서 접미사를 올려 다음 파일로
넘어간다 — 파일이 무한히 자라 sqlite 인덱스가 느려지는 것을 막는다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: 파일 하나의 소프트 상한.
#:
#: 근거: 최대 데이터율(초당 100줄, 원문 한 줄 150~250바이트)이 하루 종일
#: 유지되면 원문만 1.3~1.7 GB 다. sqlite 인덱스·오버헤드를 얹으면 하루치가
#: 이 근처이거나 넘을 수 있다. 그보다 훨씬 작게 잡으면 평상시(초당
#: 10줄 안팎, `RAW_BUFFER_MAXLEN` 주석과 같은 계산)에도 하루가 여러
#: 파일로 쪼개져 "하루=파일 하나"라는 직관이 자주 깨진다. 512 MiB 는 그
#: 사이 — 정상 운용에서는 거의 안 걸리고, 몰아 써도 하루 안에 몇 개
#: 이상으로는 안 늘어난다.
DEFAULT_MAX_FILE_BYTES = 512 * 1024 * 1024

_STEM_RE = re.compile(r"^records_(\d{8})(?:_(\d{2}))?$")
_EXT = ".sqlite3"


def file_stem_for(t_ms: int, suffix: int = 0) -> str:
    """레코드 획득 시각(`t`, epoch_ms)으로부터 파일 이름(확장자 제외)을 만든다."""
    date = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).strftime("%Y%m%d")
    if suffix == 0:
        return f"records_{date}"
    return f"records_{date}_{suffix:02d}"


def file_path_for(base_dir, t_ms: int, suffix: int = 0) -> Path:
    return Path(base_dir) / f"{file_stem_for(t_ms, suffix)}{_EXT}"


def date_of(path) -> str | None:
    """파일 이름에서 `YYYYMMDD`를 뽑는다. 형식이 안 맞으면 `None`."""
    m = _STEM_RE.match(Path(path).stem)
    return m.group(1) if m else None


def _suffix_of(path: Path) -> int:
    m = _STEM_RE.match(path.stem)
    if not m:
        return -1
    return int(m.group(2)) if m.group(2) else 0


def next_path_for(base_dir, current: Path | None, t_ms: int, *,
                   max_bytes: int = DEFAULT_MAX_FILE_BYTES) -> Path:
    """지금 파일(`current`)을 이어 쓸지, 새 파일로 넘어갈지 정한다.

    `current`가 `None`이면(수집기 첫 호출, 또는 프로세스 재시작 직후)
    그 날짜에 이미 파일이 있는지 디렉터리에서 찾는다 — 재시작할 때마다
    파일이 늘어나면 회전 정책이 무의미해진다. 상한을 넘지 않은 가장 최근
    파일이 있으면 이어 쓰고, 없으면(전부 상한을 넘었거나 아무것도 없으면)
    새 접미사로 연다.
    """
    base_dir = Path(base_dir)
    target_date = file_stem_for(t_ms).split("_")[1]

    if current is not None:
        cur_date = date_of(current)
        if cur_date == target_date and _size_of(current) < max_bytes:
            return current
        # 날짜가 바뀌었거나 상한을 넘었다 — 그 날짜의 파일 목록을 다시 본다.

    existing = sorted(
        (p for p in base_dir.glob(f"records_{target_date}*{_EXT}")),
        key=_suffix_of,
    )
    if existing:
        latest = existing[-1]
        if _size_of(latest) < max_bytes:
            return latest
        return file_path_for(base_dir, t_ms, suffix=_suffix_of(latest) + 1)

    return file_path_for(base_dir, t_ms, suffix=0)


def _size_of(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def old_files(base_dir, *, older_than_days: int, now_ms: int) -> list[Path]:
    """보존 기간(`older_than_days`)을 넘은 회전 파일들을 고른다.

    🔴 지우지는 않는다 — 호출측(`store.apply_retention`)이 지운다. 목록만
    반환하는 쪽이 "무엇이 지워질지"를 시험·로그에서 미리 볼 수 있다.
    """
    cutoff = (
        datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).date()
        - timedelta(days=older_than_days)
    )
    out = []
    for path in sorted(Path(base_dir).glob(f"records_*{_EXT}")):
        date = date_of(path)
        if date is None:
            continue
        file_date = datetime.strptime(date, "%Y%m%d").date()
        if file_date < cutoff:
            out.append(path)
    return out
