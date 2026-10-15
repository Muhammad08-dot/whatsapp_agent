@echo off
REM ==============================================================================
REM Windows Automation Runner for WhatsApp Gemini Agent
REM ==============================================================================

IF "%1"=="install" (
    pip install -r requirements.txt
    pip install -r requirements-dev.txt
    GOTO end
)

IF "%1"=="test" (
    pytest tests -v
    GOTO end
)

IF "%1"=="lint" (
    ruff check .
    GOTO end
)

IF "%1"=="run" (
    python main.py
    GOTO end
)

echo Usage: run.bat [install ^| test ^| lint ^| run]
:end
