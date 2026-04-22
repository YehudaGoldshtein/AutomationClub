"""Smoke-test logger + config: `python -m inventory_sync`."""
from inventory_sync.config import load as load_config
from inventory_sync.log import configure


def main() -> None:
    log = configure()
    log.info("app_start", version="0.1.0")

    config = load_config()
    log.info(
        "boot_ok",
        store=config.shopify.store_url,
        vendor=config.vendor.name,
        interval=config.sync_interval,
    )

    log.info("app_end")


if __name__ == "__main__":
    main()
