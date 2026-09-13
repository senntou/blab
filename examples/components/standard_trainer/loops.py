def train_one_epoch(model, dataset):
    total = 0.0
    for x, y in dataset:
        total += model.update(x, y)
    return total / max(1, len(dataset))


def evaluate(model, dataset, metric):
    return metric(model, dataset)
