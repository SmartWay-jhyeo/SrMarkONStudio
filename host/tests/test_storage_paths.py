"""회전 파일 이름·전환 규칙 (host/storage/paths.py).

🔴 sqlite 를 열지 않는다 — 여기는 파일 이름과 전환 여부를 정하는 순수
정책만 시험한다. sqlite I/O 는 test_storage_store.py 쪽이다.
"""
from __future__ import annotations

from datetime import datetime, timezone

from host.storage.paths import (
    date_of,
    file_path_for,
    file_stem_for,
    next_path_for,
    old_files,
)


def _epoch_ms(y, m, d, hh=0, mm=0, ss=0) -> int:
    dt = datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# ------------------------------------------------------------- file_stem_for
def test_file_stem_uses_utc_date_of_t():
    t = _epoch_ms(2026, 8, 19, 12, 0, 0)
    assert file_stem_for(t) == "records_20260819"


def test_file_stem_suffix_is_zero_padded():
    t = _epoch_ms(2026, 8, 19)
    assert file_stem_for(t, suffix=1) == "records_20260819_01"
    assert file_stem_for(t, suffix=12) == "records_20260819_12"


# ------------------------------------------------------------------ date_of
def test_date_of_reads_back_the_date():
    p = file_path_for(".", _epoch_ms(2026, 8, 19))
    assert date_of(p) == "20260819"


def test_date_of_reads_suffixed_names_too():
    p = file_path_for(".", _epoch_ms(2026, 8, 19), suffix=3)
    assert date_of(p) == "20260819"


def test_date_of_none_for_unrelated_files():
    assert date_of("readme.md") is None
    assert date_of("other_20260819.sqlite3") is None


# ------------------------------------------------------------- next_path_for
def test_next_path_opens_a_fresh_file_when_none_is_open(tmp_path):
    t = _epoch_ms(2026, 8, 19)
    p = next_path_for(tmp_path, None, t)
    assert p == tmp_path / "records_20260819.sqlite3"


def test_next_path_keeps_writing_the_same_file_within_a_day(tmp_path):
    t0 = _epoch_ms(2026, 8, 19, 0, 0, 0)
    t1 = _epoch_ms(2026, 8, 19, 23, 59, 0)
    current = next_path_for(tmp_path, None, t0)
    current.write_bytes(b"x")  # 파일이 존재해야 크기를 잴 수 있다
    again = next_path_for(tmp_path, current, t1)
    assert again == current


def test_next_path_rotates_when_the_utc_date_changes(tmp_path):
    """🔴 날짜는 t(획득 시각) 기준이다 — 수집기가 관찰한 지금 시각이 아니다."""
    t0 = _epoch_ms(2026, 8, 19, 23, 59, 0)
    t1 = _epoch_ms(2026, 8, 20, 0, 0, 1)
    current = next_path_for(tmp_path, None, t0)
    current.write_bytes(b"x")
    nxt = next_path_for(tmp_path, current, t1)
    assert nxt == tmp_path / "records_20260820.sqlite3"


def test_next_path_rotates_on_size_within_the_same_day(tmp_path):
    t0 = _epoch_ms(2026, 8, 19, 8, 0, 0)
    t1 = _epoch_ms(2026, 8, 19, 8, 0, 1)
    current = next_path_for(tmp_path, None, t0, max_bytes=10)
    current.write_bytes(b"0123456789ABCDEF")  # 상한(10바이트) 초과
    nxt = next_path_for(tmp_path, current, t1, max_bytes=10)
    assert nxt == tmp_path / "records_20260819_01.sqlite3"


def test_next_path_after_size_rotation_continues_the_new_suffix(tmp_path):
    """접미사 파일도 상한을 넘으면 그 다음 접미사로 또 넘어간다."""
    t = _epoch_ms(2026, 8, 19)
    first = next_path_for(tmp_path, None, t, max_bytes=10)
    first.write_bytes(b"0" * 20)
    second = next_path_for(tmp_path, first, t, max_bytes=10)
    assert second == tmp_path / "records_20260819_01.sqlite3"
    second.write_bytes(b"0" * 20)
    third = next_path_for(tmp_path, second, t, max_bytes=10)
    assert third == tmp_path / "records_20260819_02.sqlite3"


def test_next_path_restart_same_day_continues_the_latest_unfull_file(tmp_path):
    """🔴 재시작 시나리오 — 프로세스가 다시 뜨면 `current` 는 None 이다.

    그 날짜에 이미 파일이 있고 상한을 안 넘었으면 새로 만들지 않고
    이어 쓴다. 매번 재시작할 때마다 파일이 늘어나면 회전 정책이
    무의미해진다.
    """
    t = _epoch_ms(2026, 8, 19)
    p0 = next_path_for(tmp_path, None, t, max_bytes=1000)
    p0.write_bytes(b"x" * 100)
    # 프로세스가 재시작됐다고 가정 — current=None 으로 다시 묻는다.
    again = next_path_for(tmp_path, None, t, max_bytes=1000)
    assert again == p0


def test_next_path_restart_same_day_skips_a_full_file(tmp_path):
    t = _epoch_ms(2026, 8, 19)
    p0 = next_path_for(tmp_path, None, t, max_bytes=10)
    p0.write_bytes(b"x" * 20)
    again = next_path_for(tmp_path, None, t, max_bytes=10)
    assert again == tmp_path / "records_20260819_01.sqlite3"


# --------------------------------------------------------------- old_files
def test_old_files_finds_files_past_retention(tmp_path):
    keep = tmp_path / "records_20260818.sqlite3"
    keep.write_bytes(b"")
    drop = tmp_path / "records_20260101.sqlite3"
    drop.write_bytes(b"")
    now = _epoch_ms(2026, 8, 19)
    found = old_files(tmp_path, older_than_days=7, now_ms=now)
    assert found == [drop]


def test_old_files_ignores_files_within_retention(tmp_path):
    p = tmp_path / "records_20260819.sqlite3"
    p.write_bytes(b"")
    now = _epoch_ms(2026, 8, 19)
    assert old_files(tmp_path, older_than_days=7, now_ms=now) == []


def test_old_files_ignores_unrelated_files(tmp_path):
    (tmp_path / "notes.txt").write_bytes(b"")
    now = _epoch_ms(2026, 8, 19)
    assert old_files(tmp_path, older_than_days=0, now_ms=now) == []
