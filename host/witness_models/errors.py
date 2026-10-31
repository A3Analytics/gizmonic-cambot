"""Statuses reported for a model call. Secrets never go in the message."""


class ModelCallError(Exception):
    def __init__(self, status: str):
        self.status = status
        super().__init__(status)


class InvalidJson(Exception):
    """The HTTP call succeeded but the body was not the expected JSON."""


class TlsFailure(Exception):
    pass
