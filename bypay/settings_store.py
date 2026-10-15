"""运行设置读写（不含敏感信息，明文 JSON）。"""

import json
from pathlib import Path

from . import constants, logger


class SettingsStore:
    def __init__(self, path: Path = None):
        self.path = Path(path or constants.SETTINGS_FILE)

    def load(self) -> dict:
        data = dict(constants.DEFAULT_SETTINGS)
        if self.path.exists():
            try:
                data.update(json.loads(self.path.read_text("utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return data

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def set(self, key: str, value) -> dict:
        data = self.load()
        data[key] = value
        self.save(data)
        logger.info("config", "运行设置已更新", 项=key, 值=value)
        return data
