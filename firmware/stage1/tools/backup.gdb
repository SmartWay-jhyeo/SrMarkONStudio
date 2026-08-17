# 굽기 전에 현재 플래시를 읽어 둔다. 쓰지 않는다.
#
# 🔴 범위가 64 KB 다. 예전에는 32 KB (0x08000000~0x08008000) 였는데, 이미지가
#    35,840 바이트가 되면서 **잘린 백업**이 될 뻔했다. 잘린 백업은 복구가
#    안 되는데 파일은 멀쩡해 보인다 — 되돌리려는 순간에야 알게 된다.
#
# 🔴 설정 섹터도 함께 뜬다. 저장 덩어리 형식이 바뀌면(항목 수가 늘면) 새
#    펌웨어가 옛 저장값을 거부하고 기본값으로 돌아간다. 되돌릴 일이 생겼을 때
#    옛 펌웨어에는 옛 설정이 필요하다.
#    섹터는 128 KB 지만 실제로 쓰는 앞부분만 뜬다 — 나머지는 0xFF 다.
set confirm off
set pagination off
set remote memory-write-packet-size 256
set remote memory-write-packet-size fixed
target extended-remote \\.\COM24
monitor swdp_scan
attach 1
dump binary memory backup_app.bin 0x08000000 0x08010000
dump binary memory backup_cfg.bin 0x080E0000 0x080E2000
detach
quit
