@echo off
rem Phase 1a: RBI MPC capture, 09:40-14:00 IST. Launched by the "events_bot MPC capture" scheduled task.
cd /d "Z:\Data Pipelines\events_bot"
if not exist archive\captures mkdir archive\captures
echo [%date% %time%] start >> archive\captures\mpc_task.log
"C:\Users\HP\.local\bin\uv.exe" run python harness\mpc_capture.py --until 14:00 >> archive\captures\mpc_task.log 2>&1
echo [%date% %time%] exit %errorlevel% >> archive\captures\mpc_task.log
