"""레코드를 sqlite 파일에 쌓는다. 회전·색인·끊김 안전성을 갖는다.

## 왜 sqlite 인가 (Parquet 대신)

브리핑이 제시한 기준으로 판단했다.

- **표준 라이브러리에 있다.** `sqlite3`는 파이썬 내장이다. Parquet 를
  쓰려면 `pyarrow`(수십 MB, 플랫폼별 휠)나 `fastparquet` 를 새로 추가해야
  한다. "추가 의존성은 적을수록 좋다"·"순수 표준 라이브러리로 되는 일이면
  추가하지 않는다"(작업 지시) 기준에서 이미 승부가 난다.
- **쓰기 패턴이 Parquet 에 안 맞는다.** Parquet 는 컬럼 지향 포맷이라
  "다 모은 뒤 한 번에 쓰는" 배치에 강하다. 이 데이터는 초당 10~100줄이
  **꾸준히** 들어온다 — 매번 새 파일이나 row-group 을 만들며 이어 쓰는
  구성이 되는데, sqlite 처럼 같은 파일에 계속 append 하는 것보다 복잡하고
  파일 수가 급격히 늘어난다.
- **시각 범위 조회가 핵심 용도다.** sqlite 는 `t` 위에 B-tree 색인 하나만
  만들면 `WHERE t BETWEEN ? AND ?` 와 "가장 가까운 값"(`ORDER BY ... LIMIT
  1`)이 바로 된다. Parquet 는 파일 안에서 이런 색인 조회가 없다 —
  row-group 통계로 파일을 거를 뿐 파일 안에서는 스캔이다.
- **전원이 갑자기 끊길 수 있다(차량 장비).** sqlite 는 WAL(Write-Ahead
  Log) + `synchronous=NORMAL` 조합으로 **커밋된 트랜잭션은 전원이 끊겨도
  산다**는 것이 공식 보증이다(SQLite 문서 "Write-Ahead Logging"). Parquet
  는 파일 하나가 완결된 단위라 쓰는 도중 끊기면 그 파일 전체가 깨지기
  쉽다 — 이어 쓰는 도중의 손상에 sqlite 보다 약하다.
- **Jetson(ARM Linux)과 Windows 양쪽에서 돈다.** `sqlite3`는 두 플랫폼
  모두 파이썬 표준 배포에 들어 있어 별도 빌드가 필요 없다.

즉 유일하게 sqlite 가 밀리는 지점(컬럼 지향 분석 쿼리·압축률)은 이
시스템의 실제 접근 패턴(시각 범위·최근접 조회, 행 단위 지속 쓰기)과
무관하다. `docs/`에 있는 별도 오프라인 분석 파이프라인이 생기면 그때는
sqlite 파일을 다시 Parquet 로 내보내는 변환기를 별도로 만들면 된다 —
저장 원본은 sqlite 로 두고 필요할 때 파생시키는 편이 반대보다 싸다.

## 원문을 남긴다

레코드마다 `raw` 컬럼에 **원문 NDJSON 줄을 그대로** 넣는다. 파싱 결과
(`value`·`connector_id` 등)만 남기면 규격이 바뀌었을 때(§7의 필드 마스크,
`quantity` 어휘 확장 등) 옛 데이터를 다시 해석할 방법이 없다. 파싱한
컬럼은 조회 속도를 위한 **파생물**이고, 진실은 `raw` 다.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from host.core.records import SCHEMA_VER
from host.storage.paths import DEFAULT_MAX_FILE_BYTES, next_path_for, old_files

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    t INTEGER NOT NULL,
    seq INTEGER,
    type TEXT NOT NULL,
    connector_id INTEGER,
    quantity TEXT,
    value REAL,
    state INTEGER,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_t ON records(t);
CREATE INDEX IF NOT EXISTS idx_records_type_connector_t
    ON records(type, connector_id, t);
"""

_INSERT = (
    "INSERT INTO records (t, seq, type, connector_id, quantity, value, state, raw) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


class RecordStore:
    """레코드를 받아 회전하는 sqlite 파일들에 쌓는다.

    🔴 `host/gui/`를 import하지 않는다 — 무인 수집(Jetson)이 GUI 없이
    이 클래스만으로 돌아야 한다.

    쓰기는 즉시 커밋하지 않고 모았다가 커밋한다(`commit_batch`건 또는
    `commit_interval_s`초 중 먼저 채워지는 쪽). 초당 100줄을 매번
    `fsync`하면(자동 커밋) 특히 SD 카드 기반 Jetson 에서 병목이 된다 —
    벤치마크는 storage-report.md 참고. 대신 `journal_mode=WAL` +
    `synchronous=NORMAL` 조합을 쓴다 — SQLite 문서가 이 조합에서
    "커밋된 트랜잭션은 정전에도 살아남는다"고 보증한다. 즉 **잃는 것은
    최대 한 배치(미커밋분)뿐**이고, 그 손실 크기는 `commit_batch`·
    `commit_interval_s`로 정한 상한이다.
    """

    def __init__(self, base_dir, *, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
                 commit_batch: int = 50, commit_interval_s: float = 0.5):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._max_file_bytes = max_file_bytes
        self._commit_batch = commit_batch
        self._commit_interval_s = commit_interval_s

        self._conn: sqlite3.Connection | None = None
        self._current_path: Path | None = None
        self._pending = 0
        self._last_commit = time.monotonic()

    # ------------------------------------------------------------------ 쓰기
    def write(self, rec: dict, raw: str) -> None:
        """레코드 하나를 쌓는다. `rec`는 파싱된 dict, `raw`는 원문 줄."""
        t_ms = int(rec.get("t", 0))
        self._ensure_file(t_ms)
        self._conn.execute(_INSERT, _row_of(rec, raw))
        self._pending += 1
        self._maybe_commit()

    def write_event(self, kind: str, t_ms: int, note: str = "") -> None:
        """수집기 자신이 만드는 합성 이벤트(`link_down`·`link_up` 등).

        보드가 보낸 레코드가 아니므로 `seq`가 없다(NULL). 🔴 **바로
        커밋한다** — 배치를 기다리지 않는다. 끊긴 직후 전원이 나가도
        "끊겼다"는 사실 자체는 남아야, 나중에 "이 시간대에 왜 데이터가
        없지?"의 답이 파일 안에 있다.
        """
        rec = {
            "schema_ver": SCHEMA_VER, "seq": None, "t": t_ms, "type": kind,
            "note": note,
        }
        raw = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        self._ensure_file(t_ms)
        self._conn.execute(_INSERT, _row_of(rec, raw))
        self._pending += 1
        self.flush()

    def flush(self) -> None:
        """대기 중인 배치를 즉시 커밋한다."""
        if self._conn is not None and self._pending:
            self._conn.commit()
        self._pending = 0
        self._last_commit = time.monotonic()

    def close(self) -> None:
        if self._conn is not None:
            self.flush()
            # 회전 파일을 닫을 때 WAL 을 본 파일로 합쳐 둔다 — 그래야
            # 이 파일이 더 이상 안 쓰여도 -wal/-shm 없이 완결된다.
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.close()
            self._conn = None
            self._current_path = None

    # -------------------------------------------------------------- 내부
    def _maybe_commit(self) -> None:
        if (
            self._pending >= self._commit_batch
            or time.monotonic() - self._last_commit >= self._commit_interval_s
        ):
            self.flush()

    def _ensure_file(self, t_ms: int) -> None:
        path = next_path_for(
            self.base_dir, self._current_path, t_ms, max_bytes=self._max_file_bytes
        )
        if path != self._current_path:
            self._reopen(path)

    def _reopen(self, path: Path) -> None:
        if self._conn is not None:
            self.flush()
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.close()
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._current_path = path


def _row_of(rec: dict, raw: str) -> tuple:
    rtype = rec.get("type", "?")
    value = rec.get("value")
    if value is None and rtype == "ain":
        # value 가 필드 마스크로 꺼져 있으면 ma 라도 — 둘 다 없으면
        # None 그대로(raw 에는 남아 있다).
        value = rec.get("ma")
    return (
        int(rec.get("t", 0)),
        rec.get("seq"),
        rtype,
        rec.get("connector_id"),
        rec.get("quantity"),
        value,
        rec.get("state"),
        raw,
    )


def apply_retention(base_dir, *, older_than_days: int, now_ms: int) -> list[Path]:
    """보존 기간을 넘은 회전 파일을 지운다. 지운 경로 목록을 반환한다.

    WAL 사이드카(`-wal`·`-shm`)도 남아 있으면 함께 지운다 — 본 파일만
    지우면 다음에 같은 이름으로 새 파일을 열 때 낡은 WAL 이 섞여든다.
    """
    deleted = []
    for path in old_files(base_dir, older_than_days=older_than_days, now_ms=now_ms):
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            if candidate.exists():
                candidate.unlink()
        deleted.append(path)
    return deleted
