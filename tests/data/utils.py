from uuid import uuid4

from faker.providers import BaseProvider

from faker import Faker

__all__ = ("Fake",)

Fake = Faker("fr_FR")


class UniqueIdProvider(BaseProvider):
    """
    Provider for generating unique integer IDs.
    """

    def __init__(self, generator, start_id: int = 1_000):
        super().__init__(generator)
        self.current_id = start_id

    def id(self) -> int:
        """
        Generate a unique sequential integer ID.
        """
        result = self.current_id
        self.current_id += 1
        return result


class UtilsProvider(BaseProvider):
    base_email = "@polarsen.fr"

    def gen_email(self):
        return f"test+{str(uuid4())[-10:]}{self.base_email}"


Fake.add_provider(UniqueIdProvider)
Fake.add_provider(UtilsProvider)
