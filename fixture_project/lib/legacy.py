# -*- coding: utf-8 -*-
"""历史数据处理模块。"""
import pandas as pd


def normalize_records(df):
    return pd.DataFrame(df).dropna(how="all").reset_index(drop=True)
