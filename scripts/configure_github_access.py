#!/usr/bin/env python3
"""Cache a repository-scoped GitHub credential in memory, never on disk.

Run interactively on the server. The token is supplied through a hidden prompt
and Git's stdin, never command-line arguments, URLs or a configuration file.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import warnings


DEFAULT_REPOSITORY = "XuanhaoChang/AVagent-Eval"
CACHE_DAYS = 30
CACHE_TIMEOUT_SECONDS = CACHE_DAYS * 24 * 60 * 60


def credential_command(action: str) -> list[str]:
    return ["git", "-c", "credential.helper=", "-c",
            f"credential.helper=cache --timeout={CACHE_TIMEOUT_SECONDS}",
            "-c", "credential.useHttpPath=true", "credential", action]


def credential_input(repository: str, username: str, token: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid repository name")
    if not re.fullmatch(r"[A-Za-z0-9-]+", username):
        raise ValueError("Invalid GitHub username")
    # Strip only terminal paste delimiters; reject embedded control characters.
    token = token.strip()
    if not token or any(ord(character) < 33 or ord(character) > 126 for character in token):
        raise ValueError("Token 包含空格或控制字符，请重新复制完整 token。")
    return (f"protocol=https\nhost=github.com\npath={repository}.git\n"
            f"username={username}\npassword={token}\n\n")


def api_get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        "https://api.github.com/" + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "User-Agent": "avagent-eval-access-setup"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def cache_credential(repository: str, username: str, token: str) -> None:
    # Disable inherited tracing: Git's credential protocol must stay private.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("GIT_TRACE") and key != "GIT_CURL_VERBOSE"}
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/false")
    result = subprocess.run(credential_command("approve"),
                            input=credential_input(repository, username, token),
                            text=True, capture_output=True, env=environment, timeout=15)
    if result.returncode:
        # Do not repeat helper diagnostics: some Git errors echo invalid values.
        raise RuntimeError("Git 凭据缓存失败；未输出敏感诊断内容。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        parser.error("repository must have the form owner/repository")
    if not sys.stdin.isatty():
        print("请在服务器的交互式终端直接运行，不要用管道传入 token。", file=sys.stderr)
        return 1
    print(f"目标仓库：{args.repository}")
    print(f"请输入现有且仍有效的 token；输入不显示，凭据只在内存中缓存 {CACHE_DAYS} 天。")
    print("这不会延长 GitHub 上 token 本身的有效期；服务器重启或缓存进程退出会清空缓存。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("GitHub Token: ").strip()
        credential_input(args.repository, "validation", token)
        account = api_get("user", token)
        repository = api_get("repos/" + args.repository, token)
        if not repository.get("permissions", {}).get("push"):
            print("该账号没有目标仓库的写入权限。", file=sys.stderr)
            return 1
        cache_credential(args.repository, account["login"], token)
        del token
        print(f"已验证登录账号：{account['login']}")
        print(f"凭据已按 {CACHE_DAYS} 天缓存。账号权限不等于 token 写入权限；接下来需要推送预检。")
        print("告诉我“已配置个人仓库”，我会进行不修改远端的推送预检。")
        return 0
    except urllib.error.HTTPError as error:
        print(f"GitHub 验证返回 HTTP {error.code}；请检查 token 的归属、仓库选择及有效期。", file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。", file=sys.stderr)
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
    except Exception as error:
        print(f"授权检查失败（{type(error).__name__}），未显示敏感内容。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
