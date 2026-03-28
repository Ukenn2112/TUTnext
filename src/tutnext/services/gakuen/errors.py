# tutnext/services/gakuen/errors.py
# Custom exception hierarchy for the Gakuen API client.
from typing import Optional


class GakuenAPIError(Exception):
    """GakuenAPI専用例外クラス"""

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        status_code: Optional[int] = None,
        response_data: Optional[dict] = None,
    ):
        """
        Args:
            message: エラーメッセージ
            error_code: エラーコード（プログラム処理用）
            status_code: HTTPステータスコード
            response_data: 元のレスポンスデータ
        """
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.response_data = response_data

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return f"GakuenAPIError(message='{self.message}', error_code='{self.error_code}', status_code={self.status_code})"


class GakuenLoginError(GakuenAPIError):
    """ログイン関連エラー"""

    pass


class GakuenNetworkError(GakuenAPIError):
    """ネットワーク関連エラー"""

    pass


class GakuenDataError(GakuenAPIError):
    """データ解析関連エラー"""

    pass


class GakuenPermissionError(GakuenAPIError):
    """権限関連エラー"""

    pass
