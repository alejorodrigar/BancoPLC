@echo off
REM Crea las tareas programadas del vigilante a las horas de banco.toml (clave «horas»).
REM Uso:  instalar_tarea.bat              crear o actualizar las tareas
REM       instalar_tarea.bat --mostrar    ver lo que haria sin tocar Windows
REM       instalar_tarea.bat --borrar     borrar todas las tareas BancoPLC_*
cd /d "%~dp0"
python instalar_tarea.py %*
if errorlevel 1 pause
