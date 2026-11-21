"""config — 环境与配置域：数据根解析（paths）、手写文本 IO（textio）、config.json 读（config_loader）/ 写（config_store）。

刻意保持空 re-export：log_setup 在包根 import paths 会先触发本包 __init__，
在这里 re-export config_store 会形成 log_setup → config_store → log_setup 环。
消费方一律写完整路径：`from ..config import config_store`。
"""
