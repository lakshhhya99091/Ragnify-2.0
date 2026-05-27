@echo off
title Ragnify — Install Dependencies
color 0B

echo.
echo  Ragnify — Installing Dependencies
echo  ===================================
echo.

cd /d "%~dp0backend"

echo Installing core dependencies...
pip install fastapi==0.111.0 uvicorn[standard]==0.29.0 python-multipart==0.0.9

echo Installing OpenAI + FAISS...
pip install openai==1.30.1 faiss-cpu==1.8.0 tiktoken==0.7.0

echo Installing document parsers...
pip install PyMuPDF==1.24.4 python-docx==1.1.2 Pillow==10.3.0 pytesseract==0.3.10 python-pptx==1.0.2

echo Installing web crawler...
pip install httpx==0.27.0 beautifulsoup4==4.12.3 html2text==2024.2.26

echo Installing utilities...
pip install numpy==1.26.4 aiofiles==23.2.1 pydantic==2.7.1

echo.
echo [SUCCESS] All dependencies installed!
echo.
echo You can now run Ragnify by double-clicking run.bat
echo.
pause
