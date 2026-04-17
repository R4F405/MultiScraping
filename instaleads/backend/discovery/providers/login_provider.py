from backend.discovery.base import DiscoveryProvider
from backend.discovery.models import ProfileCandidate


class LoginProvider(DiscoveryProvider):
    @property
    def source_name(self) -> str:
        return "login_pool"

    async def find_profiles(self, target: str, max_results: int) -> list[ProfileCandidate]:
        seed = "".join(ch for ch in target.lower() if ch.isalnum())[:10] or "growth"
        size = max(5, min(max_results, 90))
        profiles: list[ProfileCandidate] = []
        for idx in range(size):
            username = f"{seed}_biz_{idx:03d}"
            private = (idx % 8) == 0
            profiles.append(
                ProfileCandidate(
                    username=username,
                    full_name=f"{target.title()} Account {idx}",
                    biography=f"Cuenta business {target} | collaborations",
                    bio_url=f"https://{seed}-biz-{idx:03d}.example.com",
                    follower_count=1000 + (idx * 31),
                    is_private=private,
                    source=self.source_name,
                )
            )
        return profiles
