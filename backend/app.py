#!/usr/bin/env python3
"""Ubuntu entrypoint: init, doctor, or serve. No mock chain mode."""
import argparse
import json
import os
from pathlib import Path
import sys

from degreeapp import create_app
from degreeapp.chain import ChainClient, ChainError
from degreeapp.setup import initialize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "doctor", "serve"))
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    os.umask(0o077)
    try:
        if args.command == "init":
            result = initialize(project, ChainClient(project))
            print("网页数据库与三角色账号已准备；本次导入已核对链上哈希的演示证书数量：", result["imported"])
            print("数据库：", result["database"])
            print("登录账号及随机密码文件（请在本机查看，不必反馈其内容）：", result["credentials"])
        elif args.command == "doctor":
            app = create_app(project)
            client = app.test_client()
            if client.get("/healthz").status_code != 200 or client.get("/login").status_code != 200:
                raise RuntimeError("网页基础检查未通过。")
            health = app.extensions["registry"].chain.health()
            print("检查通过：网页、数据库、角色配置及真实合约 health 均可用。")
            print("合约：", health["contract"], "协议：", health["schema_version"])
            print("此检查没有签发或撤销证书；浏览器业务验收按第六步说明进行。")
        else:
            if not 1024 <= args.port <= 65535:
                raise RuntimeError("请使用 1024–65535 之间的端口。")
            from waitress import serve
            app = create_app(project)
            print(f"学历证书存证系统：http://{args.host}:{args.port}", flush=True)
            print("请保持本终端运行；按 Ctrl+C 停止网页服务，链节点继续运行。", flush=True)
            serve(app, host=args.host, port=args.port, threads=4, expose_tracebacks=False)
        return 0
    except (ChainError, RuntimeError, OSError, ValueError, KeyError) as exc:
        print("未完成：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
