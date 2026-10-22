#!/usr/bin/env python3
"""淘宝项目专用调度器；执行逻辑由共享状态机与现有模块提供。"""
from workflow_scheduler import main


if __name__ == "__main__":
    raise SystemExit(main(expected_mode="taobao_legacy"))
