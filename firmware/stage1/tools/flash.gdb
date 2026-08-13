# MarkON 1단계 펌웨어를 굽는다.
#
# 🔴 memory-write-packet-size 256 + fixed 가 필수다 (CLAUDE.md §4).
#    없으면 512B 청크 경계에서 데이터가 손상되는데 compare-sections 로도
#    dump 로도 못 잡는다. 실제 동작으로 확인해야 한다.
set confirm off
set pagination off
set remote memory-write-packet-size 256
set remote memory-write-packet-size fixed
target extended-remote \\.\COM24
monitor swdp_scan
attach 1
load
compare-sections
monitor reset
detach
quit
