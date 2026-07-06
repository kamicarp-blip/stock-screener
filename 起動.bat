@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist modules\__pycache__ rd /s /q modules\__pycache__
echo 株スクリーナー を起動しています...
streamlit run app.py
pause
