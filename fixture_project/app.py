# -*- coding: utf-8 -*-
"""演示用项目主程序（fixture，用于验证证据采集与语义判定链路）。"""
import json

import requests
import pandas as pd
import pymupdf

from lib.legacy import normalize_records


API = "https://example.invalid/api/v1"


def fetch(url=API):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def build_frame(payload):
    df = pd.DataFrame(payload)
    return normalize_records(df)


def extract_pdf_text(path):
    """用 pymupdf 抽取 PDF 文本。"""
    doc = pymupdf.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def main():
    print(json.dumps(build_frame(fetch()).head(1).to_dict(), ensure_ascii=False))


if __name__ == "__main__":
    main()
