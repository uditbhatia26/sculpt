"""
GitHub for Chisel: the OAuth handshake, encrypted token storage, and a repo reader that
condenses a repository into a digest small enough to show an LLM.

Environment:
    GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET   OAuth app credentials
    APP_BASE_URL                             this backend's public URL (callback lives here)
"""
import asyncio
import base64
import hashlib
import logging
import os
from typing import Optional
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from config.auth import SECRET_KEY

logger = logging.getLogger("Sculpt")

GITHUB_API = "https://api.github.com"
AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"


class GitHubError(Exception):
    """A GitHub call failed in a way the user can act on. `reconnect` means the token is dead."""

    def __init__(self, message: str, reconnect: bool = False):
        super().__init__(message)
        self.reconnect = reconnect


def is_configured() -> bool:
    return bool(os.getenv("GITHUB_CLIENT_ID") and os.getenv("GITHUB_CLIENT_SECRET"))


def callback_url() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/") + "/auth/github/callback"


# ---------------------------------------------------------------- token at rest

def _fernet() -> Fernet:
    key = hashlib.sha256(("sculpt-github-token:" + SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(blob: Optional[str]) -> Optional[str]:
    if not blob:
        return None
    try:
        return _fernet().decrypt(blob.encode()).decode()
    except InvalidToken:
        return None  # the signing key changed: treat as disconnected


# ---------------------------------------------------------------- OAuth

def authorize_url(state: str, private: bool) -> str:
    """Where to send the user. With no scope GitHub grants public data only; `repo` is the
    only OAuth scope that reaches private repositories (and it is read/write on GitHub's
    side, though Sculpt only ever reads)."""
    params = {
        "client_id": os.getenv("GITHUB_CLIENT_ID", ""),
        "redirect_uri": callback_url(),
        "state": state,
        "allow_signup": "false",
    }
    if private:
        params["scope"] = "repo"
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> tuple:
    """Trade the callback code for (access_token, granted_scopes)."""
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.post(
            TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": os.getenv("GITHUB_CLIENT_ID", ""),
                "client_secret": os.getenv("GITHUB_CLIENT_SECRET", ""),
                "code": code,
                "redirect_uri": callback_url(),
            },
        )
    data = res.json() if res.status_code == 200 else {}
    token = data.get("access_token")
    if not token:
        raise GitHubError(data.get("error_description") or "GitHub did not return an access token.")
    scopes = {s.strip() for s in (data.get("scope") or "").split(",") if s.strip()}
    return token, scopes


# ---------------------------------------------------------------- API client

class GitHub:
    """A thin async client bound to one user's token."""

    def __init__(self, token: str):
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Sculpt",
        }

    async def _get(self, client: httpx.AsyncClient, path: str, *, raw: bool = False, params: dict = None):
        headers = dict(self._headers)
        if raw:
            headers["Accept"] = "application/vnd.github.raw"
        try:
            res = await client.get(f"{GITHUB_API}{path}", headers=headers, params=params)
        except httpx.HTTPError as e:
            raise GitHubError("Could not reach GitHub. Please try again.") from e
        if res.status_code == 401:
            raise GitHubError("Your GitHub connection has expired. Connect GitHub again.", reconnect=True)
        if res.status_code == 403 and res.headers.get("x-ratelimit-remaining") == "0":
            raise GitHubError("GitHub's rate limit was reached. Try again in a few minutes.")
        return res

    async def user(self) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await self._get(client, "/user")
        if res.status_code != 200:
            raise GitHubError("Could not read your GitHub profile.")
        return res.json()

    async def repos(self, page: int = 1, per_page: int = 30) -> list:
        """The user's repositories, most recently pushed first. Without the `repo` scope
        GitHub returns public ones only."""
        async with httpx.AsyncClient(timeout=20) as client:
            res = await self._get(client, "/user/repos", params={
                "sort": "pushed", "per_page": per_page, "page": page,
                "affiliation": "owner,collaborator,organization_member",
            })
        if res.status_code != 200:
            raise GitHubError("Could not list your repositories.")
        return [
            {
                "full_name": r["full_name"],
                "name": r["name"],
                "private": bool(r.get("private")),
                "fork": bool(r.get("fork")),
                "description": r.get("description") or "",
                "language": r.get("language") or "",
                "stars": r.get("stargazers_count", 0),
                "pushed_at": r.get("pushed_at"),
            }
            for r in res.json()
        ]

    async def repo_digest(self, full_name: str, username: str) -> dict:
        """Read one repository and condense it. Raises GitHubError if it can't be read or
        the user has no visible part in it."""
        async with httpx.AsyncClient(timeout=25) as client:
            meta_res = await self._get(client, f"/repos/{full_name}")
            if meta_res.status_code == 404:
                raise GitHubError("That repository wasn't found, or Sculpt doesn't have access to it.")
            if meta_res.status_code != 200:
                raise GitHubError("Could not read that repository.")
            meta = meta_res.json()
            branch = meta.get("default_branch") or "main"

            contributors_res, languages_res, readme_res, tree_res = await asyncio.gather(
                self._get(client, f"/repos/{full_name}/contributors", params={"per_page": 100}),
                self._get(client, f"/repos/{full_name}/languages"),
                self._get(client, f"/repos/{full_name}/readme", raw=True),
                self._get(client, f"/repos/{full_name}/git/trees/{branch}", params={"recursive": "1"}),
            )

            contributors = contributors_res.json() if contributors_res.status_code == 200 else []
            contributors = contributors if isinstance(contributors, list) else []
            mine = next((c for c in contributors if (c.get("login") or "").lower() == username.lower()), None)
            owner = (meta.get("owner") or {}).get("login", "")
            owns = owner.lower() == username.lower()
            # Yours if you committed to it, or it is your own (non-fork) repository.
            # A fork with none of your commits is someone else's work.
            if not mine and not (owns and not meta.get("fork")):
                raise GitHubError(
                    "You don't appear as a contributor to this repository. "
                    "Chisel only writes bullets for projects you worked on."
                )

            tree = tree_res.json().get("tree", []) if tree_res.status_code == 200 else []
            files = [(t["path"], t.get("size", 0)) for t in tree if t.get("type") == "blob" and _keep(t["path"])]
            if not files and readme_res.status_code != 200:
                raise GitHubError("This repository looks empty, so there is nothing to write bullets from.")

            picks = _pick_files(files)
            contents = await asyncio.gather(*[
                self._get(client, f"/repos/{full_name}/contents/{path}", raw=True, params={"ref": branch})
                for path, _ in picks
            ])

        total_commits = sum(c.get("contributions", 0) for c in contributors) or 0
        my_commits = (mine or {}).get("contributions", 0)
        languages = languages_res.json() if languages_res.status_code == 200 else {}
        readme = readme_res.text if readme_res.status_code == 200 else ""

        parts = [
            f"REPOSITORY: {full_name}",
            f"DESCRIPTION: {meta.get('description') or '(none)'}",
            f"TOPICS: {', '.join(meta.get('topics') or []) or '(none)'}",
            f"LANGUAGES (bytes): {', '.join(f'{k} {v}' for k, v in list(languages.items())[:8]) or '(unknown)'}",
            f"CONTRIBUTORS: {len(contributors)}; the candidate ({username}) made {my_commits} of {total_commits} commits"
            + ("; the candidate owns the repository" if owns else ""),
            "",
            "README:",
            _clip(readme, 5000) or "(no README)",
            "",
            f"FILE TREE ({len(files)} files, showing up to 150):",
            "\n".join(path for path, _ in files[:150]),
        ]
        for (path, _), res in zip(picks, contents):
            if res.status_code == 200:
                cap = 1500 if _is_manifest(path) else 3500
                parts += ["", f"FILE: {path}", _clip(res.text, cap)]

        return {
            "context": "\n".join(parts),
            "name": meta.get("name") or full_name.split("/")[-1],
            "url": meta.get("html_url") or f"https://github.com/{full_name}",
            "private": bool(meta.get("private")),
            "languages": list(languages.keys())[:8],
        }


# ---------------------------------------------------------------- choosing what to read

_SKIP_DIRS = {
    "node_modules", "vendor", "dist", "build", ".git", "__pycache__", ".venv", "venv", "env", "target",
    ".next", "out", "coverage", ".idea", ".vscode", "bin", "obj", "public", "assets", "static", "fixtures",
}
_SKIP_SUFFIXES = (
    ".lock", ".min.js", ".min.css", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".pdf", ".zip",
    ".woff", ".woff2", ".ttf", ".mp4", ".mp3", ".bin", ".pkl", ".pt", ".onnx", ".csv", ".parquet", ".ipynb",
)
_SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "license", "license.md"}
_MANIFESTS = {
    "package.json", "requirements.txt", "pyproject.toml", "go.mod", "cargo.toml", "pom.xml", "build.gradle",
    "gemfile", "composer.json", "dockerfile", "docker-compose.yml", "docker-compose.yaml", "pubspec.yaml",
}
_SOURCE_EXT = (
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".cpp", ".c",
    ".swift", ".dart", ".scala", ".sol", ".sql",
)
_KEY_STEMS = {
    "main", "app", "index", "server", "api", "routes", "router", "models", "model", "views", "handler",
    "handlers", "cli", "core", "train", "pipeline", "workflow", "service", "services", "schema", "background",
}


def _keep(path: str) -> bool:
    lower = path.lower()
    segments = lower.split("/")
    if any(seg in _SKIP_DIRS for seg in segments[:-1]):
        return False
    return not (segments[-1] in _SKIP_NAMES or lower.endswith(_SKIP_SUFFIXES))


def _is_manifest(path: str) -> bool:
    return path.lower().split("/")[-1] in _MANIFESTS


def _pick_files(files: list, manifests: int = 5, sources: int = 6) -> list:
    """Dependency manifests first (they name the stack), then the source files most likely
    to show what the project does: entry points and core modules near the top of the tree."""
    manifest_files = sorted((f for f in files if _is_manifest(f[0])), key=lambda f: f[0].count("/"))[:manifests]

    def score(item) -> float:
        path, size = item
        lower = path.lower()
        name = lower.split("/")[-1]
        stem = name.rsplit(".", 1)[0]
        if not lower.endswith(_SOURCE_EXT) or "test" in lower or "spec" in name or size < 300 or size > 60_000:
            return float("-inf")  # not a candidate
        return (3 if stem in _KEY_STEMS else 0) - lower.count("/") + min(size, 12_000) / 6_000

    ranked = sorted((f for f in files if score(f) != float("-inf")), key=score, reverse=True)[:sources]
    return manifest_files + ranked


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n… (truncated)"
