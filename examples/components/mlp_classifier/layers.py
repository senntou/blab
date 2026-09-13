"""component の中は自由。相対 import でヘルパを分けられる。"""


class Dense:
    def __init__(self, n_in, n_out, rng):
        scale = (1.0 / n_in) ** 0.5
        self.w = [[rng.uniform(-scale, scale) for _ in range(n_in)] for _ in range(n_out)]
        self.b = [0.0] * n_out

    def forward(self, x):
        return [sum(wi * xi for wi, xi in zip(w, x)) + b for w, b in zip(self.w, self.b)]

    def backward(self, x, grad, lr):
        back = [0.0] * len(x)
        for c, g in enumerate(grad):
            for j in range(len(x)):
                back[j] += self.w[c][j] * g
                self.w[c][j] -= lr * g * x[j]
            self.b[c] -= lr * g
        return back
