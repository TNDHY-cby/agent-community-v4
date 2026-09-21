"""tests 目录共享配置：注册故障注入验真组开关。"""
import pytest


def pytest_addoption(parser):
    parser.addoption("--run-fault-injection", action="store_true", default=False,
                     help="运行故障注入验真组（预期失败）")
