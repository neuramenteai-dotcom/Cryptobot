import time
import functools

def retry_with_backoff(max_retries=3, initial_delay=2, backoff_factor=2):
    """
    Decorator for exponential backoff.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    print(f"[RETRY] {func.__name__} failed (attempt {attempt+1}/{max_retries}): {e}")
                    if attempt == max_retries - 1:
                        print(f"[RETRY] Max retries reached for {func.__name__}.")
                        raise
                    time.sleep(delay)
                    delay *= backoff_factor
            return None
        return wrapper
    return decorator
