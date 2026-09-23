# -*- coding: utf-8 -*-
"""vendored 副本：本团队在 3.0.1 基础上做了以下修改（原包为 GPL-3.0）。

修改点：
  1. 修复 process.extractOne 在候选项为空时抛 IndexError 的问题
  2. 增加中文标点归一化预处理，提升中文短文本匹配准确率
"""
__version__ = "3.0.1+local.2"


def normalize_cjk(text):
    """中文标点归一化预处理（本团队新增）。"""
    table = str.maketrans("，。！？；：", ",.!?;:")
    return text.translate(table)


def extract_one(query, choices, scorer=None):
    """在原实现基础上修复空候选列表的崩溃问题（本团队修改）。"""
    if not choices:
        return None
    best, score = None, -1
    for c in choices:
        s = scorer(query, c) if scorer else (1.0 if query == c else 0.0)
        if s > score:
            best, score = c, s
    return best, score
