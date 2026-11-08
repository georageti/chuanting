@echo off
chcp 65001 >nul
rem 双击启动川听：已经在运行就只打开网页；没在运行就在后台启动（不留黑色窗口）
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo 还没安装。请先右键 scripts\setup.ps1，选“使用 PowerShell 运行”。
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" run.py
