"""Reference for the separate three-layer INT8 throughput experiment."""
import json
from pathlib import Path

import numpy as np


class BatchModelReference:
    def __init__(self, manifest):
        self.manifest = json.loads(Path(manifest).read_text())
        if self.manifest['architecture'] != '16-256-256-1':
            raise ValueError('Expected the three-layer batch model')
        self.layers = [(np.array(layer['weights'], dtype=np.int64),
                        np.array(layer['biases'], dtype=np.int64), layer['qshift'])
                       for layer in self.manifest['layers']]
        self.float_layers = [(w.astype(np.float32), b.astype(np.float32), shift)
                             for w, b, shift in self.layers]

    def score(self, features, dtype=np.int64):
        x = np.asarray(features, dtype=dtype)
        layers = self.float_layers if dtype == np.float32 else self.layers
        for w, b, shift in layers:
            x = x @ w.T + b
            if shift:
                if np.issubdtype(dtype, np.integer):
                    x = (x + (1 << (shift-1))) >> shift
                else:
                    x = np.floor((x + (1 << (shift-1))) / (1 << shift))
                x = np.clip(x, 0, 127)
        return x[:, 0].astype(np.int64)
