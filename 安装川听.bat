@echo off
chcp 65001 >nul
rem 双击安装川听（Windows）：装依赖、准备词典、跑自测，并在桌面和开始菜单建“川听”快捷方式
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
