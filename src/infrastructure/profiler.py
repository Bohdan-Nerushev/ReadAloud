"""
Profiling utilities for performance monitoring.

This module provides decorators and functions to measure execution time
and system resource usage of critical components.
"""

import cProfile
import pstats
import logging
import time
from functools import wraps
from typing import Callable, Any


def profile_method(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorator to profile method execution using cProfile.
    
    The results are logged to the console/log file showing the top 20
    most time-consuming function calls during the method's execution.
    
    Args:
        func: The function or method to profile
        
    Returns:
        Wrapped function that performs profiling
    """
    @wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        profiler = cProfile.Profile()
        
        logging.info(f"--- Starting profile for: {func.__name__} ---")
        start_time = time.perf_counter()
        
        try:
            profiler.enable()
            result = func(*args, **kwargs)
            profiler.disable()
        except Exception as e:
            profiler.disable()
            logging.error(f"Error during profiling {func.__name__}: {e}")
            raise
        finally:
            end_time = time.perf_counter()
            duration = end_time - start_time
            
            # Formating stats
            stats = pstats.Stats(profiler)
            stats.sort_stats('cumulative')
            
            logging.info(f"--- Profile result for: {func.__name__} (Duration: {duration:.4f}s) ---")
            # We use stream to capture the output of print_stats for logging
            import io
            s = io.StringIO()
            ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
            ps.print_stats(20)
            logging.debug(s.getvalue())
            
            # Always print top 10 to info for visibility
            ps.print_stats(10)
            
        return result
        
    return wrapper


def time_execution(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Simple decorator to measure and log execution time of a function.
    
    Args:
        func: The function to measure
        
    Returns:
        Wrapped function
    """
    @wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        
        logging.info(f"Execution of {func.__name__} took {end_time - start_time:.4f} seconds")
        return result
        
    return wrapper
