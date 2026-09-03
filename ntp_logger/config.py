"""Loading of ``config.yaml``.

Kept as its own module so that config validation (interface names, auth
method, required keys) has somewhere to live as it grows.
"""

import yaml

__all__ = ["load_config"]


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)
