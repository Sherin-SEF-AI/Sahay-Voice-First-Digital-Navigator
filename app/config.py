"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """SAHAY application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Google Cloud
    google_cloud_project: str
    google_cloud_location: str = "us-central1"
    google_genai_use_vertexai: bool = True

    # Gemini Models
    gemini_computer_use_model: str = "gemini-2.5-computer-use-preview-10-2025"
    gemini_voice_model: str = "gemini-2.5-flash-native-audio"

    # Firestore
    firestore_collection: str = "sahay_tasks"

    # Application
    app_port: int = 8080
    screen_width: int = 1440
    screen_height: int = 900
    default_language: str = "hi"
    browser_headless: bool = True


settings = Settings()
