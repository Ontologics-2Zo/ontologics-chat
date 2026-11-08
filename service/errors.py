"""Public-safe service errors. Details must never contain credentials or raw sources."""


class ServiceError(Exception):
    def __init__(self, code: str, status_code: int = 409, details: dict | None = None):
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(code)

    def as_dict(self) -> dict:
        return {"code": self.code, "details": self.details}
