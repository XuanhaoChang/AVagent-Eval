#!/usr/bin/env python3
"""Privately configure one account-assigned ngrok HTTPS endpoint (no API key)."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import warnings
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def endpoint_url(value: str) -> str:
    value = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("域名包含非法控制字符。")
    try:
        url = urlsplit(value if "://" in value else "https://" + value)
        domain = url.hostname or ""
        port = url.port
    except ValueError:
        raise ValueError("域名格式无效，请从 ngrok 控制台重新复制。") from None
    if (url.scheme != "https" or url.username or url.password or url.query or url.fragment
            or url.path not in ("", "/") or port is not None
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.(?:ngrok-free\.app|ngrok-free\.dev|ngrok\.app)", domain)):
        raise ValueError("请输入 ngrok 控制台分配的 HTTPS 域名，不含路径、端口或密钥。")
    return "https://" + domain


def private_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(directory: Path, domain: str, token: str) -> Path:
    public_url = endpoint_url(domain)
    token = token.strip()
    if not 16 <= len(token) <= 512 or any(not 33 <= ord(char) <= 126 for char in token):
        raise ValueError("authtoken 格式无效；不要粘贴整条命令。")
    # JSON-quoted strings are also valid YAML scalars, preventing config injection.
    config = (f'version: "3"\nagent:\n  authtoken: {json.dumps(token)}\n'
              '  web_addr: false\n  inspect_db_size: -1\n  update_check: false\n'
              '  remote_management: false\n  log_level: warn\n'
              f'endpoints:\n  - name: avagent-eval\n    url: {json.dumps(public_url)}\n'
              '    upstream:\n      url: http://127.0.0.1:8766\n')
    path = directory / "ngrok.yml"
    private_write(path, config)
    private_write(directory / "endpoint.txt", public_url + "\n")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=ROOT / ".local/ngrok")
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        print("请在交互式服务器终端运行；不要通过命令参数或管道传递密钥。", file=sys.stderr)
        return 2
    try:
        print("将为 avagent-eval 配置 ngrok 公网 HTTPS 入口。")
        print("127.0.0.1:8766 是服务器内部转发目标，不是你在自己电脑上打开的网址。")
        print("仅公开这个网站，保留它当前的访问模式；不添加 ngrok 登录或限流策略。")
        if input("确认此服务可以公开？输入 yes 继续: ").strip().lower() != "yes":
            print("已取消，未写入配置或启动隧道。")
            return 1
        print("免费域名查看：https://dashboard.ngrok.com/domains")
        domain = input("ngrok 控制台分配的域名: ")
        # Validate non-secret input before asking for the credential.
        endpoint_url(domain)
        print("令牌查看：https://dashboard.ngrok.com/get-started/your-authtoken（不是 GitHub Token）")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("ngrok authtoken（不回显）: ")
        path = configure(args.config_dir, domain, token)
        token = ""
    except (ValueError, OSError, EOFError, KeyboardInterrupt, getpass.GetPassWarning) as error:
        print("配置未完成：" + (str(error) if isinstance(error, ValueError) else "输入取消或无法写入私有配置。"), file=sys.stderr)
        return 1
    print(f"私有配置已保存到 {path}（权限 0600）。")
    print("尚未验证远端账号授权，也未启动隧道；请告诉助手已完成，不要发送配置文件或密钥。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
