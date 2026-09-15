"""config_loader 测试：默认值兜底 / 深合并 / 坏 json / 旧扁平 llm 键兼容 / get_block"""
from __future__ import annotations

import json
import os
import tempfile

from agent_loop.config_loader import DEFAULT_CONFIG, get_block, load_config


def _write(tmp_obj, data) -> str:
    path = os.path.join(tmp_obj, "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


def test_load_config_defaults_when_file_missing():
    """无文件/坏路径 → 返回代码内默认全量（永不抛错）。"""
    cfg = load_config(config_path="nonexistent_config_xyz.json")
    assert cfg["network"]["port"] == 8766
    assert cfg["concurrency"]["max_concurrent_turns"] == 2
    assert cfg["initiative"]["min_interval"] == 300.0
    assert cfg["storage"]["storage_root"] == "~/.sessions"
    assert cfg["llm"]["base_url"] is None
    assert cfg["llm"]["timeout"] == 45.0 and cfg["llm"]["total_budget"] == 100.0
    print("✓ 缺文件 → 默认全量兜底")


def test_load_config_deep_merge_partial(tmp_path):
    """用户只写想改的字段，未覆盖的键保留默认（深合并）。"""
    path = _write(tmp_path, {"network": {"port": 9999}})
    cfg = load_config(config_path=path)
    assert cfg["network"]["port"] == 9999, "覆盖生效"
    assert cfg["network"]["host"] == "127.0.0.1", "未覆盖保持默认"
    assert cfg["network"]["request_timeout"] == 120.0
    assert cfg["concurrency"]["max_concurrent_turns"] == 2, "他块不受影响"
    print("✓ 深合并：只覆盖出现的键")


def test_load_config_bad_json_falls_back(tmp_path):
    path = os.path.join(tmp_path, "config.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ not valid json !!!")
    cfg = load_config(config_path=path)
    assert cfg["network"]["port"] == 8766, "坏 json 应回退默认"
    print("✓ 坏 json → 默认兜底，不抛错")


def test_load_config_legacy_flat_llm_keys(tmp_path):
    """旧扁平 config（顶层 base_url/api_key/model/image）→ 归入 llm 块，平滑迁移。"""
    path = _write(
        tmp_path,
        {"base_url": "http://x/v1", "api_key": "k", "model": "m1", "image": {"enabled": True}},
    )
    cfg = load_config(config_path=path)
    assert cfg["llm"]["base_url"] == "http://x/v1"
    assert cfg["llm"]["model"] == "m1"
    assert cfg["llm"]["image"]["enabled"] is True
    print("✓ 旧扁平 llm 键兼容归块")


def test_llm_time_budget_defaults_present_and_merged():
    """两道时间闸必须有默认值，且能深合并进**用户已有的** config。

    这是"用户零改动"的保证：线上 config.json 的 llm 块只有 base_url/api_key/model/image/
    headers 五个键，若默认值不参与深合并，收紧超时就要玩家手工改文件 —— 等于没修。
    """
    cfg = load_config(config_path="nonexistent_config_xyz.json")
    assert cfg["llm"]["timeout"] == 45.0, "单次上限默认值缺失"
    assert cfg["llm"]["total_budget"] == 100.0, "总预算默认值缺失"
    assert cfg["llm"]["retries"] == 1

    # 模拟线上那份"只有五个键"的 llm 块
    d = tempfile.mkdtemp()
    p = _write(d, {"llm": {"base_url": "http://x/v1", "api_key": "k", "model": "m",
                           "image": {"enabled": True}, "headers": {"a": "b"}}})
    merged = load_config(config_path=p)["llm"]
    assert merged["timeout"] == 45.0 and merged["total_budget"] == 100.0
    assert merged["headers"] == {"a": "b"}, "深合并不得丢掉用户已有键"
    print("✓ llm.timeout / total_budget 默认值存在且深合并进用户配置")


def test_get_block_and_default_identity():
    cfg = load_config(config_path="nonexistent_config_xyz.json")
    assert get_block(cfg, "network")["port"] == 8766
    assert get_block(cfg, "no_such_block", default={"a": 1}) == {"a": 1}
    # DEFAULT_CONFIG 是模板，load 返回其深拷贝（改动不污染模板）
    cfg2 = load_config(config_path="nonexistent_config_xyz.json")
    cfg2["network"]["port"] = 1
    assert cfg["network"]["port"] == DEFAULT_CONFIG["network"]["port"] == 8766
    print("✓ get_block + load 返回独立拷贝（模板不污染）")


if __name__ == "__main__":
    import tempfile as _tf
    with _tf.TemporaryDirectory() as d:
        test_load_config_defaults_when_file_missing()
        test_load_config_deep_merge_partial(d)
        test_load_config_bad_json_falls_back(d)
        test_load_config_legacy_flat_llm_keys(d)
    test_llm_time_budget_defaults_present_and_merged()
    test_get_block_and_default_identity()
    print("\nconfig_loader 测试全部通过")