import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Claude
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = "claude-opus-4-6"

    # GitHub
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    GITHUB_API_URL: str = "https://api.github.com"

    # Jira
    JIRA_BASE_URL: str = os.getenv("JIRA_BASE_URL", "")
    JIRA_EMAIL: str = os.getenv("JIRA_EMAIL", "")
    JIRA_API_TOKEN: str = os.getenv("JIRA_API_TOKEN", "")
    JIRA_PROJECT_KEY: str = os.getenv("JIRA_PROJECT_KEY", "QE")

    # Kafka / PubSub
    KAFKA_BOOTSTRAP_SERVERS: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    KAFKA_TOPIC: str = os.getenv("KAFKA_TOPIC_PRODUCT_SIGNALS", "product-signals")

    # Test infrastructure
    DEVICE_FARM_ENDPOINT: str = os.getenv("DEVICE_FARM_ENDPOINT", "http://localhost:4444")
    DEVICE_FARM_CONCURRENCY: int = int(os.getenv("DEVICE_FARM_CONCURRENCY", "4"))

    # Webhook server
    WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8080"))
    GITHUB_WEBHOOK_SECRET: str = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    JIRA_WEBHOOK_SECRET: str = os.getenv("JIRA_WEBHOOK_SECRET", "")

    # ngrok (local tunnel for development)
    NGROK_AUTH_TOKEN: str = os.getenv("NGROK_AUTH_TOKEN", "")
    NGROK_DOMAIN: str = os.getenv("NGROK_DOMAIN", "")   # optional: your reserved ngrok domain

    # Git
    GIT_COMMIT_AUTO_HEAL: bool = os.getenv("GIT_COMMIT_AUTO_HEAL", "false").lower() == "true"


config = Config()
