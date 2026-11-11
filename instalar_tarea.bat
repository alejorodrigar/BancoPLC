@echo off
REM Crea (o reemplaza) la tarea programada "BancoPLC" que lanza vigilar_carpeta.py todos los dias.
REM Uso:  instalar_tarea.bat [HH:MM]        (por defecto 06:00)
REM La tarea solo corre con la sesion de este usuario iniciada (/IT): CAP1131 necesita escritorio.

setlocal
set HORA=%~1
if "%HORA%"=="" set HORA=06:00
set CARPETA=%~dp0
set CARPETA=%CARPETA:~0,-1%

for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYTHON set PYTHON=%%P
if not defined PYTHON (
    echo No se encuentra python en el PATH.
    exit /b 1
)

echo Python:  %PYTHON%
echo Carpeta: %CARPETA%
echo Hora:    %HORA% todos los dias
schtasks /Create /F /TN "BancoPLC" /SC DAILY /ST %HORA% /IT ^
  /TR "cmd /c cd /d \"%CARPETA%\" && \"%PYTHON%\" vigilar_carpeta.py >> salida_tarea.txt 2>&1"
if errorlevel 1 exit /b 1

echo.
echo Tarea creada. Para lanzarla ahora:  schtasks /Run /TN BancoPLC
echo Para borrarla:                      schtasks /Delete /TN BancoPLC /F
