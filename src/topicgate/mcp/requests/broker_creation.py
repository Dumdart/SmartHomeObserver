from pydantic import Field, field_validator

from topicgate.mcp.requests.expectation_requests import RequestModel


class CreateBrokerRequest(RequestModel):
    name: str = Field(min_length=1, max_length=200)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=1883, ge=1, le=65535, strict=True)
    username: str = Field(default="", max_length=256)
    use_tls: bool = Field(default=False, strict=True)

    @field_validator("name", "host")
    @classmethod
    def normalized_identity(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError(
                "Broker name and host must be nonblank without control characters."
            )
        return value

    @field_validator("host")
    @classmethod
    def hostname(cls, value: str) -> str:
        if any(character.isspace() for character in value) or any(
            c in value for c in "/@?#"
        ):
            raise ValueError(
                "Supply a hostname or IP address, not a URL or credentials."
            )
        return value.casefold()
