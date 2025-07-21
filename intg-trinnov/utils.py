"""Utility functions for logging setup and command validation in the Trinnov integration."""


import logging
import os
from enum import Enum
from typing import Type


def setup_logger():
    """Get logger from all modules"""

    level = os.getenv("UC_LOG_LEVEL", "DEBUG").upper()

    logging.getLogger("ucapi.api").setLevel(level)
    logging.getLogger("ucapi.entities").setLevel(level)
    logging.getLogger("ucapi.entity").setLevel(level)
    logging.getLogger("driver").setLevel(level)
    logging.getLogger("config").setLevel(level)
    logging.getLogger("discover").setLevel(level)
    logging.getLogger("setup_flow").setLevel(level)
    logging.getLogger("device").setLevel(level)
    logging.getLogger("remote").setLevel(level)
    logging.getLogger("media_player").setLevel(level)
    logging.getLogger("sensor").setLevel(level)
    #logging.getLogger("pytrinnov").setLevel(level)



def validate_simple_commands_exist_on_executor(
    enum_class: Type[Enum],
    executor: object,
    logger: logging.Logger = logging.getLogger(__name__)
) -> list[str]:
    """
    Ensures that each command in the enum resolves to a callable method on the executor,
    using getattr(), which also triggers __getattr__ fallbacks.

    :param enum_class: Enum containing command names.
    :param executor: The CommandExecutor instance.
    :param logger: Logger for output.
    :return: List of commands that failed resolution.
    """
    missing = []

    for cmd in enum_class:
        method_name = cmd.value.lower()
        try:
            method = getattr(executor, method_name)
            if not callable(method):
                missing.append(method_name)
        except AttributeError:
            missing.append(method_name)

    if missing:
        logger.warning(
            "Executor missing methods for SimpleCommands: %s", ", ".join(missing)
        )
    else:
        logger.debug("All SimpleCommands are implemented by the executor.")

    return missing

def parse_toggle_command(prefix: str, simple_cmd: str) -> tuple[str, str] | None:
    """Parse Toggle Command"""
    toggle_map = {
        f"{prefix}_off": 0,
        f"{prefix}_on": 1,
        f"{prefix}_toggle": 2
    }
    code = toggle_map.get(simple_cmd)
    if code is not None:
        return prefix, str(code)
    return None
