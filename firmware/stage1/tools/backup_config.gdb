# 설정 섹터(sector 7)를 떠 둔다. 쓰지 않는다.
set confirm off
set pagination off
set mem inaccessible-by-default off
target extended-remote \\.\COM24
monitor swdp_scan
attach 1
dump binary memory backup_config_sector.bin 0x080E0000 0x080E0800
detach
quit