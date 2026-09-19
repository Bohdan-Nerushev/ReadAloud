import shutil
import logging
import subprocess
from typing import List, Tuple

def check_dependencies() -> Tuple[bool, List[str]]:
    """
    Checks if required system dependencies are available.
    
    Returns:
        Tuple containing:
        - bool: True if all dependencies are found, False otherwise
        - List[str]: List of missing dependencies
    """
    dependencies = ["ffmpeg", "ffprobe"]
    missing = []
    
    for dep in dependencies:
        if shutil.which(dep) is None:
            missing.append(dep)
            logging.error(f"System dependency missing: {dep}")
        else:
            logging.debug(f"System dependency found: {dep}")
            
    return len(missing) == 0, missing


def check_docker() -> Tuple[bool, str]:
    """
    Checks if Docker is available and the daemon is responsive.

    Returns:
        Tuple of (available: bool, error_message: str).
        error_message is an empty string when available=True.
    """
    if shutil.which("docker") is None:
        return False, "Docker binary not found in PATH. Install Docker Engine."

    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            logging.debug("Docker daemon available, server version: %s", result.stdout.strip())
            return True, ""
        return False, f"Docker daemon not responding: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "Docker daemon did not respond within 10 seconds."
    except (FileNotFoundError, OSError) as exc:
        return False, f"Docker check failed: {exc}"
