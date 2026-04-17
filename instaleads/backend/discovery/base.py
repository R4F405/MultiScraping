from abc import ABC, abstractmethod

from backend.discovery.models import ProfileCandidate


class DiscoveryProvider(ABC):
    @abstractmethod
    async def find_profiles(self, target: str, max_results: int) -> list[ProfileCandidate]:
        raise NotImplementedError

    @property
    @abstractmethod
    def source_name(self) -> str:
        raise NotImplementedError
