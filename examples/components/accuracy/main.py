"""正解率。"""

import blab


@blab.entry
class Accuracy:
    def __call__(self, model, dataset):
        hits = sum(1 for x, y in dataset if model.predict(x) == y)
        return hits / max(1, len(dataset))
