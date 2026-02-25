"""Configuration for QA Navigator."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = {"env_prefix": "QA_NAV_"}

    # Gemini models
    computer_use_model: str = "gemini-3-flash-preview"
    analysis_model: str = "gemini-3-flash-preview"

    # Browser config
    screen_width: int = 1280
    screen_height: int = 936
    headless: bool = False
    browser_timeout_ms: int = 30000

    # Orchestrator config
    max_retries_per_item: int = 2
    item_timeout_seconds: int = 120
    settle_time_seconds: float = 0.5

    # Checklist config
    max_checklist_items: int = 200
    min_checklist_items: int = 10

    # API keys
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    google_cloud_project: str = Field(default="", alias="GOOGLE_CLOUD_PROJECT")

    @property
    def screen_size(self) -> tuple[int, int]:
        return (self.screen_width, self.screen_height)


settings = Settings()
