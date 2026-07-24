from pathlib import Path

import httpx

from flywheel.config import TelegramSettings
from flywheel.domain.result import AgentQuestion

API_ROOT = "https://api.telegram.org"


class TelegramNotifier:
    def __init__(self, settings: TelegramSettings, timeout: int = 30) -> None:
        self._settings = settings
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return self._settings.configured

    def _url(self, method: str) -> str:
        return f"{API_ROOT}/bot{self._settings.bot_token}/{method}"

    def send(self, text: str, buttons: list[tuple[str, str]] | None = None) -> bool:
        if not self.enabled:
            return False
        payload: dict[str, object] = {
            "chat_id": self._settings.target,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": label, "url": url}] for label, url in buttons]
            }
        try:
            response = httpx.post(self._url("sendMessage"), json=payload, timeout=self._timeout)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def send_photo(self, image: Path, caption: str) -> bool:
        if not self.enabled or not image.exists():
            return False
        try:
            with image.open("rb") as handle:
                response = httpx.post(
                    self._url("sendPhoto"),
                    data={
                        "chat_id": self._settings.target,
                        "caption": caption[:1000],
                        "parse_mode": "Markdown",
                    },
                    files={"photo": (image.name, handle)},
                    timeout=self._timeout,
                )
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def pull_request_ready(
        self,
        repository: str,
        issue_number: int,
        title: str,
        pull_request_url: str,
        preview_url: str | None,
        screenshots: list[Path],
        summary: str,
    ) -> bool:
        lines = [
            f"*Ready for review* - {repository}",
            f"#{issue_number} {title}",
            "",
            summary[:600],
        ]
        if preview_url:
            lines += ["", f"Preview: {preview_url}"]
        buttons = [("Open pull request", pull_request_url)]
        if preview_url:
            buttons.append(("Open preview", preview_url))
        sent = self.send("\n".join(lines), buttons)
        for screenshot in screenshots[:5]:
            self.send_photo(screenshot, f"{repository} #{issue_number}")
        return sent

    def question(
        self, repository: str, issue_number: int, questions: list[AgentQuestion], issue_url: str
    ) -> bool:
        lines = [f"*A decision is needed* - {repository} #{issue_number}", ""]
        for index, question in enumerate(questions, start=1):
            lines.append(f"{index}. {question.question}")
            for option in question.options:
                lines.append(f"   - {option}")
        lines += ["", "Reply in the issue and the agent will continue."]
        return self.send("\n".join(lines), [("Answer on GitHub", issue_url)])

    def failed(self, repository: str, issue_number: int, error: str, issue_url: str) -> bool:
        return self.send(
            f"*Task failed* - {repository} #{issue_number}\n\n```\n{error[:600]}\n```",
            [("Open issue", issue_url)],
        )

    def update(
        self,
        repository: str,
        issue_number: int,
        heading: str,
        detail: str,
        issue_url: str,
    ) -> bool:
        return self.send(
            f"*{heading}* - {repository} #{issue_number}\n\n{detail[:1000]}",
            [("Open issue", issue_url)],
        )
