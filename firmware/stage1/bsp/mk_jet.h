/* Jetson 링크 — USART2 텔레메트리 미러 + PA1 하트비트 출력.
 *
 * 경로: H723 PA2(USART2_TX) → J29 핀2 → Jetson 40핀 헤더 핀10 (UART RX)
 *       H723 PA1(GPIO 출력) → J29 핀6 → Jetson 40핀 헤더 핀7  (GPIO 입력)
 *       [결선 2026-08-21, HANDOFF.md §7.4]
 *
 * 근거: STM32H723ZGT6.pdf (DS13313 Rev 1), p.72, Table 8 —
 *       PA2 AF7 = USART2_TX, PA3 AF7 = USART2_RX.
 *       넷리스트 STM32_v2.0_current.net — JET_TX = J29.2 ↔ U5.36(PA2),
 *       JET_HB = J29.6 ↔ U5.35(PA1), 중간 부품 없음.
 *
 * 🔴 이 링크가 나르는 것은 **Cloud 계약**(app/mk_cloud.h)이다 [2026-08-21
 *    개정]. 결선 검증 때는 본선 텔레메트리의 바이트 미러였는데, 사용자
 *    결정("이 규약을 따르는 게 좋겠어")으로 mk_cloud 직렬화기가 대체했다.
 *    이 파일은 전송(링+DMA)만 안다 — 무엇을 나르는지는 main.c 배선이
 *    정한다. 젯슨 쪽 수신·명령은 다음 단계(B)다.
 *
 * 🔴 best-effort 다. 이 링이 차면 줄을 통째로 버리고 센다. 본선(USART3)의
 *    링과는 완전히 독립이라, 젯슨 쪽이 밀려도 호스트 링크와 수집에는
 *    아무 영향이 없다 — 수집이 이 프로젝트의 유일한 절대 제약이다.
 *
 * 🔴 PA1 하트비트의 방향은 **보드→젯슨** 이다 (사용자 설계 2026-08-21).
 *    데이터시트 §5.11 의 "젯슨→보드" 표기는 설계 의도였을 뿐, PA1 은
 *    커넥터 직결 GPIO 라 방향은 펌웨어가 정한다. 젯슨이 이 구형파를
 *    일정 시간 못 보면 NRST(J29 핀10)를 눌러 보드를 리셋한다(워치독).
 */
#ifndef MK_JET_H
#define MK_JET_H

#include <stddef.h>
#include <stdint.h>

/* 🔴 921600 고정 (2026-08-21). 점퍼선 신호 품질을 아직 모르므로 본선에서
 *    실증된 2 Mbps 보다 한 단계 아래에서 시작한다 — 결선 검증이 끝나면
 *    올리든가 설정 항목(jet.baud)으로 뺀다. 텔레메트리가 이보다 빠르게
 *    쌓이면 이 링크에서만 줄이 빠진다(위 best-effort). */
#define MK_JET_BAUD        921600u

/* 하트비트 반주기. 500 ms 토글 = 1 Hz 구형파 — 젯슨 워치독은 "엣지가
 * 1초 넘게 없다" 를 죽음의 판정으로 쓸 수 있다. */
#define MK_JET_HB_HALF_MS  500u

void mk_jet_init(void);

/* 텔레메트리 한 줄을 미러 링에 넣는다. 즉시 돌아온다 — 자리가 없으면
 * 줄 통째로 버리고 센다(본선의 mk_uart_write_bulk 와 같은 계약, 예약
 * 몫만 없다 — 이 링크에는 명령 응답이 없다). */
void mk_jet_write(const char *data, size_t len);

/* 슈퍼루프가 매 바퀴 부른다. MK_JET_HB_HALF_MS 마다 PA1 을 토글한다. */
void mk_jet_hb_tick(void);

/* 링이 차서 버린 줄 수와 바이트 수. 이 링크의 유실은 여기에만 남는다 —
 * 아직 $STAT 에는 안 싣는다(결선 검증 단계). */
uint32_t mk_jet_tx_drops(void);
uint32_t mk_jet_tx_dropped_bytes(void);

/* 송신 DMA(DMA2 Stream1) 완료 인터럽트에서 부른다. */
void mk_jet_dma_isr(void);

#endif /* MK_JET_H */
