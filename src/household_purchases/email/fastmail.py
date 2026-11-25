"""Read-only Fastmail JMAP adapter (RFCs 8620 and 8621)."""

import time
from collections.abc import Iterator
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from urllib.parse import quote, urlsplit

import httpx

from household_purchases.config import Settings

CORE = "urn:ietf:params:jmap:core"
MAIL = "urn:ietf:params:jmap:mail"
HOSTS = frozenset({"api.fastmail.com", "jmap.fastmail.com", "www.fastmailusercontent.com"})


class JmapError(ValueError):
    pass


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in HOSTS
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
        or parts.fragment
    ):
        raise JmapError("untrusted_jmap_url")
    return url


class FastmailClient:
    def __init__(
        self,
        token: str,
        account_id: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        if not token:
            raise JmapError("missing_token")
        self.http = httpx.Client(timeout=30, transport=transport, follow_redirects=False)
        self.token, self.sleep = token, sleep
        try:
            session = self.request("GET", "https://api.fastmail.com/jmap/session").json()
            if not {CORE, MAIL} <= session["capabilities"].keys():
                raise JmapError("missing_mail_capability")
            self.account_id = account_id or session["primaryAccounts"][MAIL]
            if MAIL not in session["accounts"][self.account_id]["accountCapabilities"]:
                raise JmapError("invalid_mail_account")
            self.api_url = safe_url(session["apiUrl"])
            self.download_url = session["downloadUrl"]
            safe_url(self.download_url)
            self.max_get = max(1, min(100, session["capabilities"][CORE]["maxObjectsInGet"]))
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        self.http.close()

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        safe_url(url)
        for attempt in range(4):
            try:
                response = self.http.request(
                    method, url, headers={"Authorization": f"Bearer {self.token}"}, **kwargs
                )
            except httpx.TransportError:
                if attempt == 3:
                    raise JmapError("transport_failed") from None
                self.sleep(2**attempt)
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt == 3:
                    raise JmapError("retries_exhausted")
                wait = response.headers.get("Retry-After", "")
                self.sleep(min(30, max(0, int(wait))) if wait.isdigit() else 2**attempt)
                continue
            if response.status_code >= 300:
                raise JmapError(
                    "authentication_failed" if response.status_code in (401, 403) else "http_failed"
                )
            return response
        raise JmapError("retries_exhausted")

    def call(self, method: str, args: dict) -> dict:
        if method not in {"Email/query", "Email/get"}:
            raise JmapError("read_only_method_required")
        payload = {
            "using": [CORE, MAIL],
            "methodCalls": [[method, {"accountId": self.account_id, **args}, "0"]],
        }
        result = self.request("POST", self.api_url, json=payload).json()
        responses = result.get("methodResponses", [])
        if len(responses) != 1 or responses[0][0] != method or responses[0][2] != "0":
            raise JmapError("jmap_method_failed")
        return responses[0][1]

    def discover(
        self, settings: Settings, *, since: str | None = None, until: str | None = None
    ) -> Iterator[dict]:
        filters = {"from": settings.sender, "subject": settings.subject}
        for key, value in (
            ("to", settings.recipient),
            ("inMailbox", settings.mailbox_id),
            ("after", since),
            ("before", until),
        ):
            if value:
                filters[key] = value
        position, state = 0, None
        seen: set[str] = set()
        while True:
            result = self.call(
                "Email/query",
                {
                    "filter": filters,
                    "sort": [{"property": "receivedAt", "isAscending": True}],
                    "position": position,
                    "limit": 100,
                },
            )
            if result.get("position") != position:
                raise JmapError("unexpected_query_position")
            if state is not None and state != result["queryState"]:
                raise JmapError("query_changed_retry_scan")
            state = result["queryState"]
            ids = result["ids"]
            if not ids:
                break
            fresh = list(dict.fromkeys(i for i in ids if i not in seen))
            if not fresh:
                raise JmapError("pagination_not_progressing")
            seen.update(fresh)
            for start in range(0, len(fresh), self.max_get):
                batch = fresh[start : start + self.max_get]
                metadata = self.call(
                    "Email/get", {"ids": batch, "properties": ["id", "blobId", "receivedAt"]}
                )
                if metadata.get("notFound") or {m["id"] for m in metadata["list"]} != set(batch):
                    raise JmapError("email_disappeared_retry_scan")
                yield from metadata["list"]
            position += len(ids)

    def download(self, blob_id: str) -> bytes:
        url = self.download_url
        for key, value in {
            "accountId": self.account_id,
            "blobId": blob_id,
            "name": "message.eml",
            "type": "message/rfc822",
        }.items():
            url = url.replace("{" + key + "}", quote(value, safe=""))
        if "{" in url or "}" in url:
            raise JmapError("unknown_download_template")
        return self.request("GET", url).content


def matches_headers(raw: bytes, settings: Settings) -> bool:
    message = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
    senders = [address.casefold() for _, address in getaddresses(message.get_all("From", []))]
    recipients = [
        address.casefold()
        for _, address in getaddresses(message.get_all("To", []) + message.get_all("Cc", []))
    ]
    return (
        senders == [settings.sender.casefold()]
        and str(message.get("Subject", "")).strip() == settings.subject
        and (not settings.recipient or settings.recipient.casefold() in recipients)
    )


def received_time(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise JmapError("naive_received_at")
    return timestamp
