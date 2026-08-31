# USB 본선 Cloud 형식 통일 — 호스트 구현 계획 (계획 2/2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 호스트(GUI·수집기·저장·시험)가 통일된 Cloud 형식 레코드를 읽게 하고, 계획 1에서 이관된 펌웨어 잔여(mk_telem 삭제)를 마무리한다.

**Architecture:** **정규화 어댑터** — 파싱 직후 클라우드 레코드를 카탈로그 역매핑(TypeMap)으로 내부 v3형 레코드(`type:"ain"`+`connector_id`+`value`/`unit` …)로 변환한다. 화면·저장·조회의 (type, connector_id) 키잉이 무변경으로 산다. 근거: 전수 조사(2026-08-31) 결과 소비 지점 전부가 그 키를 쓰고, field_budget 의 `_CLOUD_KINDS`(imu) 분기가 "Cloud 머리를 v3 옆에서 다루는" 검증된 선례다 — 이것을 기본으로 승격한다.

**Tech Stack:** Python 3 + pytest (보드 불필요 — fake_board), C 시험은 계획 1 러너.

**Spec:** `HANDOFF_0831.md` + 개정된 `protocol/specification.md` §7 + 계약 v1.7.2. 소비 지점 전수 목록은 이 계획의 근거 조사(2026-08-31 Explore)로 P0~P3 우선순위가 잡혀 있다.

## Global Constraints

- 🔴 **굽기는 이 계획 완료 후, 사용자에게 알리고** (메모리 규칙). 굽기 전까지 실보드는 v3 를 말하므로, GUI 는 **두 방언을 다 읽어야 한다** — 정규화 어댑터는 v3 레코드(이미 내부형)와 클라우드 레코드를 모두 통과시킨다. 전환기 호환이 공짜로 생기는 것이 어댑터 전략의 부수 이득이다.
- 각 태스크 끝: `python -m pytest -q` 초록 + (펌웨어 접점 태스크는) `run_tests.ps1` 초록.
- 커밋 트레일러는 계획 1과 동일.
- 사용자 캡쳐 실물 NDJSON 8종(2026-08-29 메시지)이 파서·어댑터의 고정 시험 벡터다 — 지어내지 않는다.

---

### Task 1: records.py — 계약 파서로 개정 (P0-1)

**Files:** Modify `host/core/records.py`, `host/service/collector.py:205-208`, Test `host/tests/test_records.py`

**Produces:**
- `MANDATORY_FIELDS = ("schema_ver", "t", "type")` — `seq` 는 선택(tx.seq 체크박스).
- `parse_record`: `schema_ver` 는 양의 int 면 수용(제어 줄 3, 계약 기본 1, 사용자 설정 1~9). `seq` 는 **있으면** uint32 검증. `CONTRACT_SCHEMA_VER = 1` 상수 신설(§7.1 동기용), `SCHEMA_VER = 3` 은 제어 줄(cfg_*)·전환기 v3 호환용으로 유지.
- `is_telemetry`: 기존 타입 블랙리스트 유지(사용자 문자열은 자동 통과) **+ `seq` 없는 레코드는 False**(tx.seq 꺼짐 → 유실 집계 불참).
- `TIME_SOURCES` 에 `"gnss"`·`"host_clock"`·`"unknown"` 추가(계약 §1 — 보드는 gnss/gnss_nmea/device_clock 을 냄). `is_utc_time` 은 device_clock·unknown 이 아니면 True.
- collector 의 corrupt 합성은 그대로(`seq: None` — 이미 선택형과 호환).

- [ ] 시험: 캡쳐 벡터 8종이 parse_record 를 통과한다(각각 dict 반환, ProtocolError 없음). `tx.seq` 꺼진 줄(seq 없음)도 통과 + `is_telemetry` False. 제어 줄(schema_ver 3, cfg_item)도 통과. seq 가 문자열이면 여전히 거절.
- [ ] 기존 test_records 갱신(“seq 필수” 단정 → 선택), 전체 pytest 초록, 커밋.

### Task 2: TypeMap — 카탈로그 역매핑 + 중복 검사 (결정 2 보완)

**Files:** Create `host/core/typemap.py`, Test `host/tests/test_typemap.py`

**Produces:**
```python
@dataclass(frozen=True)
class TypeTarget:
    kind: str          # "ain" | "i2c" | "din"
    connector: int     # J번호 (ain: ch+3, i2c: 포트, din: 잭)
    channel: int       # ain 채널 인덱스(0..6), 그 외 connector 와 동일
    quantity: str = "" # i2c 만: temp/humidity/temp_object/lux
    unit: str = ""     # ain 만: 값 필드 이름(ainN.unit)

class TypeMap:
    @classmethod
    def from_schema(cls, schema: ConfigSchema) -> "TypeMap": ...
    def resolve(self, type_str: str) -> tuple[TypeTarget, ...]  # 없으면 ()
    def duplicates(self) -> dict[str, list[int]]  # type → J번호들 (2개 이상만)
```
- ain: `ainN.cloud` 현재값(비면 제외) → TypeTarget(unit=`ainN.unit`).
- i2c: `i2cN.enabled` && `i2cN.kind` → 종류별 타입 표 **KIND_CLOUD** =
  `{1:("light",), 2:("temp_air","humidity"), 3:("temp_road",), 4:("temp_air",)}` —
  🔴 펌웨어 `mk_cloud.c` I2C_KIND_TYPES 의 복제(이중 정의) — 파일 머리에
  경고 주석 + CLAUDE.md 의 기존 이중 정의 항목에 한 줄 추가(Task 9).
  타입→quantity: kind2 (temp_air→temp, humidity→humidity), kind3
  (temp_road→temp_object), kind4 (temp_air→temp), kind1 (light→lux).
- din: `dinN.cloud` → TypeTarget(kind="din").
- `resolve` 는 튜플 — 같은 문자열이 여러 채널이면 전부 (GUI 경고용). 고정 타입(`gnss`/`imu`/`device_capability`)은 TypeMap 밖(어댑터가 직접 안다).

- [ ] 시험: 현장 배선 스키마(fake 카탈로그로 ain0.cloud=flow1, ain1=flow2, ain2=pressure_paint, i2c12 kind2 enabled, din20.cloud=valve)에서 resolve 정답, duplicates 감지(같은 문자열 2채널), 빈 값 미등록. 커밋.

### Task 3: 정규화 어댑터 — 클라우드 → 내부 v3형 (P0 심장)

**Files:** Create `host/core/cloudnorm.py`, Test `host/tests/test_cloudnorm.py`

**Produces:** `normalize(rec: dict, tmap: TypeMap) -> list[dict]`
- 이미 내부형(v3: type ∈ {ain,i2c,din,gnss,gnss_raw,imu} && "connector_id" in rec 또는 제어 타입)이면 `[rec]` 그대로 — **전환기 호환**.
- ain 유래(사용자 문자열, tmap 이 ain 으로 해석): `{type:"ain", connector_id, value:<unit 키 값>, unit, ma, raw, seq?, t, time_source, status:0, cloud_type:<원문 type>}`.
- i2c 유래: 슬롯별 내부 레코드. `temp_air(degc)→quantity temp`, `humidity(pct)→humidity`(+`dewpoint_degc` 무시), `temp_road(degc)→temp_object` + `ambient_degc` 있으면 **둘째 레코드**(quantity temp_ambient) 합성, `light(lux)→lux`. value=값 필드, status:0.
- din 유래: `{type:"din", connector_id, state, ...}`.
- `gnss`(계약): `{type:"gnss", lat:lat_e8/1e8, lon:lon_e8/1e8, fix_t:t, sats:sat, fix, hdop:hdop_x100/100, alt?/speed?/course?, ...}` — screen.build_gnss 의 기존 키.
- `imu`·`device_capability`: 그대로 통과(+cloud_type).
- 해석 불가(사용자 문자열인데 tmap 에 없음): `[rec]` 그대로(스트림 원문 표시는 살리고 화면 카드만 못 붙는다 — 버리지 않는다).
- 중복 해석(duplicates): **첫 타깃으로** 정규화하되 `rec["ambiguous"]=True` 표시(경고는 GUI 가 카탈로그에서 직접).

- [ ] 시험: 캡쳐 벡터 8종 각각 → 기대 내부형(숫자까지 비교: flow1→value 1.6/unit lpm/connector 3 …, gnss lat 37.40668094, valve→din J20 state). ambient 합성, 미해석 통과. 커밋.

### Task 4: board_service·stream 통합 (P0-2·3)

**Files:** Modify `host/service/board_service.py`, `host/gui/stream.py`, Test `host/tests/test_board_service.py`·`test_stream.py`

- `_ingest`: ① `line.startswith("$GNSSRAW,")` 특례 — §3 체크섬이 없으므로 parse_line 전에 가로채 `_record_raw(line, "gnssraw")` 후 반환(corrupt 오염 방지). ② `parse_record` 후 `normalize(rec, self._typemap)` 로 레코드들 생성. ③ `seq` 는 `rec.get("seq")` — None 이면 tracker 미관찰. ④ TypeMap: `fetch_schema()` 성공 시 재구축, `set_config` 성공 && 키가 `*.cloud|*.kind|*.enabled|*.unit` 이면 재구축.
- stream.py: `parse_row` 가 `seq=None` 허용, `cloud_type` 있으면 표시 열에 원문 타입 병기; SeqTracker 게이트는 `row.seq is not None` 추가.
- [ ] 시험: fake transport 로 캡쳐 벡터 흘려 records 에 내부형 축적·tracker 무장애, $GNSSRAW 줄이 corrupt 0 으로 raw 에 남음, seq 없는 줄 관찰 제외. 커밋.

### Task 5: 결정 3 — 보드 파라미터 파일 보관 + restore 전환

**Files:** Create `host/core/config_snapshot.py`, Modify `host/gui/app.py`(_load_catalog), `host/gui/worker_loop.py` 또는 SET 성공 경로, `tools/restore_board_config.py`, `.gitignore`(+`/data/`), Test `host/tests/test_config_snapshot.py`

- `save_snapshot(schema, path="data/board_config.json")`: `{saved_at, port?, items:{key:current}}` — 읽기전용 항목 제외. `load_snapshot(path)` → dict.
- app: 카탈로그 로드 성공 시 저장; SET 성공(on_accepted) 시 해당 키만 갱신 저장.
- restore_board_config: 파일 있으면 `items` 로 PLAN 을 **대체**(pwr/adc/tx/ain/i2c/din/gnss/lcd/led 키만, `link.baud` 제외 — 규격 §4.2 별도 절차), 없으면 기존 PLAN 폴백. 끝에 SAVE 는 동일.
- [ ] 시험: 저장→로드 왕복, readonly 제외, restore 의 파일 우선·PLAN 폴백. 커밋.

### Task 6: GUI — 중복 경고·이름 표시 (P1 일부)

**Files:** Modify `host/gui/app.py`, `host/gui/settings_form.py`(경고 문구 생성), Test `host/tests/test_settings_form.py`

- `_load_catalog` 에서 `TypeMap.duplicates()` 가 비지 않으면 상단 링크 라벨에 경고("flow1 이 J3·J4 에 중복 — 스트림·영점이 채널을 가릴 수 없다", bad=True).
- 동종 I2C 2대(같은 kind enabled 2포트)도 같은 경로로 경고.
- [ ] 시험: 중복 스키마 → 경고 문자열 생성 함수 검증(Qt 없이 — settings_form/typemap 층). 커밋.

### Task 7: 대역폭·전송 화면 — Cloud 모양 기본화 + 두 링크 (P2, 검토 4)

**Files:** Modify `host/gui/field_budget.py`, `host/gui/settings_form.py`, `host/gui/link_usage.py`, Test 각 짝 시험

- field_budget: `_CLOUD_KINDS` 분기를 **기본**으로 — 모든 종류가 Cloud 머리(schema_ver 1, device_id, seq(선택, 기본 포함 +12B), t, type=사용자 문자열 표본, time_source). ain 표본의 값 필드는 unit 이름. i2c 는 유도 타입 표본.
- settings_form.record_shape: i2c 주기 = `i2cN.tx_period_ms`(반복 송신), ain 은 기존 tx.period_ms 로직 유지.
- link_usage: `JETSON_KINDS` 폐기 — **모든 종류가 두 링크에** 나간다. 요약은 링크 2행: 젯슨(921600 고정)과 호스트(link.baud) — 빡빡한 쪽(젯슨)이 카드 대표 %.
- [ ] 시험: 캡쳐 벡터 실측 길이(±10%)로 sample_record 검증, i2c 반복 주기 반영, 두 링크 % 산출. 커밋.

### Task 8: fake_board — Cloud 방언 + 카탈로그 갱신 (P3-15)

**Files:** Modify `host/tests/fake_board.py`, `host/tests/catalog_snapshot.jsonl`(재생성), 관련 시험

- 카탈로그: `firmware/stage1/tests/test_cfgtable.exe --catalog` 출력으로 스냅샷 재생성(tx.seq·i2cN.tx_period_ms·dinN.cloud 포함) — 생성 절차를 fake_board 머리 주석에 남긴다.
- `build_ain_record` 를 Cloud 모양으로 교체(ainN.cloud 설정값이 type, unit 키가 값 필드, seq 는 tx.seq 따라) — 🔴 스텁 비대화 금지: **ain 하나만** 유지(기존 원칙), i2c/din/gnss 는 시험이 벡터 문자열을 직접 쓴다.
- [ ] 전체 pytest 초록(어댑터 덕에 worker/stream/storage 시험이 내부형을 그대로 받는지 확인), 커밋.

### Task 9: 펌웨어 잔여 — mk_telem 삭제 마무리 (계획 1 Task 8 이관분)

**Files:** Modify `firmware/stage1/app/mk_ads1256.h/.c`(+`mk_ads_raw_to_ma`), `mk_cloud.c`·`mk_statled.c`·`mk_screen.c`(호출 교체), Delete `mk_telem.c/h`·`tests/test_telem.c`, Modify `Makefile`·`run_tests.ps1`(mk_telem 제거 — test_cloud/statled/screen 소스 목록), `host/tests/test_field_budget.py:353`(mk_telem.c → mk_cloud.c 의 MK_CLOUD_LINE_MAX 대조), `host/tests/test_firmware_uart_dma.py:238`(MK_TELEM_MAX_LINES → mk_cloud.c 의 MK_CLOUD_MAX_AIN_LINES), `host/core/limits.py` 주석, CLAUDE.md(이중 정의 목록에 typemap KIND_CLOUD 추가)

- 추가: 펌웨어 `table_policy` 에 **예약 타입명 거절** — `ainN.cloud`/`dinN.cloud` 값이 제어 타입(id/stat/cfg_item/cfg_field/cfg_value/cfg_end)이면 RANGE (사용자 문자열이 제어 화이트리스트와 충돌하면 is_telemetry 가 오판한다). test_cfgtable 에 시험.
- test_telem.c 의 계약 시험 대조표: 마스크·last 반복·din 이벤트·gnss_raw — Task 2~6(계획 1)에서 대체 확인된 것 외 남는 것은 test_cloud.c 로 이식 후 삭제.
- [ ] run_tests.ps1 + pytest 전부 초록, ARM gcc -fsyntax-only(main.c) 재확인, 커밋.

### Task 10: 재결합·문서

**Files:** Modify `host/tests/test_spec_sync.py`(전환기 주석 해소 — CONTRACT_SCHEMA_VER 과 대조 복원), `HANDOFF.md`(굽기 가능 조건 갱신: "계획 2 완료 — 굽기는 사용자 통지 후. 절차: 빌드→굽기→restore_board_config(파일)→GUI 확인"), `HANDOFF_0831.md`(완료 표시)

- [ ] 전체 초록, 커밋. 굽기는 하지 않는다 — 사용자와 일정 협의.

## Self-Review

- HANDOFF_0831 대조: 결정 2 GUI 목록(records/역매핑/link_usage/시험/fake_board) → T1~T4·T6~T8. 결정 3 → T5. 검토 4 → T7. 검토 1·2·5·6 의 호스트 접점($GNSSRAW corrupt 방지, seq 재기준선=자연 처리 문서화) → T4. 계획 1 이관분 → T9. 누락 없음.
- 전환기 호환(실보드 v3)이 T3 의 통과 규칙으로 보장됨 — 굽기 전에도 GUI 가 깨지지 않는다.
- 이중 정의 신설(KIND_CLOUD)은 T2 주석 + T9 CLAUDE.md 등재로 관리.
