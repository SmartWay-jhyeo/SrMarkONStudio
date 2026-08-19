/* GNSS/PPS 시간축 — HAL 비의존.
 *
 * 계획서 §7.2 는 6단계 시간 등급을 말하지만, 지금 실제로 세울 수 있는
 * 것은 셋뿐이다(CLAUDE.md 지시 — 지어내지 않는다):
 *
 *   gnss_pps    PPS 로 정렬된 UTC. 가장 정확하다.
 *   gnss_nmea   RMC 문장은 유효한데 PPS 짝을 못(또는 아직 못) 지었다.
 *   device_clock GNSS 자체가 없거나 오래 조용하다 — 부팅 후 경과 시간뿐이다.
 *
 * 🔴 시간 기준은 이 모듈 안에서 **µs 단위 단조 카운터 하나**뿐이다
 *    (`dev_us`). bsp/mk_gnss_io.c 의 TIM8 입력 캡처(PC8, PPS)가 16비트
 *    레지스터 + 오버플로 카운터를 이 하나의 64비트 값으로 미리 펼쳐서
 *    넘긴다 — `mk_time_ms()`(SysTick, 1ms 분해능)는 쓰지 않는다. 두 시계를
 *    섞으면 어느 쪽 지연을 보정해야 할지 알 수 없다.
 *
 *    부팅 시 0 근처에서 시작하므로, GNSS 가 아예 없을 때(device_clock)의
 *    폴백은 `dev_us/1000` — 지금까지 `mk_time_ms()` 가 하던 일과 값의
 *    뜻이 같다(부팅 후 경과 ms).
 *
 * 🔴 짝짓기 규칙 (PPS 펄스 ↔ RMC 문장):
 *
 *    PPS 는 **그 초의 시작(.000)을 표시하는 펄스**이고, 그 직후에 오는
 *    RMC 문장이 "지금이 몇 초인지"를 말로 풀어 준다 — 흔한 GNSS 모듈의
 *    관례(펄스가 먼저, 문장이 뒤)를 따른다고 가정한다. UM981 데이터시트를
 *    확보하지 못해 이 순서 자체는 **[설계확정·실기기 미검증]**이다(§5).
 *
 *    그래서 anchor(보드 카운터 ↔ UTC 대응점)를 세울 때 **RMC 문장 자신의
 *    소수점 이하(`msec`)는 버린다** — 문장이 UART 로 나오기까지의 지연이
 *    섞여 있어 못 믿는다. 대신 그 초의 정수부(자정으로부터 몇 초째)만
 *    RMC 에서 가져오고, 정확히 몇 시(µs)에 그 초가 시작했는지는 PPS 캡처
 *    tick 에서 가져온다 — 이것이 PPS 를 쓰는 이유 전부다.
 *
 *    PPS 캡처와 RMC 문장은 `MK_TIMEAX_PAIR_WINDOW_US` 안에 함께 와야
 *    짝짓는다. 그보다 오래 떨어져 있으면(둘 중 하나만 왔거나 유실됐으면)
 *    짝짓지 않는다 — 엉뚱한 초에 못 보던 펄스를 붙이면 시간축이 통째로
 *    한 자리 밀린다.
 *
 * 🔴 PPS 가 끊기면(마지막 펄스 이후 `MK_TIMEAX_PPS_STALE_US`) gnss_nmea 로,
 *    RMC 마저 끊기면(`MK_TIMEAX_NMEA_STALE_US`) device_clock 으로 내려간다.
 *    `mk_timeax_tick()` 을 슈퍼루프가 매 바퀴(또는 레코드를 만들 때마다)
 *    불러야 이 하락이 일어난다 — 새 데이터가 안 오는 동안 등급이 저절로
 *    안 내려가면 죽은 GNSS 를 계속 "정밀"이라고 말하게 된다.
 *
 * 🔴 유효하지 않은 fix(`MkGnssRmc.valid == 0`) 는 신선도를 갱신하지
 *    않는다 — 설계 원칙 3·4 와 같은 결: 응답이 왔다고 해서 그 응답이
 *    믿을 만하다는 뜻은 아니다.
 */
#ifndef MK_TIMEAX_H
#define MK_TIMEAX_H

#include <stdint.h>

#include "mk_gnss.h"

typedef enum {
    MK_TIMEAX_DEVICE_CLOCK = 0,
    MK_TIMEAX_GNSS_NMEA    = 1,
    MK_TIMEAX_GNSS_PPS     = 2,
} MkTimeAxGrade;

/* PPS 가 없어진 뒤 gnss_pps 를 유지하는 한도. 공칭 1 Hz 이므로 1.5초는
 * "한 번 놓쳤다" 를 곧바로 의심하되, 잡음으로 반 박자 늦은 것까지 오탐하지
 * 않을 여유다. */
#define MK_TIMEAX_PPS_STALE_US   1500000ULL

/* RMC 가 없어진 뒤 gnss_nmea 마저 내려놓는 한도. PPS 보다 넉넉히 둔다 —
 * NMEA 출력 주기·UART 버퍼링 지연을 더 흡수해야 한다. */
#define MK_TIMEAX_NMEA_STALE_US  3000000ULL

/* PPS 캡처와 그 뒤 RMC 를 짝짓는 최대 간격. 공칭 주기(1초) 안쪽이면
 * 충분하다 — 이보다 벌어지면 서로 다른 초의 펄스/문장일 수 있다. */
#define MK_TIMEAX_PAIR_WINDOW_US 1200000ULL

typedef struct MkTimeAx {
    MkTimeAxGrade grade;

    /* 보드 카운터(µs) <-> UTC(ms) 정박점. gnss_pps 로 맺어졌으면 PPS 에지
     * 순간이고, gnss_nmea 뿐이면 그 RMC 를 받은 순간(정밀도가 낮다). */
    int      anchor_valid;
    int64_t  anchor_epoch_ms;
    uint64_t anchor_dev_us;

    /* 아직 RMC 와 못 맺어진 PPS 캡처. */
    int      pps_pending;
    uint64_t pps_pending_dev_us;

    /* 신선도 판정용 — 마지막으로 "확실히 봤다"고 칠 수 있는 순간. 없으면
     * 0 이고 has_* 플래그로 구분한다(dev_us==0 이 "0마이크로초에 있었다"
     * 인지 "없었다"인지 헷갈리지 않기 위해서다 — 특히 시험에서 0부터
     * 시작하는 타임라인을 쓰면 이 구분이 없으면 즉시 틀린다). */
    int      has_pps;
    uint64_t last_pps_dev_us;
    int      has_nmea;
    uint64_t last_nmea_dev_us;

    uint8_t  sats;              /* 마지막 GGA 위성 수 — $STAT 진단용 */

    /* 마지막으로 `mk_timeax_tick()` 에 넘어온 dev_us. $STAT 처럼 "지금"
     * 값을 따로 들고 있지 않은 호출자(`mk_hostlink_stat`)가
     * `mk_timeax_pps_age_us_now()` 로 쓴다 — 매 슈퍼루프 tick 만큼의
     * 지연은 진단 필드에는 무해하다. */
    uint64_t last_seen_dev_us;
} MkTimeAx;

void mk_timeax_init(MkTimeAx *tx);

/* PPS 에지 하나를 알린다. `dev_us` 는 bsp 가 TIM8 캡처를 이미 64비트로
 * 펼친 값이다(`mk_timeax_extend_ticks()` 참고). 아직 등급을 바꾸지 않는다
 * — RMC 와 맺어져야 의미가 생긴다. */
void mk_timeax_on_pps(MkTimeAx *tx, uint64_t dev_us);

/* 새 RMC 문장. `dev_us` 는 이 문장의 파싱이 끝난 시점(대략)이다.
 * `rmc->valid` 가 거짓이면 등급도 신선도도 건드리지 않는다. */
void mk_timeax_on_rmc(MkTimeAx *tx, const MkGnssRmc *rmc, uint64_t dev_us);

/* 위성 수만 갱신한다(진단용, 등급에 영향 없음). */
void mk_timeax_on_gga(MkTimeAx *tx, const MkGnssGga *gga);

/* 신선도를 확인해 필요하면 등급을 낮춘다. 슈퍼루프가 매 바퀴 부른다 —
 * 새 PPS/RMC 가 없는 동안에도 등급은 여기서만 내려간다. */
void mk_timeax_tick(MkTimeAx *tx, uint64_t dev_us);

MkTimeAxGrade mk_timeax_grade(const MkTimeAx *tx);

/* "gnss_pps" / "gnss_nmea" / "device_clock" — NDJSON `time_source` 그대로. */
const char *mk_timeax_grade_name(MkTimeAxGrade grade);

/* NDJSON `time_quality` 그대로 (등급과 같은 정수, 0=미동기). */
uint8_t mk_timeax_time_quality(const MkTimeAx *tx);

uint8_t mk_timeax_sats(const MkTimeAx *tx);

/* 지금 등급 기준으로 epoch_ms 를 계산한다.
 *   gnss_pps / gnss_nmea : anchor 로부터 dev_us 경과분을 더한다.
 *   device_clock         : dev_us/1000 (부팅 후 경과 ms, 옛 동작과 같다). */
int64_t mk_timeax_now_ms(const MkTimeAx *tx, uint64_t dev_us);

/* 마지막 PPS 이후 경과(µs). 한 번도 없었으면 -1. $STAT 진단용
 * (규격 §7.4 — "마지막 PPS 이후 경과"). */
int64_t mk_timeax_pps_age_us(const MkTimeAx *tx, uint64_t dev_us);

/* 위와 같지만 `dev_us` 를 마지막 `mk_timeax_tick()` 호출값으로 대신한다 —
 * $STAT 처럼 호출자가 "지금" 을 따로 안 들고 있을 때 쓴다. */
int64_t mk_timeax_pps_age_us_now(const MkTimeAx *tx);

/* ---- 16비트 캡처 확장 ------------------------------------------------------
 *
 * TIM8 은 16비트다(RM0468 — TIM1/TIM8 은 advanced-control 16비트 타이머).
 * `wrap_count`(Update 인터럽트가 센 오버플로 횟수) + `raw_ccr`(CH3 캡처
 * 레지스터, 0~period-1) 를 하나의 64비트 tick 으로 편다 — mk_time.c 의
 * 32비트 wrap 로직과 같은 문제(캡처와 오버플로가 겹칠 수 있다)를 16비트라
 * 훨씬 자주(주기가 65536 tick 마다, 여기 설정으로는 65.536 ms 마다) 겪는다.
 *
 * 🔴 [일반 기법 · 이 데이터시트의 고유 사실 아님] `update_pending` (Update
 *    인터럽트 보류 플래그, bsp 가 캡처 ISR 안에서 같이 읽어 넘긴다) 이 서면
 *    "캡처와 오버플로가 같은 순간에 겹쳤다"는 뜻이다. 이때 `raw_ccr` 이
 *    주기의 앞쪽 절반이면 캡처가 롤오버 **이후**(=이미 새 주기)에 일어난
 *    것으로 보고 `wrap_count` 를 그대로 쓴다. 뒤쪽 절반이면 롤오버
 *    **직전**(=아직 이전 주기)인데 Update ISR 이 먼저 실행돼 `wrap_count`
 *    가 이미 하나 올라 있는 상태로 캡처 ISR 이 그것을 읽었을 수 있다는
 *    뜻이므로 하나 빼서 보정한다.
 *
 *    이것은 STM32 앱노트류(예: AN4776 계열)에 흔히 쓰이는 일반적인 확장
 *    기법이지 STM32H723 데이터시트/RM0468 에서 이 정확한 절차를 인용한
 *    것이 아니다 — **실기기로 검증되지 않았다.** 보드가 오면 PPS 를 실제로
 *    걸어 두고 여러 시간 관찰해 확인해야 한다(보고서에 명시). */
uint64_t mk_timeax_extend_ticks(uint32_t wrap_count, uint16_t raw_ccr,
                                uint32_t period, int update_pending);

#endif /* MK_TIMEAX_H */
