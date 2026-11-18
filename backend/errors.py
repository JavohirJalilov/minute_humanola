class AppError(Exception):
    """A refusal the user should see. `message` is English and doubles as the translation key in the
    Web App and the bot; `{name}` placeholders are filled from `params`."""

    def __init__(self, code: str, message: str, status: int = 400, **params):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.params = params

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, "params": self.params}

    def text(self) -> str:
        try:
            return self.message.format(**self.params)
        except (KeyError, IndexError):
            return self.message


def forbidden(message: str = "You don't have access to this", code: str = "forbidden") -> AppError:
    return AppError(code, message, 403)


def not_found(message: str = "Not found", code: str = "not_found") -> AppError:
    return AppError(code, message, 404)
