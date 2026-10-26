@echo off
REM Disable third-party pytest plugin autoload, which can hang in offline environments.
set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
python -m pytest %*
