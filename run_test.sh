#!/bin/bash
cd "D:/astrbot_p/astrbot_plugin_livingmemory"
D:/astrbot_p/astrbot_plugin_livingmemory/.venv/Scripts/python.exe -m pytest tests/test_user_id_filtering.py -x --tb=short 2>&1
