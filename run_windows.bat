@echo off
setlocal
if not exist data mkdir data
python parts_manager.py
pause
