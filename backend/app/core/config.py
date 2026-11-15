from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

from pydantic import Field, IPvAnyNetwork, field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    environment: str = "development"
    # Origins allowed to call the API from a browser (the Vite dev server).
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    session_cookie_name: str = "chronos_session"
    session_ttl_seconds: int = Field(default=8 * 60 * 60, gt=0)
    session_cookie_secure: bool = False
    # Host header values this API answers to. Tests point their client at
    # http://localhost rather than adding TestClient's "testserver" here,
    # so this list stays the same in a test run and in a real deployment.
    trusted_hosts: list[str] = ["localhost", "127.0.0.1"]
    # Networks a client's TCP peer address must fall in. Setting this replaces
    # the default, so keep the loopback entries when adding a LAN subnet.
    allowed_client_networks: list[IPvAnyNetwork] = Field(
        default=["127.0.0.0/8", "::1/128"], validate_default=True
    )
    # Test environment only: username -> Argon2id hash. When non-empty, every
    # request except /health and /deployment.txt needs HTTP Basic credentials
    # from this map before the app's own login (docs/TEST_ENVIRONMENT.md).
    dev_access_users: dict[str, str] = {}
    # Production only: the test environment's loopback address (for example
    # http://127.0.0.1:20318). When set, /devtest/ forwards to it.
    devtest_upstream: str | None = None
    # Built frontend (index.html + assets/) served by this API; unset in
    # development, where the Vite dev server serves the frontend.
    frontend_dist_dir: str | None = None
    # Development operational defaults, not institutional performance targets.
    job_poll_interval_seconds: float = Field(default=1.0, gt=0)
    job_lease_seconds: int = Field(default=30, gt=0)
    job_heartbeat_interval_seconds: float = Field(default=5.0, gt=0)
    job_termination_grace_seconds: float = Field(default=5.0, gt=0)
    job_max_attempts: int = Field(default=3, gt=0)
    job_ga_timeout_seconds: int = Field(default=600, gt=0)
    job_arm_timeout_seconds: int = Field(default=300, gt=0)
    # Twenty GA runs, each bounded like a single GA job.
    job_evaluation_timeout_seconds: int = Field(default=12000, gt=0)

    @field_validator("allowed_client_networks", mode="before")
    @classmethod
    def accept_host_bits(cls, value):
        # "192.168.1.10/24" means the server's LAN, not a typo to crash on.
        if isinstance(value, list):
            return [ip_network(v, strict=False) if isinstance(v, str) else v for v in value]
        return value

    @field_validator("devtest_upstream")
    @classmethod
    def loopback_upstream_only(cls, value):
        # CHR-ENV-004: the proxy may never reach past this machine.
        if value is None:
            return value
        parts = urlsplit(value)
        try:
            loopback = parts.hostname == "localhost" or ip_address(parts.hostname).is_loopback
        except (TypeError, ValueError):
            loopback = False
        if parts.scheme != "http" or not loopback or parts.path not in {"", "/"}:
            raise ValueError("DEVTEST_UPSTREAM must be http://127.0.0.1:<port>")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_job_timing(self) -> "Settings":
        if self.job_heartbeat_interval_seconds >= self.job_lease_seconds:
            raise ValueError("job heartbeat interval must be shorter than the lease")
        return self

    class Config:
        env_file = ".env"


settings = Settings()
