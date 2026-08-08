import os


def _icon_theme_available(theme, search_path_dirs):
    return any(
        os.path.isdir(os.path.join(directory, theme))
        for directory in search_path_dirs
    )
