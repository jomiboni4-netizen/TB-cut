"""Load a local compute model only on a cache miss."""
class LazyModel:
    def __init__(self, factory):
        self.factory = factory
        self.model = None

    def get(self):
        if self.model is None:
            self.model = self.factory()
        return self.model
