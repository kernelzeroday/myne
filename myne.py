#!/usr/bin/env python3

import hashlib
import os
import subprocess
import sys
import logging
from functools import wraps
import asyncio
from typing import Tuple, List
import json

LOG_TO_FILE = False
LOG_FILE_PATH = "myne.log"
LOG_FILE_JSON_PATH = "myne.json"
DEFAULT_HOST = "kelsey-ai.local:3333"
SCRIPT_PATH = os.path.realpath(__file__)
LOCAL_SCRIPT_PATH = "/usr/local/bin/myne.py"
HOME_SCRIPT_PATH = "/home/kelsey/bin/myne.py"
CPULIMIT_PATH = "/usr/bin/cpulimit"
XMRIG_PATH = "/usr/local/bin/xmrig"
PGREP_CMD = ["pgrep", "xmrig"]
SLEEP_INTERVAL = 60
CPU_LIMIT_THRESHOLD = 8
CPU_LIMIT_HIGH = 100
CPU_LIMIT_LOW = 40
USAGE_LIMIT = 0.85

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)
if LOG_TO_FILE:
    file_handler = logging.FileHandler(LOG_FILE_PATH)
    json_handler = logging.FileHandler(LOG_FILE_JSON_PATH)
    logging.getLogger().addHandler(file_handler)
    logging.getLogger().addHandler(json_handler)


def log_decorator(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        logging.debug(
            f"\033[94mCalling function {func.__name__} with args: {args}, kwargs: {kwargs}\033[0m"
        )
        try:
            result = await func(*args, **kwargs)
            logging.debug(f"\033[92mFunction {func.__name__} returned: {result}\033[0m")
            return result
        except Exception as e:
            logging.error(
                f"\033[91mFunction {func.__name__} raised an exception: {e}\033[0m"
            )
            raise

    return wrapper


@log_decorator
async def calculate_file_hash(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


@log_decorator
async def install_self() -> bool:
    try:
        current_hash = await calculate_file_hash(SCRIPT_PATH)
        if current_hash is None:
            raise Exception("Failed to calculate current script hash.")
        if os.path.exists(LOCAL_SCRIPT_PATH):
            installed_hash = await calculate_file_hash(LOCAL_SCRIPT_PATH)
            if installed_hash is None:
                raise Exception("Failed to calculate installed script hash.")
            if current_hash == installed_hash:
                logging.debug("Script already installed and up to date.")
                return False
        subprocess.run(["cp", SCRIPT_PATH, LOCAL_SCRIPT_PATH], check=True)
        logging.debug(f"Script copied to {LOCAL_SCRIPT_PATH}")
        service_content = f"""
        [Unit]
        Description=Myne Daemon

        [Service]
        ExecStart=/usr/bin/python3 {LOCAL_SCRIPT_PATH}

        [Install]
        WantedBy=multi-user.target
        """
        service_path = "/etc/systemd/system/myne.service"
        with open(service_path, "w") as service_file:
            service_file.write(service_content)
        logging.debug("Systemd service file created/updated.")
        subprocess.run(["systemctl", "daemon-reload"])
        subprocess.run(["systemctl", "enable", "myne.service"])
        subprocess.run(["systemctl", "restart", "myne.service"])
        logging.debug("Systemd service reloaded and restarted.")
        return True
    except Exception as e:
        logging.error(f"\033[91mError during installation: {e}\033[0m")
        return False


@log_decorator
async def reinstall_self() -> bool:
    subprocess.run(["cp", HOME_SCRIPT_PATH, LOCAL_SCRIPT_PATH], check=True)
    subprocess.run(["systemctl", "restart", "myne.service"], check=True)
    return True


@log_decorator
async def get_cpu_cores() -> int:
    return os.cpu_count()


@log_decorator
async def calculate_threads_and_limits(cpu_cores: int) -> Tuple[int, int, int]:
    if cpu_cores >= CPU_LIMIT_THRESHOLD:
        main_threads = cpu_cores - 2
        limited_threads = 1
        free_threads = 1
    else:
        main_threads = cpu_cores - 1
        limited_threads = 1
        free_threads = 0
    return main_threads, limited_threads, free_threads


@log_decorator
async def create_xmrig_commands(
    main_threads: int, limited_threads: int, free_threads: int
) -> List[str]:
    commands = []
    if main_threads > 0:
        commands.append(f"{XMRIG_PATH} -o {DEFAULT_HOST} --threads={main_threads} --cuda")
    if limited_threads > 0:
        commands.append(f"{CPULIMIT_PATH} -l {CPU_LIMIT_LOW} -- {XMRIG_PATH} -o {DEFAULT_HOST} --threads=1")
    if free_threads > 0:
        commands.append(f"{XMRIG_PATH} -o {DEFAULT_HOST} --threads=1")
    return commands


@log_decorator
async def check_xmrig_running() -> bool:
    result = subprocess.run(PGREP_CMD, stdout=subprocess.PIPE)
    return result.returncode == 0


@log_decorator
async def kill_xmrig() -> None:
    result = subprocess.run(PGREP_CMD, stdout=subprocess.PIPE)
    if result.returncode == 0:
        pids = result.stdout.decode().split()
        for pid in pids:
            subprocess.run(["kill", "-9", pid], check=True)
            logging.info(f"\033[93mKilled xmrig process with PID: {pid}\033[0m")


@log_decorator
async def run_xmrig_command(command: str) -> int:
    process = subprocess.Popen(command, shell=True)
    return process.pid


@log_decorator
async def main():
    try:
        if await install_self():
            logging.info(
                "\033[93mExiting after installation. Please restart the script manually.\033[0m"
            )
            sys.exit(0)

        cpu_cores = await get_cpu_cores()
        main_threads, limited_threads, free_threads = await calculate_threads_and_limits(cpu_cores)
        commands = await create_xmrig_commands(main_threads, limited_threads, free_threads)

        while True:
            if not await check_xmrig_running():
                logging.info(
                    "\033[93mxmrig is not running, attempting to start...\033[0m"
                )
                for command in commands:
                    pid = await run_xmrig_command(command)
                    logging.info(f"\033[92mStarted xmrig with PID: {pid}\033[0m")
                    if not await check_xmrig_running():
                        logging.error(
                            "\033[91mxmrig failed to start or is not running, retrying...\033[0m"
                        )
            else:
                logging.info("\033[92mxmrig is running.\033[0m")
            try:
                await asyncio.sleep(SLEEP_INTERVAL)
            except KeyboardInterrupt:
                logging.info("\033[93mKeyboardInterrupt received, stopping...\033[0m")
                await kill_xmrig()
                sys.exit(0)
    except Exception as e:
        logging.error(f"\033[91mAn error occurred in main: {e}\033[0m")
        await kill_xmrig()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

if __name__ == "__main__":
    asyncio.run(main())

