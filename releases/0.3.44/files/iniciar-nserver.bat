@echo off
setlocal
cd /d "%~dp0"
set NSERVER_HOST=0.0.0.0
set NSERVER_PORT=8791

echo ========================================
echo   Nserver - Painel Pessoal Desktop
echo ========================================
echo.
echo Iniciando servidor local no notebook...
echo Se o Windows Firewall perguntar, clique em Permitir acesso na rede privada.
echo.

python --version >nul 2>&1
if errorlevel 1 (
  echo ERRO: Python nao encontrado no Windows.
  echo Instale Python 3 pelo site https://www.python.org/downloads/windows/
  echo Durante a instalacao, marque a opcao: Add python.exe to PATH
  echo.
  pause
  exit /b 1
)

if exist requirements.txt (
  echo Verificando dependencias do Nserver...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo AVISO: Nao consegui instalar todas as dependencias automaticamente.
    echo A ferramenta de video pode precisar do yt-dlp e FFmpeg.
  )
  echo.
)

python app\launcher.py
pause
