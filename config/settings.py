"""
OpenClaw TeamLab — Configuration Settings
Reads all environment variables with sensible defaults.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    """Centralized settings loaded from environment variables."""

    # ── Identity ──
    PROJECT_NAME: str = os.getenv("PROJECT_NAME", "openclaw_teamlab")
    INSTANCE_NAME: str = os.getenv("INSTANCE_NAME", "openclaw_teamlab")
    ENV: str = os.getenv("ENV", "prod")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # ── Paths ──
    PROJECT_ROOT: Path = PROJECT_ROOT
    SKILLS_DIR: Path = PROJECT_ROOT / "skills"
    WEB_DIR: Path = PROJECT_ROOT / "web"
    # 日志统一输出到项目 data/logs/，方便集中管理
    LOG_DIR: Path = Path(os.getenv("LOG_DIR", str(PROJECT_ROOT / "data" / "logs")))
    PID_DIR: Path = Path(os.getenv("PID_DIR", str(PROJECT_ROOT / "data" / "pids")))

    # ── LLM ──
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "http://endpoint/v1")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "model")
    LLM_FAST_MODEL: str = os.getenv("LLM_FAST_MODEL", "model")
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "65536"))

    # ── Gateway ──
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "10301"))
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me")

    # ── MySQL ──
    MYSQL_HOST: str = os.getenv("MYSQL_HOST", "10.100.81.177")
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER: str = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE", "openclaw_teamlab")

    @property
    def MYSQL_DSN(self) -> str:
        return (
            f"mysql+aiomysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
            f"?charset=utf8mb4"
        )

    # ── Redis ──
    # 内部连接地址（Docker 模式下为 host.docker.internal，宿主机模式为 127.0.0.1）
    REDIS_HOST: str = os.getenv("REDIS_HOST", "127.0.0.1")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "3"))
    REDIS_PREFIX: str = os.getenv("REDIS_PREFIX", "openclaw_teamlab:prod")

    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def REDIS_HOST_DISPLAY(self) -> str:
        """日志展示地址：将 Docker 内部别名统一显示为 127.0.0.1。"""
        if self.REDIS_HOST in ("host.docker.internal", "docker.host.internal"):
            return "127.0.0.1"
        return self.REDIS_HOST

    # ── Feishu ──
    FEISHU_APP_ID: str = os.getenv("FEISHU_APP_ID", "")
    FEISHU_APP_SECRET: str = os.getenv("FEISHU_APP_SECRET", "")

    # ── DingTalk ──
    DINGTALK_CLIENT_ID: str = os.getenv("DINGTALK_CLIENT_ID", "")
    DINGTALK_CLIENT_SECRET: str = os.getenv("DINGTALK_CLIENT_SECRET", "")
    DINGTALK_AGENT_ID: str = os.getenv("DINGTALK_AGENT_ID", "")

    # ── Email ──
    SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "")
    SMTP_AUTH_CODE: str = os.getenv("SMTP_AUTH_CODE", "")

    # ── Worker Pool ──
    WORKER_MIN: int = int(os.getenv("WORKER_MIN", "3"))
    WORKER_MAX: int = int(os.getenv("WORKER_MAX", "20"))
    WORKER_PORT_BASE: int = int(os.getenv("WORKER_PORT_BASE", "10310"))

    # ── Scheduler ──
    SCHEDULER_PORT: int = int(os.getenv("SCHEDULER_PORT", "10302"))

    # ── CoEvo DB (Read-only source — cognalign-coevo prod) ──
    COEVO_MYSQL_HOST: str = os.getenv("COEVO_MYSQL_HOST", "10.100.81.177")
    COEVO_MYSQL_PORT: int = int(os.getenv("COEVO_MYSQL_PORT", "3306"))
    COEVO_MYSQL_USER: str = os.getenv("COEVO_MYSQL_USER", "root")
    COEVO_MYSQL_PASSWORD: str = os.getenv("COEVO_MYSQL_PASSWORD", "agent!1234")
    COEVO_MYSQL_DATABASE: str = os.getenv("COEVO_MYSQL_DATABASE", "cognalign_coevo_prod")

    @property
    def COEVO_MYSQL_DSN(self) -> str:
        return (
            f"mysql+aiomysql://{self.COEVO_MYSQL_USER}:{self.COEVO_MYSQL_PASSWORD}"
            f"@{self.COEVO_MYSQL_HOST}:{self.COEVO_MYSQL_PORT}/{self.COEVO_MYSQL_DATABASE}"
            f"?charset=utf8mb4"
        )

    # ── Logging ──
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
