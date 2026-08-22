/* 설정을 전선에 싣는 층 — HAL 비의존.
 *
 * `mk_config` 가 값을 들고 `mk_json` 이 줄을 만든다. 여기서는 규격 §7.3 의
 * 레코드 모양을 정한다.
 *
 * 🔴 호스트는 설정 항목을 하드코딩하지 않는다. 이 응답만으로 화면을
 *    구성한다 — 그래서 이 레코드가 빠짐없이 나가야 하고, Python 쪽이
 *    내는 것과 같은 모양이어야 한다.
 */
#ifndef MK_CFGWIRE_H
#define MK_CFGWIRE_H

#include <stddef.h>
#include <stdint.h>

#include "mk_config.h"
/* 🔴 `MkLcdStat` 하나 때문에 들인다. 함수는 안 부르므로 링크가 늘지
 *    않는다 — 규격 §7.4 의 `lcd` 객체가 실을 값의 **모양**을 화면 쪽이
 *    이미 정의해 두었고, 여기에 같은 필드를 한 벌 더 적으면 둘이 갈린다
 *    (`MkRailState` 와 다른 판단이다 — 그쪽은 설정표에서 만들면 안 된다는
 *    이유로 여기서 새로 정의했다). */
#include "mk_lcd.h"

/* 이 비트가 어느 레코드의 마스크에 해당하는가 (규격 §7.2 "해당 레코드").
 *
 * 🔴 [개정, 2026-08-19] `tx.fields` 하나를 ain·i2c·din 세 마스크로 나누며
 *    생겼다. 비트 번호·이름표는 MkFieldBit 표 하나를 셋이 공유하지만,
 *    어느 레코드에서 실제로 실리는지는 비트마다 다르다 — 이 비트필드가
 *    그것을 말한다. mk_cfgtable.c 의 add_tx() 가 여기서 각 마스크의
 *    최댓값·기본값을 끌어내고, mk_telem.c 의 field_on() 이 여기서 "해당
 *    없는 비트는 마스크에 서 있어도 무시" 를 판정한다 — 이름만 비교하면
 *    비트 재사용에서 조용히 새는 자리라 반드시 함께 검사한다. */
typedef enum {
    MK_FIELD_AIN = 1u << 0,
    MK_FIELD_I2C = 1u << 1,
    MK_FIELD_DIN = 1u << 2,
    /* 🔴 [신설, 2026-08-20] 네 번째. GNSS 측위 레코드(규격 §7.8)가
     *    자기 마스크 `tx.fields_gnss` 를 갖는다 — 위치가 나가는 자리와
     *    아날로그 채널을 같은 스위치로 묶으면, 아날로그를 가볍게 하려다
     *    위성 수가 같이 사라진다. */
    MK_FIELD_GNSS = 1u << 3,
    /* [2026-08-22] imu 레코드(젯슨 링크 전용 — UM981 RAWIMUX 유래)의
     * 필드 마스크. 전송 화면에 제 카드가 생긴다(사용자 요청). */
    MK_FIELD_IMU  = 1u << 4,
} MkFieldKind;

/* NDJSON 필드 마스크의 비트 하나 (규격 §7.2). */
typedef struct {
    uint8_t     bit;
    const char *name;
    uint8_t     def;
    const char *label;
    uint8_t     kinds;   /* MkFieldKind 의 OR 조합 — 이 비트가 속한 레코드들 */
} MkFieldBit;

/* 한 줄을 만들어 콜백에 넘긴다. 줄바꿈은 포함하지 않는다. */
typedef void (*MkCfgEmit)(void *ctx, const char *line, size_t len);

/* 카탈로그 한 줄의 최대 길이.
 *
 * 🔴 줄이 이 버퍼를 넘으면 `mk_json_end` 가 0 을 돌려주고 그 항목이
 *    **조용히 빠진다.** 카탈로그가 한 줄 모자란 채로 가고, 화면에는
 *    그 설정만 없다 — 왜 없는지 알 방법이 없다.
 *
 *    막는 것은 `cfg_end` 의 count 다. 선언한 수와 실제로 온 수가 다르면
 *    호스트가 거부한다(규격 §7.3). 실제로 그것이 잡았다 — I2C 종류
 *    항목의 안내문이 길어 6개가 통째로 빠진 것을 대조 시험이 걸렀다
 *    [2026-08-17]. 안내문을 길게 쓸 때는 이 상한을 기억한다.
 *
 * 🔴 320 -> 384 [2026-08-20]. `link.baud`(규격 §4.2)에서 다시 걸렸다 —
 *    choices 여섯에 한글 라벨·안내문까지 붙어 343 바이트였고, 그 항목이
 *    조용히 빠졌다(대조 시험이 "선언 111, 수신 110" 으로 잡았다).
 *
 *    이번에는 안내문을 줄이지 않고 버퍼를 키웠다. 이 항목의 안내문은
 *    꾸밈말이 아니라 **안전 경고**이고("바꾸면 링크가 끊긴다"), 그것을
 *    줄여서 자리를 맞추면 가장 위험한 설정에 가장 짧은 설명이 붙는다. */
#define MK_CFGWIRE_LIST_LINE_MAX  384

/* 카탈로그가 몇 줄인가 — cfg_item + cfg_field + cfg_end(1줄). */
size_t mk_cfgwire_list_count(const MkConfig *cfg, size_t n_fields);

/* 카탈로그의 `index` 번째 줄을 만든다. 줄바꿈은 포함하지 않는다.
 *
 * 🔴 [신설, 2026-08-20] 한 줄씩 만들 수 있어야 **이어서 내보내기**가
 *    가능하다. 아래 `mk_cfgwire_list` 는 103줄 ≈ 25 KB 를 한 호출에
 *    쏟아내는데, 실물 보드의 송신 링은 4,096 B 다 — 못 담은 줄이 버려져
 *    카탈로그 전체가 못 쓰게 됐다(실기기 2026-08-20, mk_txring.h 참고).
 *    부르는 쪽이 링에 자리가 있는 만큼만 내보내고 나머지를 다음 바퀴로
 *    미루려면, 어디까지 냈는지를 이 `index` 하나로 기억할 수 있어야 한다.
 *
 * 반환:  > 0  줄 길이
 *          0  이 index 는 줄을 못 만들었다(버퍼 부족) — 건너뛴다.
 *             빠진 것은 `cfg_end` 의 count 대조가 잡는다(규격 §7.3)
 *        < 0  index 가 카탈로그 끝을 넘었다 */
int mk_cfgwire_list_line(const MkConfig *cfg,
                         const MkFieldBit *fields, size_t n_fields,
                         size_t index, int64_t now_ms,
                         char *out, size_t cap);

/* `$CFG,LIST` 응답 본문. cfg_item · cfg_field · cfg_end 순서로 낸다.
 *
 * 🔴 `cfg_end` 의 `count` 는 cfg_item + cfg_field 합계다(규격 §7.3).
 *    수신측이 이것을 대조해 전송이 중간에 잘렸는지 판정한다 — 링크가
 *    나쁠 때 절반만 온 카탈로그로 화면을 그리면 안 된다.
 *
 * 🔴 한 호출에 전부 쏟는다. 받는 쪽이 그것을 감당할 수 있을 때만 쓴다 —
 *    실물 보드는 `mk_cfgwire_list_line` 으로 나눠 내보낸다. */
void mk_cfgwire_list(const MkConfig *cfg,
                     const MkFieldBit *fields, size_t n_fields,
                     int64_t now_ms, MkCfgEmit emit, void *ctx);

/* `$STAT` 응답 본문 (규격 §7.4).
 *
 * 🔴 `rails` 는 **명령 상태**다. 피드백 회로가 없으므로 실측이 아니고,
 *    호스트는 이것을 `정상 ON` 이 아니라 `ON 명령됨` 으로 표시해야 한다.
 *
 * 🔴 `queues` 는 채널별 큐 깊이·최고치·유실이다. 3단계에서 ADS1256 이
 *    들어오면 이것이 유일한 진단 창구가 된다 — 유실이 나는데 어디서
 *    나는지 모르면 고칠 수 없다. 아직 큐가 없으므로 지금은 0 이다.
 */
/* 레일의 **명령 상태**. 실측이 아니다 — 피드백 회로가 없다.
 *
 * 🔴 설정표에서 만들지 않는다. 설정은 "원하는 것" 이고 이것은 "낸 것" 이다.
 *    둘 사이에 순차 기동 간격이 있어서, 설정을 그대로 보고하면 아직 안
 *    올린 레일을 켜졌다고 말하게 된다. */
typedef struct {
    uint8_t v24;
    uint8_t v14v9;
    uint8_t v5;
} MkRailState;

/* 🔴 `ch` 를 구조체가 들고 있다. 배열 첨자를 채널 번호로 쓰면, 꺼진 채널을
 *    건너뛴 순간 3번 채널의 유실이 1번 채널의 것으로 보고된다. 유실을
 *    찾으려고 보는 창구가 채널을 헷갈리면 없느니만 못하다. */
typedef struct {
    uint8_t  ch;
    uint16_t depth;
    uint16_t peak;
    uint32_t drops;
} MkQueueStat;

/* J18~J20 디지털 입력의 **지금 상태** (규격 §7.4·§7.6).
 *
 * 🔴 `rails` 와 반대로 이것은 실측이다 — 보드가 EXTI 로 핀을 직접 읽는다.
 *    상태 변화는 §7.6 의 `din` 텔레메트리로도 오지만 그것은 바뀔 때만
 *    오므로, 막 연결한 호스트는 `$STAT` 으로 지금 상태를 채운다. */
typedef struct {
    uint16_t connector_id;   /* 18·19·20 */
    uint8_t  state;          /* 이미 반전된 값 — 1 = 켜짐(신호 있음) */
} MkDinState;

/* 호스트 링크 속도의 지금 상태 (규격 §4.2·§7.4).
 *
 * 🔴 `MkLinkBaud` 를 그대로 넘기지 않는다. 저쪽은 상태기계라 kernel_hz·
 *    deadline_ms 처럼 전선에 실을 이유가 없는 것을 들고 있고, `remaining_ms`
 *    는 "지금" 을 알아야 나오는 파생값이다. 무엇을 싣는지는 전선 쪽이
 *    정한다 — `MkRailState` 와 같은 판단이다. */
typedef struct {
    uint32_t baud;             /* 지금 전선에 서 있는 속도 */
    uint32_t confirmed;        /* $CFG,SAVE 가 Flash 에 쓰는 값 */
    uint32_t pending;          /* 확인을 기다리는 속도. 0 이면 없다 → null */
    int64_t  remaining_ms;     /* 되돌아가기까지. 대기 중이 아니면 음수 → null */
    uint32_t applied;
    uint32_t confirmed_count;
    uint32_t reverted;
} MkLinkStat;

/* 송신 링의 상태 (규격 §7.4, 신설 2026-08-20).
 *
 * 🔴 왜 전선에 싣나. 이 값들을 보려면 GDB 로 `p 'mk_uart.c'::s_tx` 를
 *    해야 했다. 그래서 카탈로그가 잘려 GUI 가 통째로 못 쓰게 된 결함을
 *    **디버거를 붙이고서야** 알았다. 같은 날 PPS 에서도 똑같이 겪었고,
 *    그때 얻은 교훈이 `pps_raw_count` 다 — 밖에서 볼 수 없는 계수기는
 *    없는 것과 같다.
 *
 * 🔴 `drops` 와 `ctl_drops` 는 **급이 다르다.** 텔레메트리 유실은 "설정이
 *    링크 용량을 넘었다" 는 흔한 상태이고(호스트는 seq 구멍으로도 안다),
 *    제어 유실은 "호스트가 명령의 답을 영영 못 받았다" 는 사고다. 명령
 *    응답의 seq 는 항상 0 이라(규격 §5.2) 이 수 말고는 알 방법이 없다.
 *    한 수로 뭉치면 흔한 쪽에 묻혀 드문 쪽이 안 보인다. */
typedef struct {
    uint32_t cap;                /* 링의 전체 바이트 수 */
    uint32_t peak;               /* 여태 최고 수위 — cap 에 붙으면 다음엔 버린다 */
    uint32_t drops;              /* 텔레메트리에서 버린 줄 수 */
    uint32_t dropped_bytes;
    uint32_t ctl_drops;          /* 🔴 제어 경로에서 버린 줄 수 — 0 이 아니면 경고 */
    uint32_t ctl_dropped_bytes;
} MkTxStat;

/* 🔴 `time_source`·`time_quality` 는 규격 §7.4 예시에는 없지만 시뮬레이터와
 *    함께 낸다. 규격 §7.1.2 대로 `t` 는 시간 소스에 따라 UTC epoch 이기도
 *    하고 부팅 후 경과 ms 이기도 한데, 명령 응답에는 필드 마스크가 없어
 *    텔레메트리처럼 실어 보낼 자리가 없다. $STAT 이 그 답을 주는 유일한
 *    곳이다 — 호스트는 연결 직후 한 번 물어보면 된다. */
/* 🔴 설정표를 받지 않는다. 예전에는 여기서 pwr.* 를 읽어 rails 를 만들었는데,
 *    그것이 설계 원칙 4 위반이었다 — 설정은 사용자가 원하는 것이지 보드가
 *    낸 것이 아니다. 이제 rails 를 인자로 받는다. */
/* 🔴 `din` 도 rails 와 같은 계약이다 — 보드가 **실제로 읽은 것**을 싣는다.
 *    없으면(NULL) 빈 배열이다. 정상 경로에서는 항상 MK_SOL_COUNT(3)개를
 *    받는다 — 커넥터가 있으면 상태는 항상 있다(레일과 달리 "아직 안 낸"
 *    중간 상태가 없다). */
/* 🔴 `gnss_pps_age_ms`·`gnss_sats` 는 시간축 진단이다(규격 §7.4, Phase 3).
 *    `time_source`/`time_quality` 가 "지금 등급이 무엇인가"를 말한다면
 *    이 둘은 "그 등급을 얼마나 믿을 수 있나"를 보탠다 — 등급이 gnss_pps
 *    라도 마지막 PPS 가 1.4초 전이면 다음 tick 에 내려갈 참이라는 것을
 *    미리 알 수 있다. -1 은 "아직 모른다"(PPS 를 한 번도 못 봤다 · GGA 를
 *    한 번도 못 받았다)는 뜻이고 `null` 로 나간다 — 0 을 지어내지 않는다
 *    (설계 원칙 3·4와 같은 결).
 *
 * 🔴 [신규, 2026-08-19] `gnss_pps_raw_age_ms`·`gnss_pps_raw_count`·
 *    `gnss_pps_unpaired_reason` — 실기기 관측(UM981, 실내·fix 없음)으로
 *    필요해졌다. PPS 는 TIM8 CCR3·PC8 로 정확히 1초 간격으로 들어오고
 *    있었는데, RMC 가 전부 무효(V)라 한 번도 짝지어지지 않아
 *    `gnss_pps_age_ms` 가 계속 -1(null) 이었다 — 배선·캡처·ISR 은 전부
 *    정상인데 "PPS 가 안 온다"로 보였다. `gnss_pps_age_ms` 가 **짝지어
 *    채택된** PPS 의 나이(시간축이 실제로 쓰는 값)라면, 이 셋은 짝짓기와
 *    무관하게 "펄스가 오는가"를 답한다:
 *      - `gnss_pps_raw_age_ms` : 마지막 원시 캡처 이후 경과(ms). -1 은
 *        "원시 캡처도 한 번도 없다"이고 null 로 나간다.
 *      - `gnss_pps_raw_count`  : 지금까지 캡처한 원시 펄스 수. 카운터라
 *        "본 적 없음"과 "0 번"이 같은 뜻이므로 0 이 곧 그 뜻이다(null 이
 *        필요 없다 — `gnss_sats`(gga 없어도 0)와 같은 결).
 *      - `gnss_pps_unpaired_reason` : 마지막 짝짓기 시도가 왜 안 됐는가.
 *        NULL 이면 방금 짝지어졌거나(즉 `gnss_pps_age_ms` 가 값을 가짐)
 *        아직 판단할 사건이 없다는 뜻이고 `null` 로 나간다. 그 외에는
 *        `mk_timeax_pps_unpaired_reason_name()` 이 돌려주는 문자열
 *        ("no_valid_nmea"/"no_pps") 을 그대로 싣는다 — 지어내지 않는다
 *        (CLAUDE.md §5). */
/* 🔴 `gnss_init_sent`·`gnss_init_exhausted`·`gnss_sentence_seen` 는 GNSS
 *    초기화 시퀀스(규격 §4.1.1)를 진단한다. `time_source`가 계속
 *    device_clock 에 머물 때 — 즉 아무것도 안 올 때 — 이 셋이 "명령을
 *    보내기는 했는가" / "재시도를 다 썼는가" / "문장을 받은 적은
 *    있는가"를 가른다. mk_gnssctl 이 안 붙어 있으면 호출 쪽이 전부 0을
 *    넘긴다. */
/* 🔴 [신규, 2026-08-19] `lcd` — 화면 회복 계수기다(규격 §7.4).
 *
 *    실기기에서 "노이즈 타면 픽셀이 다 깨지는데?" 가 나왔고, 깨진 뒤
 *    저절로 안 돌아오는 것이 문제였다. 회복 장치를 넣었으면 **몇 번
 *    깨졌고 몇 번 되살렸는지**를 밖에서 볼 수 있어야 한다 — 그 수가
 *    없으면 이 문제가 해결됐는지 덮였는지 아무도 모른다. PPS 에서
 *    `pps_raw_count` 로 같은 일을 했다.
 *
 *      epoch/reinit/redraw/verify_ok/verify_fail/rejected  누적 카운터.
 *        카운터라 0 이 곧 "아직 없다" 이므로 null 이 필요 없다.
 *      readback  되읽기를 믿을 수 있나. **-1 이면 null 로 나간다** —
 *        아직 한 번도 안 물어본 것과 "못 믿는다" 는 다르다(pps_age_ms 의
 *        null 과 같은 결).
 *
 *    NULL 을 넘기면 전부 0 · readback 은 null 이다 — 화면이 안 붙은
 *    빌드다. */
/* 🔴 [신규, 2026-08-19] `clock_src`·`clock_sysclk_hz` — 시간축 신뢰도의
 *    일부다(규격 §7.4). 진단 정보가 아니다.
 *
 *    시간축은 PPS 로 1초마다 맞추고 **그 사이는 이 클럭으로 보간**한다.
 *    크리스털이 안 떠서 내부 RC 로 폴백하면(bsp/mk_clock.c) 초 안쪽
 *    오차가 ±1 % 까지 벌어진다 — 1초 끝에서 10 ms 다. 호스트가 그것을
 *    모르고 저장하면 안 된다.
 *
 *    `clock_src` 가 NULL 이면 `{"src":null,"sysclk_hz":null}` 로 나간다 —
 *    "이 장치는 답할 수 없다"이고, 시뮬레이터가 그 자리다. 값을 지어내지
 *    않는다(lcd 를 NULL 로 넘길 때와 같은 결). */
int mk_cfgwire_stat(int64_t now_ms,
                    const char *mode, const char *ctl_mode, const char *fw, const char *board_rev,
                    uint32_t uptime_ms,
                    const char *clock_src, uint32_t clock_sysclk_hz,
                    const char *time_source, uint32_t time_quality,
                    int64_t gnss_pps_age_ms,
                    int64_t gnss_pps_raw_age_ms, uint32_t gnss_pps_raw_count,
                    const char *gnss_pps_unpaired_reason,
                    int32_t gnss_sats,
                    int gnss_init_sent, int gnss_init_exhausted,
                    int gnss_sentence_seen,
                    const MkRailState *rails,
                    const MkDinState *din, size_t n_din,
                    const MkQueueStat *queues, size_t n_queues,
                    const MkLinkStat *link,
                    const MkTxStat *tx,
                    const MkLcdStat *lcd,
                    char *out, size_t cap);

/* `$CFG,GET` 응답 본문 한 줄. 반환은 길이, 실패면 음수.
 *
 * `cur` 의 JSON 타입은 항목의 vtype 을 따른다 — bool 은 참/거짓,
 * str 은 문자열, 나머지는 수다 (규격 §5.2). */
int mk_cfgwire_value(const MkCfgItem *item, int64_t now_ms,
                     char *out, size_t cap);

#endif /* MK_CFGWIRE_H */
