"""Configuration for QA Navigator."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = {"env_prefix": "QA_NAV_"}

    # Gemini models
    computer_use_model: str = "gemini-3-flash-preview"
    analysis_model: str = "gemini-3-flash-preview"

    def model_post_init(self, __context) -> None:
        # Strip trailing whitespace from model names (Windows bat files sometimes add spaces)
        object.__setattr__(self, "computer_use_model", self.computer_use_model.strip())
        object.__setattr__(self, "analysis_model", self.analysis_model.strip())

    # Browser config
    screen_width: int = 800
    screen_height: int = 600
    headless: bool = False
    browser_timeout_ms: int = 30000

    # Orchestrator config
    max_retries_per_item: int = 2
    item_timeout_seconds: int = 120
    settle_time_seconds: float = 0.5
    inter_item_delay_seconds: float = 30.0  # Delay between items to stay under 2M token/min quota

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
