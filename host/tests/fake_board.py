"""전선 반대편에서 답해 주는 최소 스텁.

🔴 **이것은 시뮬레이터가 아니다.** 시뮬레이터(`tools/simulator/`)는 지웠다
   (사용자 결정 2026-08-20). 보드가 늘 붙어 있는 지금, 설정 항목을 하나 늘릴
   때마다 펌웨어와 시뮬레이터 두 곳을 고치고 대조까지 맞춰야 하는 비용이
   값보다 커졌다.

   그런데 호스트 시험 대부분은 시뮬레이터를 **검사 대상**으로 쓴 것이 아니라
   "명령을 보내면 뭐라도 답해 주는 것"으로 썼을 뿐이다. 그 자리를 여기가
   메운다. 검사 대상은 여전히 `host/` 코드다.

🔴 **기능을 자라게 하지 마라.** 시뮬레이터가 시뮬레이터가 된 경위가 그것이다
   — 처음에는 `$SACK` 하나였다. 여기에 무언가 더 넣고 싶어지면 먼저 물어라:
   *그것이 없으면 어느 호스트 시험이 못 도는가?* 답이 없으면 넣지 않는다.
   특히 **실기기를 흉내 내지 않는다** — GNSS 데모 좌표, I2C 사인파, 화면
   회복 계수기 같은 것은 여기 없고 앞으로도 없다.

🔴 **카탈로그는 얼린 스냅샷이다** (`catalog_snapshot.jsonl`). 시뮬레이터를
   지우기 직전의 `$CFG,LIST` 출력을 한 번 떠 둔 것이고, **펌웨어를 따라가지
   않는다.** 그게 요점이다 — 없애려던 비용이 "두 곳을 맞추는 것"이었으므로
   스텁은 처음부터 대조 상대가 아니어야 한다. 시험이 확인하는 것은 *보드가
   무엇을 갖고 있나*가 아니라 *호스트가 카탈로그 한 뭉치를 받았을 때 어떻게
   구는가* 다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from host.core.errors import ConfigError, ProtocolError, Reason
from host.core.framing import build_command, parse_line
from host.core.limits import DEFAULT_BAUD, LINK_BAUD_CONFIRM_MS, LINK_BAUD_KEY
from host.core.records import SCHEMA_VER

#: 🔴 확장자가 `.jsonl` 인 이유 — `.gitignore` 가 `data/` 와 `*.ndjson` 을
#:    수집 데이터로 보고 걷어낸다. 이것은 수집 결과가 아니라 시험 자료라
#:    추적돼야 한다.
CATALOG_SNAPSHOT = Path(__file__).resolve().parent / "catalog_snapshot.jsonl"

#: 규격 §6.2 — 이 시간 안에 `$HB` 를 못 받으면 RUN 으로 내려간다.
HB_TIMEOUT_MS = 3000
HB_EMIT_PERIOD_MS = 1000

AIN_CHANNELS = 7
#: AIN0 은 J3 에 대응 (데이터시트 §5.3)
CONNECTOR_OFFSET = 3
#: 디지털 입력 커넥터 (규격 §7.6)
DIN_CONNECTORS: tuple[int, ...] = (18, 19, 20)

_COMPACT = (",", ":")
_TRUE_WORDS = ("true", "1", "on", "yes")
_FALSE_WORDS = ("false", "0", "off", "no")
_INT_TYPES = ("u8", "u16", "u32")

#: 마스크 키 (규격 §7.2). 레코드 종류마다 마스크가 따로다.
FIELD_MASK_KEYS = {
    "ain": "tx.fields_ain",
    "i2c": "tx.fields_i2c",
    "din": "tx.fields_din",
    "gnss": "tx.fields_gnss",
    "imu": "tx.fields_imu",
}


# --------------------------------------------------------------- 설정 항목


@dataclass
class FakeItem:
    """카탈로그 항목 하나. 필드 이름은 규격 §7.3 의 전선 이름을 따른다."""

    key: str
    group: str
    vtype: str
    default: object
    current: object
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    readonly: bool = False
    label: str = ""
    note: str = ""
    choices: tuple = ()
    choice_labels: tuple = ()
    interlocked: bool = False
    #: 참이면 이 항목이 실제 출력을 움직인다 (전원 레일·LED).
    out: bool = False


class FakeStore:
    """설정 항목 보관과 최소 검증. 규격 §5.2 의 거부 순서만 지킨다.

    🔴 조합 제약(용량 검사)은 **없다.** 그것은 보드가 하는 일이고, 호스트는
       거부 사유를 화면에 띄우는 것까지가 제 몫이다 — 그 경로는
       `test_qt_widgets` 가 사유 문자열로 직접 확인한다.
    """

    def __init__(self, items: list[FakeItem]):
        self.items: dict[str, FakeItem] = {i.key: i for i in items}
        self.dirty = False
        self._saved: dict[str, object] = {}

    # ------------------------------------------------------------------ 조회
    def get(self, key: str) -> object:
        item = self.items.get(key)
        if item is None:
            raise ConfigError(Reason.UNKNOWN_KEY, key)
        return item.current

    def field_mask(self, kind: str) -> int:
        return int(self.items[FIELD_MASK_KEYS[kind]].current)

    # ------------------------------------------------------------------ 변경
    def set(self, key: str, raw: str) -> None:
        item = self.items.get(key)
        if item is None:
            raise ConfigError(Reason.UNKNOWN_KEY, key)

        value = _coerce(item, raw)
        if value == item.current:
            return                          # 변화 없음 — 거부할 이유가 없다

        # 🔴 순서가 규격 §5.2 다. 인터록이 읽기 전용보다 앞이다.
        if item.interlocked:
            raise ConfigError(Reason.INTERLOCK, item.note or key)
        if item.readonly:
            raise ConfigError(Reason.READONLY, item.note or key)

        item.current = value
        self.dirty = True

    def reset(self) -> None:
        for item in self.items.values():
            item.current = item.default
        self.dirty = True

    # ---------------------------------------------------------------- 영속화
    def saved_value(self, key: str) -> object:
        """마지막 `save()` 가 남긴 값 — "플래시에 무엇이 들어 있나"."""
        return self._saved.get(key)

    def save(self) -> None:
        self._saved.update({k: i.current for k, i in self.items.items()})
        self.dirty = False

    # ---------------------------------------------------------------- 카탈로그
    def catalog_lines(self) -> Iterator[str]:
        """`$CFG,LIST` 응답 줄들 (규격 §7.3).

        스냅샷을 그대로 되뱉지 않고 항목에서 다시 만든다 — `cur` 가 지금
        값을 실어야 하고, 시험이 만든 저장소(`FakeStore([...])`)도 같은
        길로 카탈로그를 낼 수 있어야 한다.
        """
        for item in self.items.values():
            rec: dict = {
                "schema_ver": SCHEMA_VER, "seq": 0, "t": 0,
                "type": "cfg_item", "key": item.key, "grp": item.group,
                "vtype": item.vtype, "default": item.default,
                "cur": item.current, "ro": item.readonly, "label": item.label,
            }
            if item.out:
                rec["out"] = True
            if item.minimum is not None:
                rec["min"] = item.minimum
            if item.maximum is not None:
                rec["max"] = item.maximum
            if item.unit:
                rec["unit"] = item.unit
            if item.note:
                rec["note"] = item.note
            if item.choices:
                rec["choices"] = list(item.choices)
                if item.choice_labels:
                    rec["choice_labels"] = list(item.choice_labels)
            yield json.dumps(rec, ensure_ascii=False, separators=_COMPACT)

        for bit, name, default, label, records in FIELD_BITS:
            yield json.dumps(
                {"schema_ver": SCHEMA_VER, "seq": 0, "t": 0,
                 "type": "cfg_field", "bit": bit, "name": name,
                 "default": default, "label": label,
                 "records": list(records)},
                ensure_ascii=False, separators=_COMPACT,
            )

        yield json.dumps(
            {"schema_ver": SCHEMA_VER, "seq": 0, "t": 0, "type": "cfg_end",
             "count": len(self.items) + len(FIELD_BITS)},
            ensure_ascii=False, separators=_COMPACT,
        )


def _coerce(item: FakeItem, raw: str) -> object:
    """전선 문자열을 항목의 타입으로 바꾼다. 못 바꾸면 RANGE."""
    text = raw.strip()

    if item.vtype == "bool":
        low = text.lower()
        if low in _TRUE_WORDS:
            return True
        if low in _FALSE_WORDS:
            return False
        raise ConfigError(Reason.RANGE, item.key)

    if item.vtype in _INT_TYPES or item.vtype == "enum":
        try:
            value: object = int(text, 10)
        except ValueError:
            raise ConfigError(Reason.RANGE, item.key) from None
        if item.choices and value not in item.choices:
            raise ConfigError(Reason.RANGE, item.key)
    elif item.vtype == "f32":
        try:
            value = float(text)
        except ValueError:
            raise ConfigError(Reason.RANGE, item.key) from None
    elif item.vtype == "str":
        if item.maximum is not None and len(text.encode("utf-8")) > int(item.maximum):
            raise ConfigError(Reason.RANGE, item.key)
        return text
    else:
        return text

    if item.minimum is not None and value < item.minimum:
        raise ConfigError(Reason.RANGE, item.key)
    if item.maximum is not None and value > item.maximum:
        raise ConfigError(Reason.RANGE, item.key)
    return value


# ------------------------------------------------------- 얼린 카탈로그 스냅샷


def _load_snapshot() -> tuple[list[FakeItem], tuple]:
    items: list[FakeItem] = []
    bits: list[tuple] = []
    for line in CATALOG_SNAPSHOT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec["type"] == "cfg_item":
            items.append(FakeItem(
                key=rec["key"], group=rec["grp"], vtype=rec["vtype"],
                default=rec["default"], current=rec["cur"],
                minimum=rec.get("min"), maximum=rec.get("max"),
                unit=rec.get("unit", ""), readonly=rec.get("ro", False),
                label=rec.get("label", ""), note=rec.get("note", ""),
                choices=tuple(rec.get("choices", ())),
                choice_labels=tuple(rec.get("choice_labels", ())),
                out=rec.get("out", False),
            ))
        elif rec["type"] == "cfg_field":
            bits.append((rec["bit"], rec["name"], rec["default"],
                         rec["label"], tuple(rec["records"])))
    return items, tuple(bits)


_SNAPSHOT_ITEMS, FIELD_BITS = _load_snapshot()

_BIT_OF = {name: bit for bit, name, _d, _l, _r in FIELD_BITS}
_RECORDS_OF = {name: records for _b, name, _d, _l, records in FIELD_BITS}


def fake_store() -> FakeStore:
    """얼린 카탈로그로 만든 저장소. 호출할 때마다 새 항목을 만든다."""
    items, _ = _load_snapshot()
    return FakeStore(items)


# ------------------------------------------------------------ 레코드 만들기

#: ADS1256 은 24비트 양방향. 단일단 양의 전 범위 코드.
ADS1256_FULL_SCALE = (1 << 23) - 1
SHUNT_OHMS = 120.0
#: 🔴 만재 입력은 VREF 가 아니라 2·VREF 다 (PGA=1, ADS1256.pdf p.11).
FULL_SCALE_V = 2.0 * 2.5


def _field_on(mask: int, name: str, kind: str) -> bool:
    if kind not in _RECORDS_OF.get(name, ()):
        return False
    bit = _BIT_OF.get(name)
    return bit is not None and bool(mask & (1 << bit))


def raw_to_ma(raw: int) -> float:
    return raw / ADS1256_FULL_SCALE * FULL_SCALE_V / SHUNT_OHMS * 1000.0


def build_ain_record(store: FakeStore, *, channel: int, seq: int, t_ms: int,
                     raw: int, capture_counter: int,
                     time_source: str = "device_clock",
                     time_quality: int = 0) -> dict:
    """마스크에 따라 필드를 골라 담은 `ain` 레코드 (규격 §7.1·§7.2)."""
    mask = store.field_mask("ain")
    digits = int(store.get("tx.float_digits"))

    def on(name: str) -> bool:
        return _field_on(mask, name, "ain")

    rec: dict = {"schema_ver": SCHEMA_VER, "seq": seq, "t": t_ms,
                 "type": "ain"}
    # 🔴 connector_id 는 마스크로 못 끈다(규격 §7.2 개정 2026-08-21) —
    #    펌웨어 build_record 와 같은 자리.
    rec["connector_id"] = channel + CONNECTOR_OFFSET
    if on("raw"):
        rec["raw"] = int(raw)               # 원본 — 반올림하지 않는다
    ma = raw_to_ma(raw)
    if on("ma"):
        rec["ma"] = round(ma, digits)
    if on("value"):
        zero = float(store.get(f"ain{channel}.zero"))
        scale = float(store.get(f"ain{channel}.scale"))
        rec["value"] = round((ma - zero) * scale, digits)
    if on("unit"):
        rec["unit"] = store.get(f"ain{channel}.unit")
    if on("status"):
        rec["status"] = 0
    if on("device_id"):
        rec["device_id"] = store.get("dev.id")
    # 🔴 time_source 는 마스크로 못 끈다 (규격 §7.1.2).
    rec["time_source"] = time_source
    if on("time_quality"):
        rec["time_quality"] = time_quality
    if on("capture_counter"):
        rec["capture_counter"] = capture_counter
    return rec


def render(rec: dict) -> str:
    return json.dumps(rec, ensure_ascii=False, separators=_COMPACT)


# ------------------------------------------------------------------ 스텁 보드


class Mode:
    CONFIG = "CONFIG"
    RUN = "RUN"


_CONFIG_ONLY = frozenset({"SET", "SAVE", "RESET"})


class FakeBoard:
    """명령에 답하고 주기적으로 `ain` 을 흘리는 스텁.

    🔴 흉내 내는 것은 **절차**뿐이다 — 모드 판정(§6.2), 설정 거부 사유(§5),
       링크 속도 확인·되돌림(§4.2). 호스트가 시험해야 하는 것이 그것이고,
       그 셋을 빼면 보드 없이 확인할 수 있는 것이 남지 않는다.
    """

    def __init__(self, store: FakeStore | None = None, *, fw: str = "0.1.0",
                 board_rev: str = "2.0"):
        self.store = store if store is not None else fake_store()
        self.fw = fw
        self.board_rev = board_rev

        self._seq = 0
        self._now_ms = 0
        self._last_hb_rx_ms: int | None = None
        self._last_hb_tx_ms = 0
        self._last_emit_ms = 0
        self._last_ain: dict[int, tuple[int, int]] = {}
        self._last_collect_ms: dict[int, int] = {}

        # ── 링크 속도 (규격 §4.2) ─────────────────────────────────────────
        # 🔴 항목이 없을 수도 있다 (시험이 몇 항목짜리 저장소를 물릴 때).
        #    그때는 기본값을 들고 `_link_tick` 이 아무 일도 안 한다.
        item = self.store.items.get(LINK_BAUD_KEY)
        self._link_active = int(item.current) if item is not None else DEFAULT_BAUD
        self._link_confirmed = self._link_active
        self._link_state = "idle"          # idle | armed | pending
        self._link_pending: int | None = None
        self._link_deadline_ms: int | None = None
        self._link_applied = 0
        self._link_confirmed_count = 0
        self._link_reverted = 0

    # ------------------------------------------------------------------ 모드
    @property
    def mode(self) -> str:
        """접속 모드 — 하트비트로 **관측**된다 (규격 §6.2)."""
        if self._last_hb_rx_ms is None:
            return Mode.RUN
        if self._now_ms - self._last_hb_rx_ms > HB_TIMEOUT_MS:
            return Mode.RUN
        return Mode.CONFIG

    @property
    def ctl_mode(self) -> str:
        """제어 모드 (규격 §6.4). 🔴 스텁은 **언제나 ACTIVE** 다 — 여기에는
        움직일 출력이 없으므로 TEST 가 지킬 것도 없다. `BoardService.pump()`
        가 이 이름을 읽으므로 자리만 지킨다."""
        return "ACTIVE"

    @property
    def link_baud(self) -> int:
        """지금 전선에 서 있다고 주장하는 속도."""
        return self._link_active

    # ------------------------------------------------------------- 수신 처리
    def feed(self, line: str) -> list[str]:
        try:
            cmd = parse_line(line)
        except ProtocolError:
            # 🔴 verb 를 못 읽으면 어느 명령에 대한 거부인지 말할 수 없다.
            return []

        handler = {
            "HB": self._on_hb, "ID": self._on_id, "STAT": self._on_stat,
            "CFG": self._on_cfg, "BAUD": self._on_baud,
        }.get(cmd.verb)
        if handler is None:
            return [self._sack(cmd.verb, "ERR", Reason.UNSUPPORTED)]
        return handler(cmd.args)

    def _on_hb(self, _args: tuple[str, ...]) -> list[str]:
        self._last_hb_rx_ms = self._now_ms
        return []                           # 하트비트에는 응답하지 않는다

    def _on_id(self, _args: tuple[str, ...]) -> list[str]:
        return [self._json(type="id", device_id=self.store.get("dev.id"),
                           fw=self.fw, board_rev=self.board_rev),
                self._sack("ID", "OK")]

    def _on_stat(self, _args: tuple[str, ...]) -> list[str]:
        # 🔴 **모르는 것은 null 이다.** 여기에는 발진기도, 송신 링도, 패널도
        #    없다 — 0 을 채우면 진단 화면이 "있는데 한 번도 안 찼다" 로 읽어
        #    거짓 안심을 준다 (설계 원칙 4).
        return [
            self._json(
                type="stat", mode=self.mode, ctl_mode="ACTIVE", fw=self.fw,
                board_rev=self.board_rev,
                time_source="device_clock", time_quality=0,
                gnss={"pps_age_ms": None, "pps_raw_age_ms": None,
                      "pps_raw_count": 0, "pps_unpaired_reason": None,
                      "sats": None, "init_sent": False,
                      "init_exhausted": False, "sentence_seen": False},
                uptime_ms=self._now_ms,
                clock={"src": None, "sysclk_hz": None},
                rails={"v24": self.store.get("pwr.24v"),
                       "v14v9": self.store.get("pwr.14v9"),
                       "v5": self.store.get("pwr.5v")},
                din=[{"connector_id": c, "state": 0} for c in DIN_CONNECTORS],
                queues=[{"ch": ch, "depth": 0, "peak": 0, "drops": 0}
                        for ch in range(AIN_CHANNELS)
                        if self.store.get(f"ain{ch}.enabled")],
                link={"baud": self._link_active,
                      "confirmed": self._link_confirmed,
                      "pending": self._link_pending,
                      "remaining_ms": (None if self._link_deadline_ms is None
                                       else max(0, self._link_deadline_ms
                                                - self._now_ms)),
                      "applied": self._link_applied,
                      "confirmed_count": self._link_confirmed_count,
                      "reverted": self._link_reverted},
                tx=None,
                lcd={"epoch": 0, "reinit": 0, "redraw": 0, "verify_ok": 0,
                     "verify_fail": 0, "rejected": 0, "readback": None},
            ),
            self._sack("STAT", "OK"),
        ]

    def _on_cfg(self, args: tuple[str, ...]) -> list[str]:
        if not args:
            return [self._sack("CFG", "ERR", Reason.UNKNOWN_KEY)]
        sub = args[0].upper()
        if sub in _CONFIG_ONLY and self.mode != Mode.CONFIG:
            return [self._sack("CFG", "ERR", Reason.MODE)]
        try:
            if sub == "LIST":
                return [*self.store.catalog_lines(), self._sack("CFG", "OK")]
            if sub == "GET":
                key = args[1] if len(args) > 1 else ""
                return [self._json(type="cfg_value", key=key,
                                   cur=self.store.get(key)),
                        self._sack("CFG", "OK")]
            if sub == "SET":
                if len(args) < 3:
                    return [self._sack("CFG", "ERR", Reason.RANGE)]
                # 값에 쉼표가 들어갈 수 있으므로 나머지를 전부 붙인다.
                self.store.set(args[1], ",".join(args[2:]))
                return [self._sack("CFG", "OK")]
            if sub == "SAVE":
                # 🔴 확정되지 않은 링크 속도는 Flash 에 가지 않는다
                #    (규격 §4.2.2 규칙 5) — 아무도 말을 못 거는 보드가 된다.
                if self._link_state != "idle":
                    return [self._sack("CFG", "ERR", Reason.BUSY)]
                self.store.save()
                return [self._sack("CFG", "OK")]
            if sub == "RESET":
                self.store.reset()
                return [self._sack("CFG", "OK")]
        except ConfigError as exc:
            return [self._sack("CFG", "ERR", exc.reason)]
        return [self._sack("CFG", "ERR", Reason.UNKNOWN_KEY)]

    def _on_baud(self, args: tuple[str, ...]) -> list[str]:
        """`$BAUD,CONFIRM,<baud>` (규격 §4.2.3).

        🔴 CONFIG 전용이 **아니다.** 호스트는 포트를 다시 여는 동안 `$HB` 를
           못 보내고, 그것이 3000 ms 를 넘으면 보드는 RUN 이다 (§6.2).
        """
        if len(args) < 1 or args[0].upper() != "CONFIRM":
            return [self._sack("BAUD", "ERR", Reason.UNSUPPORTED)]
        if len(args) < 2 or not args[1].isdigit():
            return [self._sack("BAUD", "ERR", Reason.RANGE)]
        if self._link_state != "pending":
            return [self._sack("BAUD", "ERR", Reason.MODE)]
        if int(args[1]) != self._link_pending:
            # 🔴 시한을 늘려 주지 않는다. 틀린 확인은 확인이 아니다.
            return [self._sack("BAUD", "ERR", Reason.RANGE)]

        self._link_confirmed = self._link_pending
        self._link_pending = None
        self._link_deadline_ms = None
        self._link_state = "idle"
        self._link_confirmed_count += 1
        return [self._sack("BAUD", "OK")]

    # --------------------------------------------------------------- 주기 처리
    def tick(self, now_ms: int) -> list[str]:
        self._now_ms = now_ms
        out: list[str] = []

        if now_ms - self._last_hb_tx_ms >= HB_EMIT_PERIOD_MS:
            self._last_hb_tx_ms = now_ms
            out.append(build_command("HB").rstrip("\r\n"))

        # 🔴 **자리가 곧 안전장치다** — 이번 바퀴에 나갈 줄이 다 만들어진
        #    뒤다. 응답 한가운데서 속도가 바뀌면 규격 §4.2.2 규칙 1 위반을
        #    호스트 시험에게 거꾸로 가르치게 된다.
        self._link_tick(now_ms)

        self._collect(now_ms)
        period = int(self.store.get("tx.period_ms"))
        if now_ms - self._last_emit_ms >= period:
            self._last_emit_ms = now_ms
            out.extend(self._emit(now_ms))
        return out

    def _link_tick(self, now_ms: int) -> None:
        item = self.store.items.get(LINK_BAUD_KEY)
        if item is None:
            return
        if self._link_state == "idle" and int(item.current) != self._link_active:
            self._link_state = "armed"
            self._link_pending = int(item.current)
        if self._link_state == "armed":
            self._link_active = self._link_pending
            self._link_state = "pending"
            self._link_deadline_ms = now_ms + LINK_BAUD_CONFIRM_MS
            self._link_applied += 1
        if self._link_state == "pending" and now_ms >= self._link_deadline_ms:
            # 🔴 사람이 아무것도 안 해도 링크가 살아난다 — 절차의 존재 이유.
            self._link_active = self._link_confirmed
            self._link_pending = None
            self._link_deadline_ms = None
            self._link_state = "idle"
            self._link_reverted += 1
        # 설정표를 전선의 사실에 맞춘다 — 안 하면 다음 바퀴가 그 차이를 새
        # 요청으로 오해해 영원히 되돌림을 되풀이한다.
        item.current = self._link_active

    def _collect(self, now_ms: int) -> None:
        """채널 주기마다 마지막 표본을 덮어쓴다. 값은 톱니다 — 지어낸 것이고
        모양이 그럴듯할 이유가 없다."""
        for ch in range(AIN_CHANNELS):
            if not self.store.get(f"ain{ch}.enabled"):
                continue
            period = int(self.store.get(f"ain{ch}.period_ms"))
            last = self._last_collect_ms.get(ch)
            if last is not None and now_ms - last < period:
                continue
            self._last_collect_ms[ch] = now_ms
            raw = (now_ms * 977 + ch * 131_071) % ADS1256_FULL_SCALE
            self._last_ain[ch] = (now_ms, raw)

    def _emit(self, _now_ms: int) -> list[str]:
        lines: list[str] = []
        for ch in range(AIN_CHANNELS):
            if not self.store.get(f"ain{ch}.enabled"):
                continue
            sample = self._last_ain.get(ch)
            if sample is None:
                continue                    # 수집 전 — 지어내지 않는다
            t_ms, raw = sample
            self._seq += 1
            lines.append(render(build_ain_record(
                self.store, channel=ch, seq=self._seq, t_ms=t_ms, raw=raw,
                capture_counter=t_ms * 1000)))
        return lines

    # ------------------------------------------------------------------ 직렬화
    def _sack(self, verb: str, *rest: str) -> str:
        if len(verb) > 12 or not verb.isalpha() or not verb.isupper():
            verb = "?"
        return build_command("SACK", verb, *rest).rstrip("\r\n")

    def _json(self, **fields) -> str:
        rec = {"schema_ver": SCHEMA_VER, "seq": 0, "t": self._now_ms}
        rec.update(fields)
        return json.dumps(rec, ensure_ascii=False, separators=_COMPACT)


# ------------------------------------------------------------------ 트랜스포트


class LoopbackTransport:
    """스텁을 직접 물리는 트랜스포트. 시리얼 없이 전 구간을 시험한다.

    🔴 **속도가 맞지 않으면 줄이 오가지 않는다** (규격 §4.2).

       진짜 UART 가 없다고 이 통로를 언제나 통하게 두면, 링크 속도 변경의
       실패 경로 — 이 기능의 본체 — 를 한 번도 못 밟는다.
    """

    def __init__(self, board: FakeBoard, baud: int = DEFAULT_BAUD) -> None:
        self.sim = board          # 이름을 유지한다 — 부르는 쪽이 `.sim` 을 쓴다
        self.baud = baud
        #: 🔴 `(내보낸 속도, 줄)`. 보드는 응답을 **옛 속도로 내보낸 다음**
        #:    속도를 바꾼다 (규격 §4.2.2 규칙 1).
        self._pending: list[tuple[int, str]] = []

    def _board_baud(self) -> int:
        return getattr(self.sim, "link_baud", self.baud)

    def write(self, data: str) -> None:
        if self.baud != self._board_baud():
            return                          # 보드가 못 알아듣는 파형이다
        for line in data.splitlines():
            if not line.strip():
                continue
            out = self.sim.feed(line + "\r\n")
            # 🔴 태그는 feed **뒤의** 속도다. 처리 중에 속도가 바뀌었다면
            #    응답은 새 속도로 나간 것이고, 그것이 §4.2.2 규칙 1 위반이다.
            self._pending.extend((self._board_baud(), ln) for ln in out)

    def read_lines(self) -> Iterator[str]:
        pending, self._pending = self._pending, []
        for baud, line in pending:
            if baud == self.baud:
                yield line
            # 속도가 다를 때 온 줄은 조용히 사라진다 — 실물에서는 프레이밍
            # 오류가 되고, 어느 쪽이든 호스트에는 안 온 것과 같다.

    def tick(self, now_ms: int) -> None:
        # 🔴 여기는 반대로 tick **전의** 속도로 태그한다. 주기 송신은 속도
        #    전환보다 위에서 만들어진다(`FakeBoard.tick`).
        board = self._board_baud()
        self._pending.extend((board, out) for out in self.sim.tick(now_ms))

    def reopen(self, baud: int) -> None:
        # 🔴 옛 속도로 오던 바이트는 버린다. 실물에서도 포트를 닫는 순간
        #    커널 버퍼가 사라진다.
        self._pending = []
        self.baud = baud

    def close(self) -> None:
        pass


def fake_service(**kwargs):
    """스텁을 물린 `BoardService` — 시험이 가장 자주 쓰는 조립."""
    from host.service.board_service import BoardService

    return BoardService(LoopbackTransport(FakeBoard()), **kwargs)
