"""閲覧層のサーバ（読み取り専用）。"""

from .app import create_app, serve

__all__ = ["create_app", "serve"]
