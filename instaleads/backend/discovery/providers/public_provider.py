from backend.discovery.base import DiscoveryProvider
from backend.discovery.models import ProfileCandidate


class PublicDiscoveryProvider(DiscoveryProvider):
    @property
    def source_name(self) -> str:
        return "public"

    async def find_profiles(self, target: str, max_results: int) -> list[ProfileCandidate]:
        seed = "".join(ch for ch in target.lower() if ch.isalnum())[:12] or "lead"
        size = max(3, min(max_results, 40))
        profiles: list[ProfileCandidate] = []
        for idx in range(size):
            username = f"{seed}{idx:02d}"
            profiles.append(
                ProfileCandidate(
                    username=username,
                    full_name=f"{target.title()} {idx}",
                    biography=f"Negocio local de {target}. Contacto por email.",
                    bio_url=f"https://{seed}{idx:02d}.example.com",
                    follower_count=200 + (idx * 17),
                    is_private=False,
                    source=self.source_name,
                )
            )
        return profiles
