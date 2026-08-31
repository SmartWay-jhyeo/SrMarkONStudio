/* mk_queue.c(app/)에 꽂는 임계구역 진입/이탈 — 실제 PRIMASK 조작.
 *
 * 🔴 [검토 지적 I3] `app/` 은 HAL 을 모른다는 경계가 `__disable_irq()` 에도
 *    적용된다(CMSIS 인트린식이지 순수 C 가 아니다). `mk_queue_push`(ISR)와
 *    `mk_queue_pop`(슈퍼루프)이 `q->count` 를 함께 건드리는 문제를 app/ 안
 *    에서 풀 수 없어서, 여기 bsp/ 에 실제 구현을 두고 `mk_queue_set_
 *    critical_section()` 으로 주입한다. main.c 가 부팅 초입에 한 번 등록
 *    한다 — 큐를 쓰는 어떤 코드보다도 먼저여야 한다.
 *
 * bsp/mk_time.c 의 mk_time_ms() 와 같은 PRIMASK 저장/복원 방식이다. 거기는
 * 진입과 이탈이 한 함수 안이라 지역변수로 충분했지만, 여기는 두 함수로
 * 갈라 호출하는 자리(mk_queue.c)가 그 사이를 유지해야 하므로 static 이
 * 필요하다. 그래도 안전하다 — enter() 가 인터럽트를 전부 끄므로, exit()
 * 가 불릴 때까지는 어떤 ISR 도 새로 끼어들어 enter()/exit() 를 다시 부를
 * 수 없다(중첩이 생기지 않는다는 뜻). */
#ifndef MK_CRITSEC_H
#define MK_CRITSEC_H

void mk_critsec_enter(void);
void mk_critsec_exit(void);

#endif /* MK_CRITSEC_H */
