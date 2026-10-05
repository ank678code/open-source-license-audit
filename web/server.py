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
import os
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

from license_audit import (parse_requirements_verbose, parse_pyproject_verbose,
                           parse_package_json_verbose, exclude_workspace_deps,
                           audit, category_of, obligations_of, to_checklist_table,
                           to_checklist_csv, to_checklist_rows,
                           render_report, CATEGORY_CN, VERSION)
from semantic_audit import collect_evidence, judge_by_rules, evidence_summary

MAX_BODY = 20 * 1024 * 1024      # 20MB
MAX_PKGS = 200                   # 单次审计的依赖上限，避免误传超大清单
JOBS = 12                        # 元数据抓取并发数（零第三方依赖，用标准库线程池）
MANIFEST_CANDIDATES = ["requirements.txt", "requirements/runtime.txt",
                       "requirements/common.txt", "pyproject.toml", "package.json"]
# 请求体上限只约束了"压缩包本身"。压缩比极高的包解压后可以膨胀到
# 几十 GB，把本机磁盘写满，因此还要限制解压后的总大小与条目数。
MAX_UNZIP_BYTES = 200 * 1024 * 1024
MAX_UNZIP_ENTRIES = 2000
# 合法清单类型 → 落盘扩展名。非法值此前会 KeyError 被兜底 except 捕获，
# 返回 500 内部错误；应作为客户端参数错误返回 400。
MANIFEST_KINDS = {"requirements": ".txt", "pyproject": ".toml",
                  "package-json": ".json"}


def manifest_ext(kind):
    """把前端传来的清单类型映射成扩展名；非法类型抛 ValueError（→ 400）。"""
    try:
        return MANIFEST_KINDS[kind]
    except KeyError:
        raise ValueError(
            f"不支持的清单类型：{kind}（可选：{'、'.join(MANIFEST_KINDS)}）")


def safe_extract(zf, dest):
    """解压 zip，拒绝路径穿越与绝对路径，并限制解压后的规模。"""
    dest = Path(dest).resolve()
    members = zf.infolist()
    if len(members) > MAX_UNZIP_ENTRIES:
        raise ValueError(f"压缩包条目数过多（{len(members)} > {MAX_UNZIP_ENTRIES}）")
    total = sum(max(0, m.file_size) for m in members)
    if total > MAX_UNZIP_BYTES:
        raise ValueError(f"压缩包解压后过大（约 {total // (1024 * 1024)}MB > "
                         f"{MAX_UNZIP_BYTES // (1024 * 1024)}MB）")
    for m in members:
        name = m.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts:
            raise ValueError(f"压缩包内含非法路径：{m.filename}")
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest)):
            raise ValueError(f"压缩包内含非法路径：{m.filename}")
    zf.extractall(dest)


# 说明：`-r` 引用的跟随与路径围栏（path_within）统一由
# license_audit.parse_requirements_verbose() 提供，CLI 与 Web 共用同一套判定，
# 避免两个入口各写一份、日后只改一处导致防护宽度不一致。此处不再单独实现。


MANIFEST_SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "env", "__pycache__",
                      ".idea", ".vscode", "dist", "build", "site-packages"}


def discover_manifests(project_dir, max_depth=3):
    """按优先级列出项目里的依赖清单，返回 [(相对路径, 绝对路径), ...]。

    根目录在前（MANIFEST_CANDIDATES 顺序），子目录按层加深排在其后；
    限 max_depth 层，跳过 node_modules / .git 等永远不可能是"项目根"的目录。
    注意返回的是**全部候选**而不是第一个命中——根目录清单可能是空的或
    解析不出依赖，此时应继续尝试子目录里的（S6 用例守的就是这个行为）。
    此前只在根目录找——src/、backend/ 这类常见布局的 zip 会直接报
    「没找到依赖清单」，是用户反馈 zip 上传不好用的主要原因。
    """
    hits = []
    for rel in MANIFEST_CANDIDATES:
        p = project_dir / rel
        if p.is_file():
            hits.append((rel, p))

    def walk(d, depth, prefix):
        if depth > max_depth:
            return
        for child in sorted(d.iterdir()):
            if not child.is_dir() or child.name in MANIFEST_SKIP_DIRS:
                continue
            if child.name.startswith("."):
                continue
            for rel in MANIFEST_CANDIDATES:
                p = child / rel
                if p.is_file():
                    hits.append((prefix + child.name + "/" + rel, p))
            walk(child, depth + 1, prefix + child.name + "/")

    walk(project_dir, 1, "")
    return hits


def drill_into_single_root(project_dir):
    """压缩包里只有一层根目录时逐层下钻（GitHub 导出的 zip、先建文件夹再压缩都常见）。

    此前只下钻一层，两层嵌套的包（如 repo/main/src）仍会找不到清单。
    注意：唯一的子目录是 node_modules / .git 这类目录时**不能**下钻——
    否则会把依赖包里的清单当成项目清单（S8 用例守这个行为）。
    """
    d = Path(project_dir)
    while True:
        entries = list(d.iterdir())
        subs = [x for x in entries if x.is_dir()
                and x.name not in MANIFEST_SKIP_DIRS and not x.name.startswith(".")]
        if len(subs) == 1 and not any(x.is_file() for x in entries):
            d = subs[0]
        else:
            return d


def detect_and_parse(project_dir):
    """在解压后的项目里找依赖清单并解析。

    返回 (清单相对路径, 包名列表, 生态, 版本约束表, 已排除的工作区内部包)。
    清单发现交给 discover_manifests：根目录优先，没有时搜 3 层内子目录；
    `-r` 引用由 parse_requirements_verbose 统一跟随（含目录围栏、环路
    与深度保护），不再在这里自己写一份。
    """
    for rel, path in discover_manifests(project_dir):
        ws_deps = []
        try:
            if rel.endswith(".toml"):
                vpkgs = parse_pyproject_verbose(path)
                kind = "PyPI"
            elif rel.endswith(".json"):
                vpkgs = parse_package_json_verbose(path)
                kind = "npm"
                # monorepo 内部包不发布到 npm，查了必然 404 并被记成"源站无此包"
                vpkgs, ws_deps = exclude_workspace_deps(vpkgs)
            else:
                vpkgs = parse_requirements_verbose(path, include_root=project_dir)
                kind = "PyPI"
        except Exception:
            continue
        if vpkgs:
            pkgs = [n for n, _ in vpkgs]
            specs = {n.lower(): s for n, s in vpkgs}
            return rel, pkgs, kind, specs, ws_deps
    return None, [], "PyPI", {}, []


def build_payload(project_name, project_license, records, findings,
                  project_dir=None, manifest_label="", workspace_excluded=()):
    from collections import Counter
    resolved = [r for r in records if r["spdx"] != "UNKNOWN"]
    high = [r for r in records if r.get("confidence") == "高"]
    cats = Counter(category_of(r["spdx"]) for r in records)
    hi = sum(1 for f in findings if f["level"] == "高")

    out_records = []
    judgments = {}
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
            # 把语义判定回填进清单表，避免"页面上写已二次开发、
            # 下载的 Markdown 里却写未修改源码"这种自相矛盾
            judgments[r["name"]] = j
        out_records.append(row)

    # 清单的三种出口全部走 to_checklist_rows() 这一个数据源：
    # checklist_rows 供页面直接渲染（所见），md/csv 供下载（所得）。
    # 以前前端另拼一张 8 列表当 CSV，用户下到的根本不是要求的 11 列清单。
    head, crows = to_checklist_rows(records, judgments)
    pending = sum(1 for row in crows if row[6].startswith("待确认"))

    # 完整合规报告也一并给出（含逐项判定、风险明细与未识别归因）。
    # locked 与 CLI 同一口径；judgments 传进去，报告里的清单才不会与页面打架。
    locked = sum(1 for r in records if r.get("requested_version"))
    report_md, _ = render_report(project_name, project_license, records, findings,
                                 locked, judgments)

    return {
        "project": project_name, "project_license": project_license,
        "manifest": manifest_label,
        "tool_version": VERSION,
        "stats": {
            "total": len(records), "resolved": len(resolved),
            "resolve_rate": round(100.0 * len(resolved) / len(records), 1) if records else 0.0,
            "high_confidence": len(high),
            "high_conf_rate": round(100.0 * len(high) / len(records), 1) if records else 0.0,
            "findings": len(findings), "findings_high": hi,
            "categories": {CATEGORY_CN.get(k, k): v for k, v in cats.items()},
            "semantic": bool(project_dir),
            # 清单里还有多少行的「使用方式」是待确认的——这是交付前必须人工补的
            "checklist_pending": pending,
            # 按清单锁定的精确版本查询的依赖数（与 CLI 报告同一口径）
            "locked": locked,
            # 把这些内部包显式报出来。数量对得上，用户才知道
            # 依赖数比 package.json 里少是"排除"还是"漏解析"。
            "workspace_excluded": len(workspace_excluded),
        },
        "workspace_excluded": list(workspace_excluded),
        "records": out_records,
        "findings": findings,
        "checklist_columns": head,
        "checklist_rows": crows,
        "checklist_md": to_checklist_table(records, judgments),
        "checklist_csv": to_checklist_csv(records, judgments),
        "report_md": report_md,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = f"license-audit/{VERSION}"

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
                try:
                    raw = base64.b64decode(req["zip_b64"].split(",")[-1])
                    safe_extract(zipfile.ZipFile(io.BytesIO(raw)), tmp)
                except (ValueError, zipfile.BadZipFile) as e:
                    # 压缩包本身的问题属于客户端输入错误，应回 400；
                    # 此前一律落到兜底 except 返回 500，语义不准也不好排查
                    return self._send(400, json.dumps({"error": f"压缩包被拒绝：{e}"}))
                proj = Path(tmp)
                # 压缩包里"只有一层根目录"时逐层下钻（GitHub 导出的 zip、
                # 先建文件夹再压缩的习惯都常见，两层嵌套也会遇到）
                proj = drill_into_single_root(proj)
                rel, pkgs, src, specs, ws = detect_and_parse(proj)
                if not pkgs:
                    found = discover_manifests(proj)
                    if found:
                        return self._send(400, json.dumps({"error":
                            "找到依赖清单但没能解析出依赖：%s。"
                            "可能文件为空，或清单格式不被支持。"
                            % "、".join(r for r, _ in found)}))
                    return self._send(400, json.dumps({"error":
                        "压缩包里没找到可解析的依赖清单。已检查根目录及 3 层内子目录，"
                        "支持：requirements.txt、requirements/*.txt、pyproject.toml、"
                        "package.json。常见原因：清单在更深处、压缩包里没有依赖声明、"
                        "或上传的不是项目压缩包。"}))
                records, findings = audit(sorted(set(pkgs))[:MAX_PKGS], plic, src,
                                          version_specs=specs, jobs=JOBS)
                payload = build_payload(pname, plic, records, findings, proj, rel, ws)
                # 让前端能说明「用了哪份清单、还有哪些备选」
                payload["manifest_candidates"] = [r for r, _ in discover_manifests(proj)]
            else:
                text = req.get("manifest") or ""
                if not text.strip():
                    return self._send(400, json.dumps({"error": "依赖清单为空"}))
                kind = req.get("kind") or "requirements"
                try:
                    ext = manifest_ext(kind)
                except ValueError as e:
                    return self._send(400, json.dumps({"error": str(e)}))
                tmp = tempfile.mkdtemp(prefix="audit_")
                mf = Path(tmp) / ("manifest" + ext)
                mf.write_text(text, encoding="utf-8")
                ws = []
                if kind == "pyproject":
                    vpkgs, src = parse_pyproject_verbose(mf), "PyPI"
                elif kind == "package-json":
                    vpkgs, src = parse_package_json_verbose(mf), "npm"
                    vpkgs, ws = exclude_workspace_deps(vpkgs)
                else:
                    # include_root 限定在临时目录内：粘贴的清单里若写了
                    # `-r /etc/passwd` 之类的绝对路径，围栏会直接拒绝
                    vpkgs, src = parse_requirements_verbose(mf, include_root=tmp), "PyPI"
                pkgs = [n for n, _ in vpkgs]
                specs = {n.lower(): s for n, s in vpkgs}
                if not pkgs:
                    return self._send(400, json.dumps({"error": "没有解析出任何依赖，请检查清单格式"}))
                records, findings = audit(sorted(set(pkgs))[:MAX_PKGS], plic, src,
                                          version_specs=specs, jobs=JOBS)
                payload = build_payload(pname, plic, records, findings, None,
                                        "粘贴的清单", ws)
            self._send(200, json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            self._send(500, json.dumps({"error": f"审计失败：{e}"}))
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    # 命令行参数优先；没给就取 PORT / HOST 环境变量（托管平台按约定注入 PORT，
    # 并要求监听 0.0.0.0），最后退回本地默认值。
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("PORT") or 8770))
    ap.add_argument("--host", default=os.environ.get("HOST") or "127.0.0.1")
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
