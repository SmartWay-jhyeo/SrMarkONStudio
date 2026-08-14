# MarkON 펌웨어를 굽는다.
#
# 🔴 `memory-write-packet-size 256` + `fixed` 가 필수다 (CLAUDE.md §4).
#    없으면 512B 청크 경계에서 데이터가 손상되는데 compare-sections 로도
#    dump 로도 못 잡는다. 실제 동작으로 확인해야 한다.
#
# 🔴 `monitor connect_rst enable` — 리셋을 건 채로 붙는다.
#
#    깨진 펌웨어가 플래시에 있으면 붙기도 전에 그것이 실행돼 HardFault 로
#    들어가고, 그 상태에서는 `attach` 자체가 실패한다
#    ("Attaching to Remote target failed: FF"). 실제로 그렇게 막혔다 —
#    `.text` 가 MIS-MATCHED 로 반쯤 써진 뒤 보드에 접근할 수 없었다.
#
#    리셋을 걸어 두면 코드가 돌지 않으므로 언제나 붙을 수 있다. 굽기에
#    이것을 기본으로 둔다 — 정상일 때 손해가 없고, 깨졌을 때 유일한 길이다.
#
# 🔴 `load` 앞에 `monitor reset` 을 넣지 않는다. `attach` 는 코어를 멈춘
#    상태로 붙는데, 거기서 리셋하면 다시 달리기 시작한다. 깨진 판이면
#    그것이 곧 HardFault 다.
set confirm off
set pagination off
set mem inaccessible-by-default off
set remote memory-write-packet-size 256
set remote memory-write-packet-size fixed
target extended-remote \\.\COM24
monitor connect_rst enable
monitor swdp_scan
attach 1
load
compare-sections
monitor connect_rst disable
monitor reset
detach
quit
