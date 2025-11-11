import os
from pathlib import Path


def get_xdg_user_dir(dir_name: str) -> Path:
    """
    Finds a special XDG user directory (like DESKTOP, DOCUMENTS)
    in a language-independent way on Linux by reading the
    ~/.config/user-dirs.dirs file.

    Args:
        dir_name: The internal name of the directory (e.g., "DESKTOP",
                  "DOCUMENTS", "DOWNLOAD").
    """

    # 1. The key we are looking for in the file
    key_to_find = f"XDG_{dir_name.upper()}_DIR"

    # 2. Determine the config file path
    # It's almost always in ~/.config/user-dirs.dirs
    config_home = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    config_file_path = Path(config_home) / "user-dirs.dirs"

    # 3. Set a sensible fallback (e.g., $HOME/Desktop)
    # This is used if the file or key doesn't exist
    fallback_dir = Path.home() / dir_name.capitalize()

    if not config_file_path.is_file():
        return fallback_dir

    try:
        with open(config_file_path, "r") as f:
            for line in f:
                line = line.strip()

                # Skip comments or empty lines
                if not line or line.startswith("#"):
                    continue

                # Check if this is the line we want
                if line.startswith(key_to_find):
                    try:
                        # Line looks like: XDG_DESKTOP_DIR="$HOME/Bureaublad"
                        # Split at '=', get the second part
                        value = line.split("=", 1)[1]

                        # Remove surrounding quotes (e.g., "...")
                        value = value.strip('"')

                        # IMPORTANT: Expand variables like $HOME
                        path = os.path.expandvars(value)

                        return Path(path)

                    except Exception:
                        # Found the key but the line was malformed, use fallback
                        return fallback_dir

    except Exception as e:
        print(f"Error reading {config_file_path}: {e}")
        # Fallback in case of permissions errors, etc.
        return fallback_dir

    # If the key (e.g., XDG_DESKTOP_DIR) wasn't found in the file
    return fallback_dir
