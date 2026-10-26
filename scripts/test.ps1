# 本机 Anaconda 的某些第三方 pytest 插件（如 langsmith）在无网络时可能阻塞启动。
# 本项目测试不需要这些插件，因此默认关闭自动加载。
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest @args
