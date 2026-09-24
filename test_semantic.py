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
                            parse_model_json, judge_one,
                            RuleBackend)

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


j, meta = judge_one("fuzzywuzzy", "GPL-2.0-only", ev_fuzzy, HallucinatingBackend())
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


# ============================================================ F. 测试文件判定

print("\nF. 测试文件路径判定（v0.3 修复：子串匹配会把结论带反）")

from semantic_audit import (is_test_path, judgments_of, render_checklist,
                            OllamaBackend, OpenAICompatibleBackend)

# 修复前的实现是 `"test" in rel.lower()`，会把下面这些生产代码误判为测试文件，
# 进而把真实调用判成"仅测试环节使用、许可义务不触发"——这是会把结论带反的方向。
for cid, rel, want in [
    ("F1", "tests/test_app.py", True),
    ("F2", "test/foo.py", True),
    ("F3", "testing/util.py", True),
    ("F4", "spec/helper.py", True),
    ("F5", "__tests__/a.py", True),
    ("F6", "conftest.py", True),
    ("F7", "src/foo_test.py", True),
    ("F8", "src/foo_spec.py", True),
    ("F9", "app.py", False),
    ("F10", "lib/legacy.py", False),
    # 以下四条是修复前必然误判的回归用例
    ("F11", "contest/report.py", False),
    ("F12", "latest/run.py", False),
    ("F13", "src/attestation.py", False),
    ("F14", "mytest.py", False),
    ("F15", "protest/x.py", False),
]:
    check(cid, f"is_test_path({rel!r}) 应为 {want}", is_test_path(rel), want)

check("F16", "空路径不应被判为测试文件", is_test_path(""), False)
check("F17", "路径分隔符反斜杠也应正确处理",
      (is_test_path("tests\\test_app.py"), is_test_path("contest\\x.py")), (True, False))


# ============================================================ G. 证据采集边界

print("\nG. 证据采集（在自建目录结构上验证，含回归场景）")

import tempfile
from pathlib import Path as _P

_proj = _P(tempfile.mkdtemp(prefix="ev_"))


def _mkfile(rel, text):
    p = _proj / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# requests 出现在 4 个"名字里带 test 但不是测试"的生产文件 + 1 个真测试文件
_mkfile("app.py", "import requests\n")
_mkfile("contest/report.py", "import requests\n")
_mkfile("latest/run.py", "import requests\n")
_mkfile("src/attestation.py", "import requests\n")
_mkfile("mytest.py", "import requests\n")
_mkfile("tests/test_app.py", "import requests\n")
# pandas 只在测试里出现
_mkfile("tests/test_pandas.py", "import pandas as pd\n")
# vendored 副本与补丁文件
_mkfile("_vendor/fuzzywuzzy/__init__.py", "# vendored\n")
_mkfile("third_party/chardet/__init__.py", "# vendored\n")
_mkfile("patches/chardet.patch", "--- a/x\n+++ b/x\n")
_mkfile("requirements.txt", "requests\nfuzzywuzzy\nchardet\n")

_ev_req = collect_evidence(_proj, "requests")
check("G1", "带别名的 import 与普通 import 都应计入",
      _ev_req["import_count"], 6)
check("G2", "名字含 test 的生产文件不得让整个包被判为测试专用",
      _ev_req["test_only"], False)

_ev_pd = collect_evidence(_proj, "pandas")
check("G3", "只在 tests/ 下出现的包应判定为测试专用",
      _ev_pd["test_only"], True)
check("G4", "test_only 的包 import_count 仍应被记录",
      _ev_pd["import_count"], 1)

_ev_fz = collect_evidence(_proj, "fuzzywuzzy")
check("G5", "_vendor/ 下的 vendored 副本应被检出",
      _ev_fz["vendored_path"], "_vendor/fuzzywuzzy")

_ev_cd = collect_evidence(_proj, "chardet")
check("G6", "third_party/ 下的 vendored 副本应被检出",
      _ev_cd["vendored_path"], "third_party/chardet")
check("G7", "补丁文件应被检出",
      _ev_cd["patch_files"], ["patches/chardet.patch"])
check("G8", "出现在依赖清单里应被标记",
      _ev_req["in_requirements"], True)

_ev_none = collect_evidence(_proj, "this-package-does-not-exist")
check("G9", "完全不存在的包应返回零证据而不是报错",
      (_ev_none["import_count"], _ev_none["vendored_path"], _ev_none["patch_files"]),
      (0, None, []))


# ============================================================ H. 校验规则补充

print("\nH. 规则校验层补充（弱传染 / 仅测试 / 边界值）")

# H1 有 vendored 副本却说义务不触发 → 驳回
probs = validate_judgment(mk_judgment(trigger="否"), "GPL-2.0-only", _ev_fz)
check("H1", "有 vendored 副本却声称「义务不触发」应被驳回",
      any("不可能不触发" in p for p in probs), True)

# H2 弱传染许可 + 直接调用 + 声称不触发 → 规则层不拦（LGPL 动态链接下确实可能不触发）
probs = validate_judgment(mk_judgment(trigger="否"), "LGPL-3.0-only", _ev_req)
check("H2", "弱传染许可不应被当成强传染来拦截",
      [p for p in probs if "不可能不触发" in p], [])

# H3 强传染许可但仅测试使用 → 规则层不拦（不随产品分发，义务一般不触发）
probs = validate_judgment(
    mk_judgment(usage="仅测试环节使用（不随产品分发）", trigger="否", conf="高"),
    "GPL-3.0-only", _ev_pd)
check("H3", "强传染许可但仅测试使用，不应因「义务不触发」被驳回",
      [p for p in probs if "不可能不触发" in p], [])

# H4 置信度缺失不应被当作"过度自信"
probs = validate_judgment(mk_judgment(conf=None), "MIT", ev_mysql)
check("H4", "置信度缺失不应被判定为过度自信", probs, [])

# H5/H6 枚举越界
probs = validate_judgment(mk_judgment(boundary="随便写"), "MIT", ev_pandas)
check("H5", "「自主开发边界」取值越界应被驳回",
      any("取值越界" in p for p in probs), True)
probs = validate_judgment(mk_judgment(trigger="也许"), "MIT", ev_pandas)
check("H6", "「许可义务是否触发」取值越界应被驳回",
      any("取值越界" in p for p in probs), True)

# H7 全空字典 → 应报多处问题而不是崩溃
probs = validate_judgment({}, "MIT", ev_pandas)
check("H7", "空判定对象应报多处问题而不是崩溃", len(probs) >= 3, True)


# ============================================================ I. 后端与输出

print("\nI. 后端抽象与清单输出")

check("I1", "规则后端不应调用模型", RuleBackend().complete("s", "u"), None)
check("I2", "Ollama 后端名称应为 ollama", OllamaBackend().name, "ollama")
check("I3", "OpenAI 兼容后端名称应为 openai", OpenAICompatibleBackend().name, "openai")
check("I4", "OpenAI 兼容后端应支持自定义 base_url",
      OpenAICompatibleBackend(base_url="https://x/v1").base_url, "https://x/v1")
check("I5", "Ollama 后端应去掉 host 末尾斜杠",
      OllamaBackend("http://localhost:11434/").host, "http://localhost:11434")
check("I6", "嵌套花括号的 JSON 应能解析",
      parse_model_json('前缀 {"a": {"b": 2}} 后缀'), {"a": {"b": 2}})
# 数组本身是合法 JSON，解析层不该报错；但校验层必须拒绝它，
# 这才是真正要守住的属性——模型返回数组时不能当成判定结果用。
check("I7", "数组输出虽能解析，但必须被校验层拒绝",
      len(validate_judgment(parse_model_json("[1, 2, 3]"), "MIT", ev_pandas)) > 0, True)

_rows = [{"name": "fuzzywuzzy",
          "judgment": {"使用方式": "修改源码 / 二次开发", "自主开发边界": "已二次开发"}}]
check("I8", "judgments_of 应抽出 {包名: 判定}",
      list(judgments_of(_rows).keys()), ["fuzzywuzzy"])
_cl = render_checklist({"project": "演示项目", "project_license": "MIT",
                        "records": [{"name": "fuzzywuzzy", "source": "PyPI",
                                     "version": "0.18.0", "license_raw": "GPLv2",
                                     "spdx": "GPL-2.0-only",
                                     "license_field": "trove classifier",
                                     "confidence": "高", "deps": [], "status": "OK"}]},
                       _rows)
check("I9", "竞赛清单应包含「使用方式」列",
      "使用方式" in _cl, True)
check("I10", "竞赛清单应写入证据驱动的判定结果",
      "修改源码 / 二次开发" in _cl, True)
check("I11", "竞赛清单应包含「自主开发边界」与「关键许可义务」列",
      ("自主开发边界" in _cl) and ("关键许可义务" in _cl), True)

for cid, pkg, want in [("I12", "scikit-image", "skimage"),
                       ("I13", "opencv-python", "cv2"),
                       ("I14", "Pillow", "PIL"),
                       ("I15", "beautifulsoup4", "bs4"),
                       ("I16", "protobuf", "google.protobuf"),
                       ("I17", "huggingface-hub", "huggingface_hub"),
                       ("I18", "python-dateutil", "dateutil")]:
    check(cid, f"{pkg} 应映射到 {want}", want in import_names(pkg), True)


# ============================================================ 汇总

print("\n" + "=" * 62)
print(f"用例总数 {PASS + FAIL}　通过 {PASS}　失败 {FAIL}")
if FAILURES:
    print("\n失败用例：")
    for cid, desc, got, want in FAILURES:
        print(f"  [{cid}] {desc}\n      期望 {want}\n      实际 {got}")
print("=" * 62)
sys.exit(1 if FAIL else 0)
