import base64
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class GitHubContentError(Exception):
    """Base error for GitHub-backed content operations."""


class GitHubConfigurationError(GitHubContentError):
    pass


class GitHubAuthenticationError(GitHubContentError):
    pass


class GitHubConflictError(GitHubContentError):
    pass


class GitHubNotFoundError(GitHubContentError):
    pass


class GitHubAPIError(GitHubContentError):
    pass


class GitHubNetworkError(GitHubContentError):
    pass


class GitHubContentStore:
    API_ROOT = "https://api.github.com"

    def __init__(self, token, owner, repo, branch, content_root="content", timeout=10, opener=None):
        self.token = token.strip() if token else ""
        self.owner = owner.strip() if owner else ""
        self.repo = repo.strip() if repo else ""
        self.branch = branch.strip() if branch else ""
        self.content_root = content_root.strip("/") if content_root else ""
        self.timeout = timeout
        self.opener = opener or urlopen

    @property
    def configured(self):
        return bool(self.owner and self.repo and self.branch and self.content_root)

    def _validate_configuration(self, require_token=False):
        if not self.configured or (require_token and not self.token):
            raise GitHubConfigurationError("GitHub content persistence is not fully configured.")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", self.repo):
            raise GitHubConfigurationError("GitHub repository configuration contains an invalid owner or repository.")
        branch_parts = self.branch.split("/")
        root_parts = self.content_root.split("/")
        if any(not re.fullmatch(r"[A-Za-z0-9._-]+", part) or part in {".", ".."} for part in branch_parts + root_parts):
            raise GitHubConfigurationError("GitHub branch or content root contains an invalid path segment.")

    def _content_path(self, relative_path):
        if not isinstance(relative_path, str) or not relative_path:
            raise GitHubConfigurationError("A content file path is required.")
        segments = relative_path.split("/")
        if any(segment in {"", ".", ".."} for segment in segments):
            raise GitHubConfigurationError("Invalid content file path.")
        if any(not re.fullmatch(r"[A-Za-z0-9._-]+", segment) for segment in segments):
            raise GitHubConfigurationError("Invalid content file path.")
        return f"{self.content_root}/{relative_path}"

    def _request(self, method, relative_path, payload=None, require_token=False):
        self._validate_configuration(require_token=require_token)
        path = quote(self._content_path(relative_path), safe="/")
        endpoint = f"{self.API_ROOT}/repos/{quote(self.owner, safe='')}/{quote(self.repo, safe='')}/contents/{path}"
        if method == "GET":
            endpoint = f"{endpoint}?{urlencode({'ref': self.branch})}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ADHAYAN-content-service",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(endpoint, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw_response = response.read()
        except HTTPError as error:
            if error.code == 404:
                return None
            if error.code in {401, 403}:
                raise GitHubAuthenticationError("GitHub rejected the configured credentials or repository access.") from None
            if error.code in {409, 422}:
                raise GitHubConflictError("The GitHub content file changed concurrently. Reload and try again.") from None
            raise GitHubAPIError(f"GitHub returned HTTP {error.code} for a content operation.") from None
        except (URLError, TimeoutError, OSError):
            raise GitHubNetworkError("GitHub could not be reached. No content changes were saved.") from None

        if not raw_response:
            return {}
        try:
            return json.loads(raw_response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise GitHubAPIError("GitHub returned an invalid API response.") from None

    def read_file(self, relative_path):
        result = self._request("GET", relative_path)
        if result is None:
            return None
        if result.get("type") != "file" or not isinstance(result.get("content"), str):
            raise GitHubAPIError("The configured GitHub path is not a regular content file.")
        try:
            content = base64.b64decode(result["content"], validate=False).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise GitHubAPIError("The GitHub content file is not valid UTF-8 text.") from None
        return content, result.get("sha")

    def write_file(self, relative_path, content, message):
        self._validate_configuration(require_token=True)
        current = self.read_file(relative_path)
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": self.branch,
        }
        if current is not None:
            payload["sha"] = current[1]
        response = self._request("PUT", relative_path, payload, require_token=True)
        if not isinstance(response, dict) or not response.get("content", {}).get("sha"):
            raise GitHubAPIError("GitHub did not confirm the content update.")
        return response["content"]["sha"]

    def delete_file(self, relative_path, message):
        self._validate_configuration(require_token=True)
        current = self.read_file(relative_path)
        if current is None:
            raise GitHubNotFoundError("The GitHub content file does not exist.")
        response = self._request(
            "DELETE",
            relative_path,
            {"message": message, "sha": current[1], "branch": self.branch},
            require_token=True,
        )
        if not isinstance(response, dict) or "commit" not in response:
            raise GitHubAPIError("GitHub did not confirm the content deletion.")
        return response