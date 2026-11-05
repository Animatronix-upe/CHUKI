@echo off
:: ================================================================
:: iniciar.bat - Arranca el sistema Animatrónico en Windows
:: Ejecutar desde la carpeta animatronico/
:: ================================================================

title Animatronico Control System

echo.
echo  =================================================
echo   ANIMATRONICO - Sistema de Control
echo  =================================================
echo.

:: Verificar que Python está instalado
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python no encontrado. Descargalo en https://python.org
    pause
    exit /b 1
)

:: Verificar que existe la carpeta python/
if not exist "python\app.py" (
    echo [ERROR] No se encontro python\app.py
    echo         Asegurate de ejecutar este archivo desde la carpeta animatronico\
    pause
    exit /b 1
)

:: Instalar dependencias si no están instaladas
echo [INFO] Verificando dependencias...
pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [AVISO] Algunas dependencias no se pudieron instalar.
    echo         Revisa requirements.txt o ejecuta:
    echo         pip install -r requirements.txt
)

echo.
echo [INFO] Iniciando servidor...
echo [INFO] Cuando aparezca el mensaje "Running on http://..." abre:
echo        http://localhost:5000
echo.
echo [INFO] Para cerrar el programa presiona Ctrl+C en esta ventana.
echo.

:: Iniciar el servidor Python
python python\app.py

echo.
echo [INFO] Servidor detenido.
pause
