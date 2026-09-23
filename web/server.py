#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
web/server.py — 可演示的 Web 界面（纯标准库，无第三方依赖）

端点：
  GET  /                  → 前端页面
  POST /api/audit         → 执行审计，返回 JSON 结果

请求体（二选一）：
  { "manifest": "<依赖清单文本>", "kind": "requirements|pyproject|package-json",
    "project_name": "...", "project_license": "MIT" }
  { "zip_b64": "<项目 zip 的 base64>", "project_name": "...", "project_license": "MIT" }

用法：
  python web/server.py            # 默认 http://127.0.0.1:8770
  python web/server.py --port 9000
"""

import argparse
import base64
import io
import json
import shutil
import sys
import tempfile
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from license_audit import (parse_requirements, parse_pyproject, parse_package_json,
                           audit, category_of, obligations_of, to_checklist_table,
                           CATEGORY_CN, LICENSE_DB)
from semantic_audit import collect_evidence, judge_by_rules, evidence_summary

MAX_BODY = 20 * 1024 * 1024      # 20MB
MANIFEST_CANDIDATES = ["requirements.txt", "requirements/runtime.txt",
                       "requirements/common.txt", "pyproject.toml", "package.json"]


def safe_extract(zf, dest):
    """解压 zip，拒绝路径穿越与绝对路径。"""
    dest = Path(dest).resolve()
    for m in zf.infolist():
        name = m.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts:
            raise ValueError(f"压缩包内含非法路径：{m.filename}")
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest)):
            raise ValueError(f"压缩包内含非法路径：{m.filename}")
    zf.extractall(dest)


def detect_and_parse(project_dir):
    """在解压后的项目里找依赖清单并解析。"""
    for rel in MANIFEST_CANDIDATES:
        p = project_dir / rel
        if not p.exists():
            continue
        try:
            if rel.endswith(".toml"):
                pkgs = parse_pyproject(p)
                kind = "PyPI"
            elif rel.endswith(".json"):
                pkgs = parse_package_json(p)
                kind = "npm"
            else:
                pkgs = parse_requirements(p)
                kind = "PyPI"
                if not pkgs:                       # 指针文件：跟随 -r 引用
                    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                        s = line.strip()
                        if s.lower().startswith(("-r", "--requirement")):
                            inc = s.split(None, 1)[1].strip()
                            q = (p.parent / inc)
                            if q.exists():
                                pkgs = parse_requirements(q)
                                kind = "PyPI"
                                break
        except Exception:
            continue
        if pkgs:
            return rel, pkgs, kind
    return None, [], "PyPI"


def build_payload(project_name, project_license, records, findings,
                  project_dir=None, manifest_label=""):
    from collections import Counter
    resolved = [r for r in records if r["spdx"] != "UNKNOWN"]
    high = [r for r in records if r.get("confidence") == "高"]
    cats = Counter(category_of(r["spdx"]) for r in records)
    hi = sum(1 for f in findings if f["level"] == "高")

    out_records = []
    for r in records:
        cat = category_of(r["spdx"])
        row = {
            "name": r["name"], "version": r["version"], "spdx": r["spdx"],
            "category": cat, "category_cn": CATEGORY_CN.get(cat, cat),
            "obligation": obligations_of(r["spdx"]),
            "confidence": r.get("confidence", "?"),
            "field": r.get("license_field", "?"),
            "raw": r.get("license_raw", ""),
            "status": r["status"],
        }
        if project_dir:
            ev = collect_evidence(project_dir, r["name"])
            j = judge_by_rules(r["name"], r["spdx"], ev)
            row.update({"usage": j["使用方式"], "boundary": j["自主开发边界"],
                        "trigger": j["许可义务是否触发"], "reason": j["理由"],
                        "evidence": evidence_summary(ev)})
        out_records.append(row)

    return {
        "project": project_name, "project_license": project_license,
        "manifest": manifest_label,
        "stats": {
            "total": len(records), "resolved": len(resolved),
            "resolve_rate": round(100.0 * len(resolved) / len(records), 1) if records else 0.0,
            "high_confidence": len(high),
            "high_conf_rate": round(100.0 * len(high) / len(records), 1) if records else 0.0,
            "findings": len(findings), "findings_high": hi,
            "categories": {CATEGORY_CN.get(k, k): v for k, v in cats.items()},
            "semantic": bool(project_dir),
        },
        "records": out_records,
        "findings": findings,
        "checklist_md": to_checklist_table(records),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "license-audit/0.2"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            f = HERE / "index.html"
            if not f.exists():
                return self._send(404, "index.html 缺失", "text/plain; charset=utf-8")
            return self._send(200, f.read_text(encoding="utf-8"),
                              "text/html; charset=utf-8")
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path != "/api/audit":
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(413, json.dumps({"error": "请求体过大（上限 20MB）"}))
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            return self._send(400, json.dumps({"error": f"请求解析失败：{e}"}))

        pname = (req.get("project_name") or "未命名项目").strip()[:80]
        plic = (req.get("project_license") or "MIT").strip()
        tmp = None
        try:
            if req.get("zip_b64"):
                tmp = tempfile.mkdtemp(prefix="audit_")
                raw = base64.b64decode(req["zip_b64"].split(",")[-1])
                safe_extract(zipfile.ZipFile(io.BytesIO(raw)), tmp)
                proj = Path(tmp)
                # 若压缩包内只有一层根目录，自动下钻
                subs = [x for x in proj.iterdir() if x.is_dir()]
                files = [x for x in proj.iterdir() if x.is_file()]
                if len(subs) == 1 and not files:
                    proj = subs[0]
                rel, pkgs, src = detect_and_parse(proj)
                if not pkgs:
                    return self._send(400, json.dumps(
                        {"error": "压缩包里没找到可解析的依赖清单（requirements.txt / pyproject.toml / package.json）"}))
                records, findings = audit(sorted(set(pkgs))[:200], plic, src)
                payload = build_payload(pname, plic, records, findings, proj, rel)
            else:
                text = req.get("manifest") or ""
                if not text.strip():
                    return self._send(400, json.dumps({"error": "依赖清单为空"}))
                kind = req.get("kind") or "requirements"
                tmp = tempfile.mkdtemp(prefix="audit_")
                ext = {"requirements": ".txt", "pyproject": ".toml",
                       "package-json": ".json"}[kind]
                mf = Path(tmp) / ("manifest" + ext)
                mf.write_text(text, encoding="utf-8")
                if kind == "pyproject":
                    pkgs, src = parse_pyproject(mf), "PyPI"
                elif kind == "package-json":
                    pkgs, src = parse_package_json(mf), "npm"
                else:
                    pkgs, src = parse_requirements(mf), "PyPI"
                if not pkgs:
                    return self._send(400, json.dumps({"error": "没有解析出任何依赖，请检查清单格式"}))
                records, findings = audit(sorted(set(pkgs))[:200], plic, src)
                payload = build_payload(pname, plic, records, findings, None, "粘贴的清单")
            self._send(200, json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            self._send(500, json.dumps({"error": f"审计失败：{e}"}))
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"许可证合规检查 Web 界面已启动：http://{a.host}:{a.port}")
    print("按 Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
