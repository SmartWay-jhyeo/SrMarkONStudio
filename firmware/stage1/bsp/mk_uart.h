/* USART3 송수신 — HAL 을 쓰는 유일한 층.
 *
 * 🔴 app/ 은 HAL 을 모른다. 이 파일이 경계다. 위쪽(mk_hostlink)은 줄만
 *    주고받고, 아래쪽 하드웨어 사정은 전부 여기서 끝난다.
 *
 * 경로: H723 PB10/PB11 (USART3) → F103(BMP) → USB VCP
 *       데이터시트 §7.5 는 이 경로로 통신할 수 없다고 하지만 **틀렸다.**
 *       실기기에서 COM23 으로 NDJSON 이 그대로 나오는 것을 확인했고,
 *       넷리스트에도 경로가 있다 (CLAUDE.md 참조).
 */
#ifndef MK_UART_H
#define MK_UART_H

#include <stddef.h>
#include <stdint.h>

/* 받은 바이트를 담아 두는 링. 한 줄이 다 모이면 꺼내 쓴다. */
#define MK_RX_RING_SIZE   512

/* 🔴 이 크기를 넘는 줄은 버린다. 규격 §3.1 의 payload 상한이 192 이므로
 *    넉넉하다. 넘치는 입력을 잘라 담으면 앞부분만으로 명령이 되어 버린다. */
#define MK_RX_LINE_MAX    256

void mk_uart_init(uint32_t baud);

/* 속도만 바꾼다 (규격 §4.2). 수신 링과 그 안에 든 바이트는 그대로 둔다.
 *
 * 🔴 **보내던 바이트를 자르지 않는다.** 재설정 전에 TC(전송 완료)를
 *    기다린다 — 규격 §4.2.2 규칙 1 이 요구하는 것이 그것이다. 응답의
 *    앞부분만 옛 속도로 나가면 호스트는 그 줄을 읽지 못하고, 성공했는지
 *    조차 모르는 채로 링크가 끊긴다.
 *
 * 🔴 링을 비우지 않는다. 명령이 줄 단위로 걸쳐 있을 수 있고, 여기서
 *    버리면 아직 응답하지 않은 명령이 조용히 사라진다. 옛 속도로 오던
 *    바이트가 새 속도에서 깨져 보이더라도 그것은 프레이밍 계층이
 *    체크섬으로 걸러 낸다. */
void mk_uart_set_baud(uint32_t baud);

/* 한 줄을 통째로 내보낸다. 다 나갈 때까지 기다린다.
 *
 * 🔴 stage 1 은 이것으로 충분하다. 내보낼 것이 $HB(8B/초)와 $ID 응답뿐이라
 *    115200 에서 최대 1 ms 도 안 막는다. ADS1256 이 들어오는 3단계에서는
 *    DMA 로 바꿔야 한다 — 그때는 초당 10 KB 가 나간다. */
void mk_uart_write(const char *data, size_t len);

/* 완성된 줄이 있으면 out 에 담고 길이를 돌려준다. 없으면 0.
 *
 * 줄끝은 `\n` 으로 판정한다. `\r` 은 파서가 떼어낸다(규격 §3.2).
 * 반환 길이에 줄끝은 포함되지 않는다. */
size_t mk_uart_read_line(char *out, size_t cap);

/* 링이 넘쳐 버린 바이트 수. 진단용 — 링을 키워야 하는지 알려 준다. */
uint32_t mk_uart_rx_overruns(void);

/* 인터럽트에서 부른다. */
void mk_uart_isr(void);

#endif /* MK_UART_H */
