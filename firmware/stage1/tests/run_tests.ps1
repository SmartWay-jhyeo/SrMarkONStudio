# 🔴 이 개발 호스트에는 gcc 도 clang 도 없다 (2026-08-13 확인). arm-none-eabi-gcc
#    는 크로스 전용이라 호스트 시험에 못 쓴다. 있는 것은 MSVC 뿐이다.
#    Makefile 은 gcc 가 있는 환경(CI·리눅스)을 위해 남겨 둔다.
#
#    /utf-8 이 필수다. 이 파일들은 UTF-8 인데 MSVC 는 기본으로 CP949 로 읽어,
#    한글 바이트열 안의 0x5C 를 백슬래시로 오인하고 문자열을 깨뜨린다.
$ErrorActionPreference = "Stop"
$vcvars = "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path $vcvars)) { throw "vcvars64.bat 없음: $vcvars" }
Set-Location $PSScriptRoot
$cmd = "chcp 65001 >nul && `"$vcvars`" >nul 2>&1 && " +
       "cl /nologo /W4 /WX /utf-8 /std:c11 /Fe:test_framing.exe " +
       "test_framing.c ..\app\mk_framing.c >nul && .\test_framing.exe"
cmd /c $cmd
exit $LASTEXITCODE
