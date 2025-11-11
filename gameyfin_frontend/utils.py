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
    key_to_find = f"XDG_{dir_name.upper()}_DIR"

    config_home = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    config_file_path = Path(config_home) / "user-dirs.dirs"

    # Set a sensible fallback (e.g., $HOME/Desktop)
    fallback_dir = Path.home() / dir_name.capitalize()

    if not config_file_path.is_file():
        return fallback_dir

    try:
        with open(config_file_path, "r") as f:
            for line in f:
                line = line.strip()

                if not line or line.startswith("#"):
                    continue

                if line.startswith(key_to_find):
                    try:
                        # Line looks like: XDG_DESKTOP_DIR="$HOME/Desktop"
                        value = line.split("=", 1)[1]
                        value = value.strip('"')

                        # Expand variables like $HOME
                        path = os.path.expandvars(value)

                        return Path(path)

                    except Exception:
                        # Found the key but the line was malformed
                        return fallback_dir

    except Exception as e:
        print(f"Error reading {config_file_path}: {e}")
        return fallback_dir

    # Key wasn't found in the file
    return fallback_dir