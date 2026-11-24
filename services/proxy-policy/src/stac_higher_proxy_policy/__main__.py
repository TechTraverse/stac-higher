"""Entry point: upstream's ``python -m stac_auth_proxy`` with our app factory."""

import os

import uvicorn
from uvicorn.config import LOGGING_CONFIG

if __name__ == "__main__":
    uvicorn.run(
        "stac_higher_proxy_policy.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        log_config={
            **LOGGING_CONFIG,
            "loggers": {
                **LOGGING_CONFIG["loggers"],
                "stac_auth_proxy": {"level": "DEBUG", "handlers": ["default"]},
                "stac_higher_proxy_policy": {"level": "INFO", "handlers": ["default"]},
            },
        },
    )
