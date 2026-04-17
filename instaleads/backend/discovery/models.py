from dataclasses import asdict, dataclass


@dataclass
class ProfileCandidate:
    username: str
    full_name: str = ""
    biography: str = ""
    bio_url: str = ""
    follower_count: int = 0
    is_private: bool = False
    source: str = "internal"

    def to_dict(self) -> dict:
        return asdict(self)
