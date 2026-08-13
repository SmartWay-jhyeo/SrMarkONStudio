# 굽기 전에 현재 플래시를 읽어 둔다. 쓰지 않는다.
set confirm off
set pagination off
set remote memory-write-packet-size 256
set remote memory-write-packet-size fixed
target extended-remote \\.\COM24
monitor swdp_scan
attach 1
dump binary memory backup_before_stage1.bin 0x08000000 0x08008000
detach
quit
