#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_semantic.py — 语义判定模块回归测试

重点覆盖两件事：
  1. 证据采集的准确性（PyPI 包名 ≠ import 名的各种坑）
  2. 规则校验层能否拦住模型的错误结论（防幻觉闸门）

用 fixture_project 作为固定测试夹具，结果可复现。
用法：python test_semantic.py [-v]
"""

import sys
from pathlib import Path

from semantic_audit import (import_names, collect_evidence, validate_judgment,
                            parse_model_json, judge_by_rules, judge_one,
                            RuleBackend, USAGE_ENUM, BOUNDARY_ENUM)

VERBOSE = "-v" in sys.argv
FIXTURE = Path(__file__).parent / "fixture_project"
PASS = FAIL = 0
FAILURES = []


def check(case_id, desc, got, want):
    global PASS, FAIL
    ok = got == want
    if ok:
        PASS += 1
        if VERBOSE:
            print(f"  PASS  [{case_id}] {desc} → {got}")
    else:
        FAIL += 1
        FAILURES.append((case_id, desc, got, want))
        print(f"  FAIL  [{case_id}] {desc}")
        print(f"        期望 = {want}")
        print(f"        实际 = {got}")


# ============================================================ A. 包名 → import 名映射

print("\nA. PyPI 包名 ≠ import 名（不处理会把'已使用'误判成'未使用'）")

check("A1", "scikit-learn 应映射到 sklearn",
      "sklearn" in import_names("scikit-learn"), True)
check("A2", "PyYAML 应映射到 yaml",
      "yaml" in import_names("PyYAML"), True)
check("A3", "python-Levenshtein 应映射到 Levenshtein",
      "Levenshtein" in import_names("python-Levenshtein"), True)
check("A4", "PyMuPDF 应同时给出 pymupdf 与 fitz",
      {"pymupdf", "fitz"} <= set(import_names("pymupdf")), True)
check("A5", "mysqlclient 应映射到 MySQLdb",
      "MySQLdb" in import_names("mysqlclient"), True)
check("A6", "普通包名应原样返回",
      "requests" in import_names("requests"), True)


# ============================================================ B. 证据采集

print("\nB. 使用证据采集（在固定夹具上运行）")

ev_pandas = collect_evidence(FIXTURE, "pandas")
check("B1", "「import pandas as pd」带别名写法必须被检出（最易漏）",
      ev_pandas["import_count"] > 0, True)
check("B2", "pandas 出现在多个文件中应全部计入",
      len(ev_pandas["files"]) >= 2, True)
check("B3", "pandas 非测试专用",
      ev_pandas["test_only"], False)

ev_sk = collect_evidence(FIXTURE, "scikit-learn")
check("B4", "scikit-learn 应通过别名 sklearn 被检出",
      ev_sk["import_count"] > 0, True)
check("B5", "scikit-learn 应判定为仅测试环节使用",
      ev_sk["test_only"], True)

ev_fuzzy = collect_evidence(FIXTURE, "fuzzywuzzy")
check("B6", "vendored 副本应被检出",
      ev_fuzzy["vendored_path"], "vendor/fuzzywuzzy")

ev_lev = collect_evidence(FIXTURE, "python-Levenshtein")
check("B7", "补丁文件应被检出",
      len(ev_lev["patch_files"]) > 0, True)

ev_pymupdf = collect_evidence(FIXTURE, "pymupdf")
check("B8", "pymupdf 直接调用应被检出",
      ev_pymupdf["import_count"] > 0, True)

ev_mysql = collect_evidence(FIXTURE, "mysqlclient")
check("B9", "仅在依赖清单声明、源码未调用时 import_count 应为 0",
      ev_mysql["import_count"], 0)
check("B10", "但应标记为已在依赖清单中声明",
      ev_mysql["in_requirements"], True)


# ============================================================ C. 规则校验层（防幻觉闸门）

print("\nC. 规则校验层——能否拦住模型的错误结论")


def mk_judgment(usage="作为库调用（未修改源码）",
                boundary="未修改，仅调用公开 API",
                trigger="否", reason="测试", conf="中"):
    return {"使用方式": usage, "自主开发边界": boundary,
            "许可义务是否触发": trigger, "理由": reason, "置信度": conf}


# C1 证据显示有 vendored 副本，模型却说"未修改" → 必须驳回
j = mk_judgment(boundary="未修改，仅调用公开 API")
probs = validate_judgment(j, "GPL-3.0-only", ev_fuzzy)
check("C1", "有 vendored 副本却声称「未修改」应被驳回",
      any("不得声称未修改" in p for p in probs), True)

# C2 证据显示有补丁文件，模型却说义务不触发 → 必须驳回
j = mk_judgment(trigger="否")
probs = validate_judgment(j, "GPL-2.0-or-later", ev_lev)
check("C2", "有补丁文件却声称「义务不触发」应被驳回",
      any("不可能不触发" in p for p in probs), True)

# C3 传染性许可 + 源码直接调用，模型却说义务不触发 → 必须驳回
j = mk_judgment(trigger="否")
probs = validate_judgment(j, "AGPL-3.0-only", ev_pymupdf)
check("C3", "AGPL 且源码直接调用却声称「义务不触发」应被驳回",
      any("义务不可能不触发" in p for p in probs), True)

# C4 枚举值越界 → 必须驳回
j = mk_judgment(usage="随便瞎写的一个使用方式")
probs = validate_judgment(j, "MIT", ev_pandas)
check("C4", "「使用方式」取值越界应被驳回",
      any("取值越界" in p for p in probs), True)

# C5 无任何使用证据却给出高置信度 → 必须驳回
j = mk_judgment(conf="高")
probs = validate_judgment(j, "MIT", ev_mysql)
check("C5", "无证据却给出高置信度应被驳回",
      any("过度自信" in p for p in probs), True)

# C6 理由为空 → 必须驳回
j = mk_judgment(reason="   ")
probs = validate_judgment(j, "MIT", ev_pandas)
check("C6", "「理由」为空应被驳回",
      any("理由" in p for p in probs), True)

# C7 合法输出 → 应通过
j = mk_judgment(usage="仅测试环节使用（不随产品分发）", trigger="否", conf="高")
probs = validate_judgment(j, "BSD-3-Clause", ev_sk)
check("C7", "合法且与证据一致的结论应通过校验", probs, [])

# C8 非字典输出 → 应驳回
probs = validate_judgment(None, "MIT", ev_pandas)
check("C8", "模型输出不是 JSON 对象应被驳回", len(probs) > 0, True)


# ============================================================ D. 模型输出解析

print("\nD. 模型输出解析（容忍代码块围栏与前后废话）")

check("D1", "带 ```json 围栏的输出应能解析",
      parse_model_json('```json\n{"a": 1}\n```'), {"a": 1})
check("D2", "前后带解释文字的输出应能解析",
      parse_model_json('好的，结论如下：{"a": 1} 以上。'), {"a": 1})
check("D3", "纯垃圾输入应返回 None", parse_model_json("完全不是 JSON"), None)
check("D4", "空输入应返回 None", parse_model_json(""), None)


# ============================================================ E. 回退行为

print("\nE. 模型不可用时必须安全回退，不能崩")


class BrokenBackend:
    """模拟模型服务不可用。"""
    name = "openai"

    def complete(self, system, user, model=None):
        raise RuntimeError("connection refused")


j, meta = judge_one("pymupdf", "AGPL-3.0-only", ev_pymupdf, BrokenBackend())
check("E1", "模型调用失败时应回退且不抛异常", meta["fell_back"], True)
check("E2", "回退后仍应给出正确结论（AGPL 义务触发）",
      j["许可义务是否触发"], "是")
check("E3", "回退结论应标明来源",
      "回退" in j.get("_来源", ""), True)


class HallucinatingBackend:
    """模拟模型幻觉：明明有 vendored 副本却说未修改。"""
    name = "openai"

    def complete(self, system, user, model=None):
        return ('{"使用方式":"作为库调用（未修改源码）",'
                '"自主开发边界":"未修改，仅调用公开 API",'
                '"许可义务是否触发":"否",'
                '"理由":"只是引用了这个包",'
                '"置信度":"高"}')


j, meta = judge_one("fuzzywuzzy", "GPL-3.0-only", ev_fuzzy, HallucinatingBackend())
check("E4", "模型幻觉应被校验层拦下", meta["fell_back"], True)
check("E5", "拦截后应改用规则结论（已二次开发）",
      j["自主开发边界"], "已二次开发")
check("E6", "拦截后义务判定应修正为触发",
      j["许可义务是否触发"], "是")


class GoodBackend:
    """模拟一个输出合规的模型。"""
    name = "openai"

    def complete(self, system, user, model=None):
        return ('{"使用方式":"仅测试环节使用（不随产品分发）",'
                '"自主开发边界":"未修改，仅调用公开 API",'
                '"许可义务是否触发":"否",'
                '"理由":"仅出现在 tests/test_app.py 中",'
                '"置信度":"高"}')


j, meta = judge_one("scikit-learn", "BSD-3-Clause", ev_sk, GoodBackend())
check("E7", "合规的模型输出应被采纳", meta["validated"], True)
check("E8", "采纳后应标注来源为模型判定",
      "模型判定" in j.get("_来源", ""), True)


# ============================================================ 汇总

print("\n" + "=" * 62)
print(f"用例总数 {PASS + FAIL}　通过 {PASS}　失败 {FAIL}")
if FAILURES:
    print("\n失败用例：")
    for cid, desc, got, want in FAILURES:
        print(f"  [{cid}] {desc}\n      期望 {want}\n      实际 {got}")
print("=" * 62)
sys.exit(1 if FAIL else 0)
