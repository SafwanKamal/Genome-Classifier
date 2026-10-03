"""Train/export the separate 16-256-256-1 batched-throughput experiment."""
import argparse
import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import nn
from sklearn.metrics import average_precision_score

from software.model.hardware_model import quantized_parameter, ste_round, relu_requantize
from software.model.train_model import set_seed, best_f1_threshold, classification_metrics, sha256


class BatchModel(nn.Module):
    def __init__(self, checkpoint):
        super().__init__()
        state = checkpoint['state_dict']
        self.weights = nn.ParameterList([
            nn.Parameter(state['weight1'].clone()),
            nn.Parameter(torch.empty(256, 256).uniform_(-1.5, 1.5)),
            nn.Parameter(state['weight2'].clone())])
        self.biases = nn.ParameterList([
            nn.Parameter(state['bias1'].clone()), nn.Parameter(torch.zeros(256)),
            nn.Parameter(state['bias2'].clone())])

    def scores(self, x):
        for i, (w, b) in enumerate(zip(self.weights, self.biases)):
            x = x @ quantized_parameter(w, -127, 127).T + ste_round(b)
            if i < 2:
                x = relu_requantize(x, (4, 6)[i])
        return x.squeeze(1)

    def forward(self, x):
        return self.scores(x) / 384


@torch.no_grad()
def predict(model, x):
    return np.concatenate([model.scores(torch.from_numpy(chunk)).numpy()
                           for chunk in np.array_split(x, max(1, (len(x)+4095)//4096))])


def pack_memories(layers, directory):
    directory.mkdir(parents=True, exist_ok=True)
    words, biases = [], []
    for layer in layers:
        w, b = np.array(layer['weights']), layer['biases']
        layer['weight_base'] = len(words)
        layer['bias_base'] = len(biases)
        for out in range(0, len(b), 32):
            bias_word = sum((int(b[out+j]) & 0xffffffff) << (32*j)
                            for j in range(32) if out+j < len(b))
            biases.append(f'{bias_word:0256x}')
            for inp in range(0, w.shape[1], 4):
                word = sum((int(w[out+j, inp+k]) & 255) << (8*(j*4+k))
                           for j in range(32) for k in range(4)
                           if out+j < len(b) and inp+k < w.shape[1])
                words.append(f'{word:0256x}')
    (directory/'batch_weights.mem').write_text('\n'.join(words)+'\n')
    (directory/'batch_biases.mem').write_text('\n'.join(biases)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('data/processed/variants_model_int8.parquet'))
    parser.add_argument('--warm-start', type=Path, default=Path('artifacts/model_v3_h256_seed_7/model_qat.pt'))
    parser.add_argument('--output-dir', type=Path, default=Path('artifacts/model_v4_batch_seed_7'))
    parser.add_argument('--epochs', type=int, default=80)
    parser.add_argument('--patience', type=int, default=12)
    args = parser.parse_args()
    torch.set_num_threads(4)
    set_seed(7)
    checkpoint = torch.load(args.warm_start, map_location='cpu', weights_only=False)
    order = checkpoint['feature_order']
    frame = pd.read_parquet(args.input)
    train, val = [frame.loc[frame['split'].eq(split)] for split in ('train', 'validation')]
    x = train[order].to_numpy(dtype=np.float32)
    y = train['label'].to_numpy(dtype=np.float32)
    vx = val[order].to_numpy(dtype=np.float32)
    vy = val['label'].to_numpy(dtype=np.int64)
    model = BatchModel(checkpoint)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor((len(y)-y.sum())/y.sum()))
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.02, weight_decay=1e-5)
    loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(
        torch.from_numpy(x), torch.from_numpy(y)), batch_size=4096, shuffle=True,
        generator=torch.Generator().manual_seed(7))
    initial_ap = average_precision_score(vy, predict(model, vx))
    best_ap = -float('inf')
    best_state, best_epoch, stale, history = None, 0, 0, []
    print(f'Initialized deeper model validation AP={initial_ap:.6f}', flush=True)
    for epoch in range(1, args.epochs+1):
        model.train()
        loss_sum = 0
        for features, labels in loader:
            optimizer.zero_grad()
            loss = loss_fn(model(features), labels)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 10)
            optimizer.step()
            with torch.no_grad():
                for w in model.weights:
                    w.clamp_(-127, 127)
            loss_sum += float(loss.detach()) * len(features)
        model.eval()
        ap = float(average_precision_score(vy, predict(model, vx)))
        history.append({'epoch': epoch, 'loss': loss_sum/len(x), 'validation_ap': ap})
        print(f'epoch={epoch:03d} loss={loss_sum/len(x):.6f} val_ap={ap:.6f}', flush=True)
        if ap > best_ap:
            best_ap, best_state, best_epoch, stale = ap, copy.deepcopy(model.state_dict()), epoch, 0
        else:
            stale += 1
        if stale >= args.patience:
            break
    model.load_state_dict(best_state)
    scores = predict(model, vx)
    threshold = best_f1_threshold(vy, scores)
    # Freeze a routing threshold with >=99.5% pathogenic validation recall.
    folded = scores.astype(np.int64) - threshold
    positive = np.sort(folded[vy == 1])
    routing = int(positive[int(np.floor(0.005 * len(positive)))])
    layers = [{'weights': torch.round(w).to(torch.int64).detach().numpy().tolist(),
               'biases': torch.round(b).to(torch.int64).detach().numpy().tolist(),
               'qshift': (4, 6, 0)[i]}
              for i, (w, b) in enumerate(zip(model.weights, model.biases))]
    layers[-1]['biases'][0] -= threshold
    directory = args.output_dir
    directory.mkdir(parents=True, exist_ok=True)
    pack_memories(layers, directory/'memory')
    manifest = {'architecture': '16-256-256-1', 'feature_order': order, 'layers': layers,
                'classification_threshold': threshold, 'routing_threshold': routing,
                'input_lanes': 4, 'output_lanes': 32,
                'dataset_sha256': sha256(args.input), 'warm_start_sha256': sha256(args.warm_start),
                'seed': 7, 'best_epoch': best_epoch, 'epochs_completed': len(history),
                'selection_split': 'validation',
                'validation': classification_metrics(vy, scores, threshold),
                'validation_routing_recall': float((folded[vy==1] >= routing).mean())}
    (directory/'export_manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    (directory/'training_history.json').write_text(json.dumps(history, indent=2)+'\n')
    torch.save({'state_dict': best_state, 'feature_order': order}, directory/'model_qat.pt')
    print(json.dumps({k: v for k, v in manifest.items() if k != 'layers'}, indent=2), flush=True)


if __name__ == '__main__':
    main()
