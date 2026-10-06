@echo off
chcp 65001 >nul
title Ключи Artist Lead Finder
set PYTHONIOENCODING=utf-8
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0scripts\alf-keys.py"
) else (
  python "%~dp0scripts\alf-keys.py"
)
pause
