import shutil
import logging
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
